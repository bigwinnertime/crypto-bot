import os
import sys

# #22: 编码环境设置——必须在 logging.basicConfig 之前执行（下方 StreamHandler 用 sys.stdout，
# 若 stdout 编码未先 reconfigure 为 utf-8，中文日志在无 locale 的环境会 UnicodeEncodeError：
# 某些 Docker 容器 / cron 调度默认 C locale）。故不能移到 if __name__ 块，也不能删。
# 顺序约束：本块 → imports → logging.basicConfig(StreamHandler(sys.stdout))。
os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['PYTHONIOENCODING'] = 'utf-8'

if hasattr(sys, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# .env 由 config.py 统一加载（带绝对路径），这里不再重复 load_dotenv()（#23）

import ccxt
import copy
import pandas as pd
import time
import logging
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

import threading
from remote_control import start_remote_listener, init_remote_control

import config
from risk_manager import RiskManager
from telegram_notifier import send_notification

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot_main.log", encoding='utf-8', errors='replace'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TradingBot.Main")


class AdvancedTradingBot:
    def __init__(self):
        raw_key = os.getenv('BINANCE_SECRET_KEY', '')
        formatted_key = raw_key.replace('\\n', '\n').strip('"').strip("'")

        if not formatted_key.startswith('-----BEGIN PRIVATE KEY-----'):
            formatted_key = f"-----BEGIN PRIVATE KEY-----\n{formatted_key}"
        if not formatted_key.endswith('-----END PRIVATE KEY-----'):
            formatted_key = f"{formatted_key}\n-----END PRIVATE KEY-----"

        self.exchange = ccxt.binance({
            'apiKey': config.API_KEY,
            'secret': formatted_key,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot', 'secretType': 'ed25519'}
        })
        self.risk = RiskManager(
            max_exposure=config.MAX_TOTAL_EXPOSURE,
            fuse_limit=config.DRAWDOWN_FUSE
        )

        # 信号二次确认机制：连续2轮触发同方向信号才执行，过滤假突破
        # 格式: { 'BTC/USDT': {'signal': 'BUY', 'mode': 'TREND_MACD_GOLDEN_CROSS', 'count': 1} }
        self.pending_signals = {}

        init_remote_control(self.risk)
        self.cmd_thread = threading.Thread(target=start_remote_listener, daemon=True)
        self.cmd_thread.start()

    # ═══════════════════════════════════════════════════
    #  数据获取
    # ═══════════════════════════════════════════════════

    def fetch_data(self, symbol, timeframe=None, limit=100):
        try:
            clean_symbol = symbol.strip()
            tf = timeframe or config.TIMEFRAME
            bars = self.exchange.fetch_ohlcv(clean_symbol, timeframe=tf, limit=limit)
            df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            logger.error(f"获取 {symbol} ({timeframe or config.TIMEFRAME}) 数据异常: {str(e)}")
            return pd.DataFrame()

    # ═══════════════════════════════════════════════════
    #  多时间框架趋势过滤
    # ═══════════════════════════════════════════════════

    def _check_higher_tf_trend(self, symbol):
        """
        检查高级时间框架趋势方向。
        返回: 1 (上升趋势), -1 (下降趋势), 0 (震荡)
        #13 修复：数据获取失败或指标为 NaN 时返回 -1（fail-safe，抑制买入），
               而非此前的 0（震荡，允许买入）——避免在 HTF 真实下跌时因 API 失败而误买入。
        """
        htf = getattr(config, 'HIGHER_TIMEFRAME', '4h')
        df_htf = self.fetch_data(symbol, timeframe=htf, limit=60)
        if df_htf.empty or len(df_htf) < 60:
            logger.warning(f"{symbol} HTF({htf}) 数据不足，fail-safe 视为下跌趋势抑制买入")
            return -1

        close = df_htf['close']
        high = df_htf['high']
        low = df_htf['low']
        price = close.iloc[-1]

        sma20_htf = SMAIndicator(close, window=20).sma_indicator().iloc[-1]
        sma60_htf = SMAIndicator(close, window=60).sma_indicator().iloc[-1]
        adx_htf = ADXIndicator(high, low, close).adx().iloc[-1]

        # 指标 NaN 视为数据不足，fail-safe
        if pd.isna(sma20_htf) or pd.isna(sma60_htf) or pd.isna(adx_htf):
            logger.warning(f"{symbol} HTF 指标含 NaN，fail-safe 视为下跌趋势抑制买入")
            return -1

        # 上升趋势: SMA20 > SMA60 + 价格 > SMA20 + ADX > 20
        if sma20_htf > sma60_htf and price > sma20_htf and adx_htf > 20:
            return 1
        # 下降趋势: SMA20 < SMA60 + 价格 < SMA20 + ADX > 20
        if sma20_htf < sma60_htf and price < sma20_htf and adx_htf > 20:
            return -1

        return 0

    # ═══════════════════════════════════════════════════
    #  市场状态识别 & 信号生成
    # ═══════════════════════════════════════════════════

    def _detect_regime(self, adx, bb_upper, bb_lower, bb_mid, price, spec):
        """
        市场状态识别：根据 ADX 强度 + 布林带宽度判断当前为趋势态/震荡态/中性。
        返回: 'TREND' | 'RANGE' | 'NEUTRAL'
        """
        bb_width = (bb_upper - bb_lower) / price if price > 0 else 0
        trend_adx = spec.get('regime_trend_adx', 25)
        range_adx = spec.get('regime_range_adx', 20)
        trend_bb = spec.get('regime_trend_bb_width', 0.03)
        range_bb = spec.get('regime_range_bb_width', 0.02)

        if adx >= trend_adx and bb_width >= trend_bb:
            return 'TREND'
        if adx <= range_adx and bb_width <= range_bb:
            return 'RANGE'
        return 'NEUTRAL'

    def get_strategy_signal(self, df, symbol):
        """策略信号生成器 v5 — Regime 自适应（趋势态用趋势信号，震荡态用均值回归信号）"""
        if len(df) < 60:
            logger.warning(f"{symbol} 数据不足（{len(df)}根），跳过信号判定")
            return "HOLD", "INSUFFICIENT_DATA", None

        spec = self.risk.get_effective_config(symbol)

        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        price = close.iloc[-1]

        adx_val = ADXIndicator(high, low, close).adx().iloc[-1]
        rsi_series = RSIIndicator(close).rsi()
        rsi_val = rsi_series.iloc[-1]
        rsi_prev = rsi_series.iloc[-2]
        rsi_3_ago = rsi_series.iloc[-4]

        sma20 = SMAIndicator(close, window=20).sma_indicator().iloc[-1]
        sma60 = SMAIndicator(close, window=60).sma_indicator().iloc[-1]

        bb = BollingerBands(close,
                            window=spec.get('bb_period', 20),
                            window_dev=spec.get('bb_std', 2))
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_mid = bb.bollinger_mavg().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]

        atr = AverageTrueRange(high, low, close,
                               window=spec.get('atr_period', 14)
                               ).average_true_range().iloc[-1]
        atr_pct = atr / price

        macd_ind = MACD(close)
        macd_line = macd_ind.macd().iloc[-1]
        macd_sig = macd_ind.macd_signal().iloc[-1]
        macd_prev = macd_ind.macd().iloc[-2]

        vol_ma = volume.rolling(spec.get('volume_ma_period', 20)).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 0

        # K线形态分析：阳线判定 + 实体占比（过滤十字星、影线过长的弱信号K线）
        candle_body = close.iloc[-1] - df['open'].iloc[-1]
        candle_range = high.iloc[-1] - low.iloc[-1]
        is_green_candle = candle_body > 0
        candle_body_ratio = abs(candle_body) / candle_range if candle_range > 0 else 0

        # 波动率自适应参数
        adjusted = self._adjust_params_by_volatility(spec, atr_pct)

        # 市场状态识别
        regime = self._detect_regime(adx_val, bb_upper, bb_lower, bb_mid, price, spec)

        # 卖出信号（持仓时优先判断，根据持仓策略类型走不同退出逻辑）
        sell_reason = self._should_sell(
            symbol, price, adx_val, rsi_val, macd_line, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, atr, spec, adjusted
        )
        if sell_reason:
            return "SELL", sell_reason, None

        # 买入信号（根据市场状态选择策略类型）
        buy_reason, strategy_type = self._should_buy(
            price, adx_val, rsi_val, rsi_prev, rsi_3_ago, sma20, sma60,
            macd_line, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
            is_green_candle, candle_body_ratio, regime
        )
        if buy_reason:
            return "BUY", buy_reason, strategy_type

        regime_label = {"TREND": "趋势", "RANGE": "震荡", "NEUTRAL": "中性"}.get(regime, regime)
        logger.debug(f"{symbol} 【{regime_label}】 ADX={adx_val:.1f} RSI={rsi_val:.1f} → 持币观望")
        return "HOLD", "NONE", None

    def _adjust_params_by_volatility(self, spec, atr_pct):
        """根据波动率动态调整参数"""
        vol_adjust = spec.get('volatility_adjust', {})
        if not vol_adjust.get('enabled', False):
            return spec

        low_vol_threshold = vol_adjust.get('low_vol_threshold', 0.02)
        high_vol_threshold = vol_adjust.get('high_vol_threshold', 0.05)
        low_vol_multiplier = vol_adjust.get('low_vol_multiplier', 0.8)
        high_vol_multiplier = vol_adjust.get('high_vol_multiplier', 1.2)

        adjusted = copy.deepcopy(spec)

        if atr_pct < low_vol_threshold:
            multiplier = low_vol_multiplier
        elif atr_pct > high_vol_threshold:
            multiplier = high_vol_multiplier
        else:
            return spec

        if 'adx_threshold' in adjusted:
            adjusted['adx_threshold'] = spec['adx_threshold'] * multiplier
        if 'rsi_oversold' in adjusted:
            adjusted['rsi_oversold'] = spec['rsi_oversold'] * multiplier
        if 'rsi_overbought' in adjusted:
            adjusted['rsi_overbought'] = spec['rsi_overbought'] / multiplier

        return adjusted

    # ═══════════════════════════════════════════════════
    #  卖出信号（统一决策树）
    # ═══════════════════════════════════════════════════

    def _should_sell(self, symbol, price, adx, rsi, macd, macd_sig, macd_prev,
                     bb_lower, bb_mid, bb_upper, atr, spec, adjusted):
        """
        统一卖出决策树，根据持仓策略类型走不同退出逻辑：
          trend   → ATR追踪止损/固定止损/主动止盈/RSI超买/MACD死叉（让利润奔跑）
          meanrev → 紧止损/布林中轨止盈/RSI回升退出/超时退出（快进快出）
        """
        pos = self.risk.state['positions'].get(symbol)
        if not pos:
            return None

        entry_price = pos['entry_price']
        highest_price = pos.get('highest_price', entry_price)
        strategy_type = pos.get('strategy_type', 'trend')
        profit_pct = (price - entry_price) / entry_price

        # ═══ 均值回归退出逻辑（快进快出） ═══
        if strategy_type == 'meanrev':
            mr_cfg = spec.get('meanrev_config', {})
            mr_stop = mr_cfg.get('stop_loss_pct', 0.025)
            mr_rsi_exit = mr_cfg.get('rsi_exit', 50)
            mr_bb_mid_exit = mr_cfg.get('bb_mid_exit', True)
            mr_max_hold = mr_cfg.get('max_hold_hours', 24)

            # 1. 紧止损
            hard_stop = entry_price * (1 - mr_stop)
            if price <= hard_stop:
                return f"均值回归止损 (价{price:.2f}≤线{hard_stop:.2f})"

            # 2. 触及布林中轨止盈
            if mr_bb_mid_exit and price >= bb_mid and profit_pct > 0:
                return f"均值回归止盈 (触及布林中轨 {bb_mid:.2f})"

            # 3. RSI 回升退出
            if rsi >= mr_rsi_exit and profit_pct > 0:
                return f"均值回归RSI退出 (RSI={rsi:.1f}≥{mr_rsi_exit})"

            # 4. 超时强制退出
            holding_hours = self.risk._get_holding_hours(pos)
            if holding_hours >= mr_max_hold:
                return f"均值回归超时退出 (持仓{holding_hours:.1f}h≥{mr_max_hold}h)"

            return None

        # ═══ 趋势跟踪退出逻辑（让利润奔跑） ═══
        atr_multi = spec.get('atr_multiplier', 2.0)
        profit_in_atr = (price - entry_price) / atr if atr > 0 else 0
        min_profit_pct = spec.get('min_profit_pct', 0.008)

        # 1. ATR 追踪止损（无条件触发，止损优先）
        # 注意：atr<=0 时跳过此检查，避免 trail_stop=highest_price 导致刚买入就误触发
        if atr > 0:
            trail_stop = highest_price - atr_multi * atr
            if price <= trail_stop:
                return f"ATR追踪止损 (价{price:.2f}≤线{trail_stop:.2f})"

        # 2. 固定止损（无条件触发）
        hard_stop = entry_price * (1 - spec.get('stop_loss_pct', 0.04))
        if price <= hard_stop:
            return f"固定止损 (价{price:.2f}≤线{hard_stop:.2f})"

        # 3. 主动止盈：盈利达到 N×ATR 时锁定利润
        profit_target = spec.get('profit_target_atr', 6.0)
        if profit_in_atr >= profit_target:
            return f"主动止盈 (盈利{profit_in_atr:.1f}×ATR ≥ {profit_target:.0f}×ATR)"

        # === 以下为主动卖出信号，仅在盈利足够时触发（最小盈利保护）===

        # 4. RSI 超买（盈利 > 5% 且超过最小盈利保护时触发）
        # #20: 用 .get() 加 fallback，避免 adjusted 缺字段时 KeyError
        rsi_overbought_thr = adjusted.get('rsi_overbought', 70)
        if profit_pct > 0.05 and profit_pct > min_profit_pct and rsi > rsi_overbought_thr:
            return f"RSI超买预警 (RSI={rsi:.1f}>阈值{rsi_overbought_thr:.0f})"

        # 5. MACD 死叉 + ADX 回落（放宽条件，强趋势反转也能退出）
        strong_trend_threshold = adjusted.get('adx_threshold', 25) * 1.5
        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        if macd_dead and profit_pct > min_profit_pct and adx < strong_trend_threshold:
            return f"MACD死叉+ADX回落 (ADX={adx:.1f}<{strong_trend_threshold:.1f})"

        return None

    # ═══════════════════════════════════════════════════
    #  买入信号（四套逻辑 OR 叠加）
    # ═══════════════════════════════════════════════════

    def _should_buy(self, price, adx, rsi, rsi_prev, rsi_3_ago,
                    sma20, sma60,
                    macd, macd_sig, macd_prev,
                    bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
                    is_green_candle=True, candle_body_ratio=0.0, regime='NEUTRAL'):
        """
        Regime 自适应入场：
          TREND  → 趋势跟随信号（MACD金叉/ADX量能/RSI上穿50/SMA20突破）
          RANGE  → 均值回归信号（布林下轨+RSI底背离）
          NEUTRAL → 观望
        返回: (buy_reason, strategy_type) 或 (None, None)
        """
        vol_thr = spec.get('volume_threshold', 1.5)
        macd_golden = (macd_prev < macd_sig and macd > macd_sig)
        macd_above_zero = macd > -abs(macd_sig) * 0.5
        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        # 阳线实体占比阈值（过滤十字星/弱信号K线）
        min_body_ratio = 0.30
        quality_candle = candle_body_ratio > min_body_ratio
        # #12: 以下阈值改用 spec.get()，避免改配置不生效
        adx_thr = adjusted.get('adx_threshold', 25)
        rsi_oversold_thr = spec.get('rsi_oversold', 35)
        range_bb_width_thr = spec.get('regime_range_bb_width', 0.02)

        if regime == 'TREND':
            # ── 趋势跟随信号 ──
            if adx > adx_thr * 0.8:
                # A1. MACD 金叉 + 量能 + 零轴过滤
                if macd_golden and macd_above_zero and vol_ratio >= vol_thr and is_green_candle and quality_candle:
                    return "TREND_MACD_GOLDEN_CROSS", 'trend'
                # A2. ADX + 量能确认
                if (adx > adx_thr and
                        vol_ratio >= vol_thr and
                        price > sma20 and rsi > 50 and is_green_candle and quality_candle):
                    return "TREND_ADX_VOL_CONFIRM", 'trend'

            # B. RSI 上穿50 + 趋势过滤
            rsi_cross_50 = (rsi_prev < 50 <= rsi)
            trend_ok = (price > sma60 or adx > adx_thr)
            if rsi_cross_50 and trend_ok and price > bb_mid and vol_ratio >= vol_thr * 0.8:
                return "TREND_RSI_50_CROSS", 'trend'

            # D. SMA20 突破 + 阳线质量
            if (price > sma20 and rsi > 55 and
                    vol_ratio >= vol_thr and not macd_dead and is_green_candle and quality_candle):
                return "TREND_SMA20_BREAKOUT", 'trend'

        elif regime == 'RANGE':
            # ── 均值回归信号 ──
            # C. 布林下轨支撑 + RSI 底背离
            touch_lower = price <= bb_lower * 1.005
            bb_width = (bb_upper - bb_lower) / price
            bb_sufficient = bb_width > range_bb_width_thr
            rsi_bouncing = (rsi_3_ago is not None and rsi > rsi_prev > rsi_3_ago and rsi < rsi_oversold_thr)
            if touch_lower and bb_sufficient and rsi_bouncing and vol_ratio >= vol_thr * 0.8:
                return "MEANREV_BB_LOWER_RSI_DIVERGENCE", 'meanrev'

        # NEUTRAL: 观望
        return None, None

    # ═══════════════════════════════════════════════════
    #  波动率自适应仓位计算
    # ═══════════════════════════════════════════════════

    def _calc_position_size(self, symbol, price, atr, total_usdt):
        spec = self.risk.get_effective_config(symbol)
        risk_per_trade = spec.get('risk_per_trade', 0.01)
        max_trade_amount = spec.get('max_trade_amount', spec.get('trade_amount', 20))
        atr_multiplier = spec.get('atr_multiplier', 2.0)
        fallback_amount = spec.get('trade_amount', 20)

        # #19: atr<=0 不拦截 NaN（NaN<=0 为 False），NaN 会一路传到 min/max 让下单失败。
        #      用 pd.isna 显式拦截。
        if atr is None or pd.isna(atr) or atr <= 0:
            trade_amount = fallback_amount
        else:
            # #29: 注意 risk_per_trade 名义上是"每笔风险占账户比例"，但下面算出的 trade_amount
            #      = total*risk_per_trade / (atr_pct*multiplier)，在常见波动下远超 max_trade_amount，
            #      实际几乎总是被截到 max_trade_amount。即真正生效的是 max_trade_amount（固定金额上限），
            #      risk_per_trade 仅在极端高波动时才会成为约束。这是有意设计，非 bug。
            risk_amount = total_usdt * risk_per_trade
            trade_amount = risk_amount / (atr / price * atr_multiplier)

        trade_amount = max(trade_amount, 5)
        trade_amount = min(trade_amount, max_trade_amount)

        amount = trade_amount / price
        return amount, trade_amount

    # ═══════════════════════════════════════════════════
    #  订单执行
    # ═══════════════════════════════════════════════════

    def _execute_order(self, symbol, side, amount, price, mode, strategy_type='trend'):
        """
        执行订单。原子化：余额变动 + 持仓写入/删除在同一把锁内一次 save（修复 #2/#27）。
        返回: (success, fill_price, fill_amount)
          - 模拟交易: fill_price=信号价, fill_amount=计算量
          - 实盘交易: fill_price/fill_amount 从订单响应中提取真实成交数据
        """
        FEE_RATE = 0.001

        if not config.LIVE_TRADE:
            # 模拟交易：直接调用原子化的 execute_*_update（余额 + 持仓一次落盘）
            if side == 'buy':
                cost = amount * price
                success, raw_cost, fee = self.risk.execute_buy_update(
                    symbol, price, amount, cost, mode, strategy_type, FEE_RATE
                )
                if not success:
                    logger.error(f"❌ 模拟购买失败：虚拟余额不足！(含手续费需: {raw_cost:.2f})")
                    return False, None, None
                logger.info(f"🧪 [模拟买入] 成交:{raw_cost:.2f} | 手续费:{fee:.2f} | 剩余余额:{self.risk.state['virtual_account']['balance']:.2f}")
                return True, price, amount

            elif side == 'sell':
                pnl_pct = self.risk.execute_sell_update(symbol, price, mode, FEE_RATE)
                if pnl_pct is None:
                    logger.error(f"❌ 模拟卖出失败：无持仓 {symbol}")
                    return False, None, None
                logger.info(f"🧪 [模拟卖出] pnl: {pnl_pct:.2f}% | 余额: {self.risk.state['virtual_account']['balance']:.2f}")
                return True, price, amount

            return False, None, None

        # 实盘交易：先调交易所 API（#6 精度规整 + #7 瞬时错误重试），成交后校验（#8）再原子化更新本地持仓
        # 卖出是风控必卖，重试更激进；买入失败可跳过，只试 1 次
        max_retries = 3 if side == 'sell' else 1
        base_backoff = 2.0
        order = None
        for attempt in range(1, max_retries + 1):
            try:
                # #6: 按交易所精度规整下单量，避免因精度不符被拒单
                precise_amount = self.exchange.amount_to_precision(symbol, amount)
                if side == 'buy':
                    order = self.exchange.create_market_buy_order(symbol, precise_amount)
                else:
                    order = self.exchange.create_market_sell_order(symbol, precise_amount)
                break
            except (ccxt.NetworkError, ccxt.DDoSProtection, ccxt.RateLimitExceeded) as e:
                # #7: 网络/限频类瞬时错误，指数退避重试
                if attempt < max_retries:
                    wait = base_backoff ** attempt
                    logger.warning(f"⚠️ [实盘{side}] 第{attempt}/{max_retries}次重试 ({type(e).__name__}): {e}，{wait}s 后重试")
                    time.sleep(wait)
                else:
                    logger.error(f"❌ [实盘{side}] {symbol} 重试 {max_retries} 次仍失败: {e}")
            except Exception as e:
                # 非瞬时错误（余额不足/参数错误/签名失败等）不重试
                logger.error(f"❌ [实盘{side}] {symbol} 订单执行失败（不重试）: {e}")
                return False, None, None

        if order is None:
            return False, None, None

        # #8: 校验成交信息——average/price/filled 任一为空都视为未成交，不回落到信号价
        fill_price = order.get('average') or order.get('price')
        fill_amount = order.get('filled')
        if not fill_price or not fill_amount:
            logger.error(f"❌ [实盘{side}] {symbol} 订单 {order.get('id')} 无成交信息（average/price/filled 为空），视为失败")
            return False, None, None

        logger.info(f"✅ [实盘{side}] {symbol} 订单已执行: {order.get('id')} | 成交价:{fill_price} | 成交量:{fill_amount}")
        if side == 'buy':
            actual_cost = fill_amount * fill_price
            self.risk.execute_buy_update(
                symbol, fill_price, fill_amount, actual_cost, mode, strategy_type
            )
            return True, fill_price, fill_amount
        else:
            self.risk.execute_sell_update(symbol, fill_price, mode)
            return True, fill_price, fill_amount

    # ═══════════════════════════════════════════════════
    #  主循环
    # ═══════════════════════════════════════════════════

    def run(self):
        logger.info(f"🚀 系统已启动 (当前模式: {'实盘' if config.LIVE_TRADE else '测试/模拟'})")
        while True:
            try:
                if config.LIVE_TRADE:
                    balance_info = self.exchange.fetch_balance()
                    total_usdt = balance_info['total'].get('USDT', 0)
                else:
                    total_usdt = self.risk.state['virtual_account']['balance']

                for symbol in config.SYMBOLS:
                    df = self.fetch_data(symbol)
                    if df.empty:
                        continue

                    # 按币种独立熔断检查（检测暴跌、管理熔断过期）
                    was_fused = self.risk.is_symbol_fused(symbol)
                    self.risk.check_circuit_breaker(symbol, df)
                    is_fused = self.risk.is_symbol_fused(symbol)

                    # 仅在首次触发熔断时发送通知（避免每轮循环重复发送）
                    if is_fused and not was_fused:
                        send_notification(f"🚨 熔断: {symbol}",
                                          "检测到异常跌幅，该币种已暂停买入。已有持仓将继续监控止盈止损。")

                    price = df['close'].iloc[-1]
                    pos = self.risk.state['positions'].get(symbol)

                    # --- 第一步：追踪止盈（风控层，熔断时仍执行） ---
                    if pos:
                        trailing_reason = self.risk.update_trailing_stop(symbol, price, df)
                        if trailing_reason:
                            # _execute_order 内部已原子化完成"入账+历史+删仓位"，无需再调 execute_sell_update
                            success, fill_price, fill_amount = self._execute_order(
                                symbol, 'sell', pos['amount'], price, trailing_reason)
                            if success:
                                logger.warning(f"🚨 {symbol} 触发 {trailing_reason}")
                                pnl = (fill_price / pos['entry_price'] - 1) * 100
                                send_notification(f"🆘 离场通知: {symbol}",
                                                  f"<b>原因</b>: {trailing_reason}\n<b>收益率</b>: {pnl:.2f}%")
                            continue

                    # --- 第二步：策略信号判定 ---
                    signal, mode, strategy_type = self.get_strategy_signal(df, symbol)

                    # --- 第三步：信号确认（连续2轮相同信号才入场，过滤假突破）---
                    confirmed_mode = None
                    confirmed_strategy_type = None
                    if signal == "BUY" and not is_fused:
                        prev = self.pending_signals.get(symbol, {})
                        if prev.get('signal') == 'BUY' and prev.get('mode') == mode:
                            # 连续第2轮触发相同买入信号，确认入场
                            confirmed_mode = mode
                            confirmed_strategy_type = strategy_type
                            logger.info(f"✅ {symbol} 信号二次确认: {mode} (策略: {strategy_type})")
                            self.pending_signals.pop(symbol, None)
                        else:
                            # 第1轮，记录信号，等待下一轮确认
                            self.pending_signals[symbol] = {'signal': 'BUY', 'mode': mode, 'strategy_type': strategy_type}
                            logger.info(f"⏳ {symbol} 等待信号二次确认: {mode} (第1/2轮)")
                    else:
                        # 非BUY信号或熔断状态，清除待确认状态
                        if symbol in self.pending_signals:
                            self.pending_signals.pop(symbol, None)

                    # --- 第四步：多时间框架过滤 + 执行买入（熔断时跳过） ---
                    if confirmed_mode and self.risk.can_open_position(symbol, total_usdt):
                        # 多时间框架趋势检查
                        htf_trend = self._check_higher_tf_trend(symbol)
                        if htf_trend == -1:
                            logger.info(f"⚠️ {symbol} 高级时间框架处于下跌趋势，抑制买入信号 {confirmed_mode}")
                            continue

                        # #9: ATR 窗口用 spec.get('atr_period', 14)，与信号生成处保持一致
                        spec_for_size = self.risk.get_effective_config(symbol)
                        atr = AverageTrueRange(df['high'], df['low'], df['close'],
                                               window=spec_for_size.get('atr_period', 14)
                                               ).average_true_range().iloc[-1]
                        amount, trade_amount = self._calc_position_size(symbol, price, atr, total_usdt)

                        htf_label = "↗上升" if htf_trend == 1 else "→震荡"
                        logger.info(f"📤 准备执行买入: {symbol}, 金额: {trade_amount:.2f}, 价格: {price:.2f}, 4h趋势: {htf_label}")
                        # _execute_order 内部已原子化完成"扣款+写仓位"，无需再调 execute_buy_update
                        success, fill_price, fill_amount = self._execute_order(
                            symbol, 'buy', amount, price, confirmed_mode,
                            strategy_type=confirmed_strategy_type)
                        if success:
                            actual_cost = fill_amount * fill_price if config.LIVE_TRADE else trade_amount
                            safe_mode = confirmed_mode.replace("_", " ")
                            strategy_label = "趋势跟踪" if confirmed_strategy_type == 'trend' else "均值回归"
                            send_notification(f"✅ 买入成交: {symbol}",
                                              f"<b>价格</b>: {fill_price}\n<b>金额</b>: {actual_cost:.2f} USDT\n<b>模式</b>: {safe_mode}\n<b>策略</b>: {strategy_label}\n<b>4h趋势</b>: {htf_label}")
                            # 买入后刷新可用余额，避免后续币种基于过期余额过度下单
                            if not config.LIVE_TRADE:
                                total_usdt = self.risk.state['virtual_account']['balance']
                            else:
                                # #18: 实盘也需刷新——本地扣减已用资金（含手续费偏差可接受，
                                #      下轮循环开头会重新 fetch_balance 拉准）
                                total_usdt = max(total_usdt - actual_cost, 0)

                    # --- 第五步：执行策略卖出（熔断时仍执行） ---
                    elif signal == "SELL" and pos:
                        # _execute_order 内部已原子化完成"入账+历史+删仓位"
                        success, fill_price, fill_amount = self._execute_order(
                            symbol, 'sell', pos['amount'], price, mode)
                        if success:
                            pnl_pct = (fill_price / pos['entry_price'] - 1) * 100
                            send_notification(f"🔻 卖出成交: {symbol}",
                                              f"<b>收益率</b>: {pnl_pct:.2f}%")

                time.sleep(240)  # 4h框架下每4分钟检查一次，减少不必要的API调用
            except Exception as e:
                # #30: 用 logger.exception 记录完整 traceback，便于定位
                logger.exception(f"运行异常: {e}")
                time.sleep(10)


if __name__ == "__main__":
    AdvancedTradingBot().run()
