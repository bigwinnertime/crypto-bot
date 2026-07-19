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
from sentiment import get_sentiment_scale
from anomaly_detector import AnomalyDetector
from signal_engine import (
    detect_regime as _detect_regime_impl,
    adjust_params_by_volatility as _adjust_params_impl,
    should_buy as _should_buy_impl,
    should_sell as _should_sell_impl,
    calc_signal_score as _calc_score_impl,
    score_to_position_scale as _score_to_scale_impl,
    get_regime_position_scale as _regime_scale_impl,
    check_trailing_stop as _trailing_stop_impl,
    calc_slippage as _calc_slippage_impl,
)

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

        # 异常检测器（长期规划-3）
        self.anomaly_detector = AnomalyDetector(notifier=send_notification)
        # 市场情绪（长期规划-2）
        self._sentiment_scale = None
        self._sentiment_update_time = 0
        # HTF 数据缓存（减少 API 调用）
        self._htf_cache = {}

        init_remote_control(self.risk)
        self.cmd_thread = threading.Thread(target=start_remote_listener, daemon=True)
        self.cmd_thread.start()

    # ═══════════════════════════════════════════════════
    #  数据获取
    # ═══════════════════════════════════════════════════

    def fetch_data(self, symbol, timeframe=None, limit=100):
        """获取K线数据。HTF 数据自动缓存4小时（减少 API 调用）。"""
        try:
            clean_symbol = symbol.strip()
            tf = timeframe or config.TIMEFRAME

            # HTF 数据缓存（4小时有效，减少 API 调用）
            cache_key = f"{clean_symbol}_{tf}"
            if tf != config.TIMEFRAME:  # 非主时间框架才缓存
                cached = self._htf_cache.get(cache_key)
                if cached and (time.time() - cached['ts']) < 14400:  # 4小时缓存
                    return cached['df']

            bars = self.exchange.fetch_ohlcv(clean_symbol, timeframe=tf, limit=limit)
            df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])

            # 缓存 HTF 数据
            if tf != config.TIMEFRAME:
                self._htf_cache[cache_key] = {'df': df, 'ts': time.time()}

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
        df_htf = self.fetch_data(symbol, timeframe=htf, limit=80)
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
        """市场状态识别（委托 signal_engine）。"""
        return _detect_regime_impl(adx, bb_upper, bb_lower, bb_mid, price, spec)

    def get_strategy_signal(self, df, symbol):
        """策略信号生成器 v6 — Regime 自适应 + 信号评分。
        返回: (signal, mode, strategy_type, signal_score)
        """
        if len(df) < 60:
            logger.warning(f"{symbol} 数据不足（{len(df)}根），跳过信号判定")
            return "HOLD", "INSUFFICIENT_DATA", None, 0

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
            return "SELL", sell_reason, None, 0

        # 买入信号（根据市场状态选择策略类型）
        buy_reason, strategy_type, signal_score = self._should_buy(
            price, adx_val, rsi_val, rsi_prev, sma20, sma60,
            macd_line, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
            is_green_candle, candle_body_ratio, regime
        )
        if buy_reason:
            return "BUY", buy_reason, strategy_type, signal_score

        regime_label = {"TREND": "趋势", "RANGE": "震荡", "NEUTRAL": "中性"}.get(regime, regime)
        logger.debug(f"{symbol} 【{regime_label}】 ADX={adx_val:.1f} RSI={rsi_val:.1f} → 持币观望")
        return "HOLD", "NONE", None, 0

    def _adjust_params_by_volatility(self, spec, atr_pct):
        """根据波动率动态调整参数（委托 signal_engine）。"""
        return _adjust_params_impl(spec, atr_pct)

    # ═══════════════════════════════════════════════════
    #  卖出信号（统一决策树）
    # ═══════════════════════════════════════════════════

    def _should_sell(self, symbol, price, adx, rsi, macd, macd_sig, macd_prev,
                     bb_lower, bb_mid, bb_upper, atr, spec, adjusted):
        """统一卖出决策树（委托 signal_engine）。"""
        pos = self.risk.state['positions'].get(symbol)
        if not pos:
            return None

        entry_price = pos['entry_price']
        highest_price = pos.get('highest_price', entry_price)
        strategy_type = pos.get('strategy_type', 'trend')
        holding_hours = self.risk._get_holding_hours(pos)

        return _should_sell_impl(
            price, entry_price, highest_price, strategy_type, holding_hours,
            adx, rsi, macd, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, atr, spec, adjusted
        )

    # ═══════════════════════════════════════════════════
    #  买入信号（四套逻辑 OR 叠加）
    # ═══════════════════════════════════════════════════

    def _should_buy(self, price, adx, rsi, rsi_prev,
                    sma20, sma60,
                    macd, macd_sig, macd_prev,
                    bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
                    is_green_candle=True, candle_body_ratio=0.0, regime='NEUTRAL'):
        """Regime 自适应入场 + 信号评分（委托 signal_engine）。"""
        return _should_buy_impl(
            price, adx, rsi, rsi_prev, sma20, sma60,
            macd, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
            is_green_candle, candle_body_ratio, regime
        )

    def _calc_signal_score(self, *args, **kwargs):
        """信号强度评分（委托 signal_engine）。"""
        return _calc_score_impl(*args, **kwargs)

    def _score_to_position_scale(self, score, spec):
        """评分映射仓位（委托 signal_engine）。"""
        return _score_to_scale_impl(score, spec)

    def _get_regime_position_scale(self, symbol, regime, strategy_type):
        """动态资金分配（委托 signal_engine）。"""
        return _regime_scale_impl(regime, strategy_type)

    # ═══════════════════════════════════════════════════
    #  波动率自适应仓位计算
    # ═══════════════════════════════════════════════════

    def _calc_position_size(self, symbol, price, atr, total_usdt):
        """波动率自适应仓位计算（平衡型）。

        仓位由 risk_per_trade（风险下限）和 max_position_pct（仓位上限）共同约束：
          - 风险仓位 = total * risk_per_trade / (atr_pct * atr_multiplier)
          - 仓位上限 = total * max_position_pct
          - 最终仓位 = clip(风险仓位, min_floor, 仓位上限)

        这样在正常波动下 risk_per_trade 生效（不再是摆设），同时 max_position_pct
        防止极端低波动时单笔仓位过大。旧参数 max_trade_amount/trade_amount 保留为 fallback。
        """
        spec = self.risk.get_effective_config(symbol)
        risk_per_trade = spec.get('risk_per_trade', 0.01)
        atr_multiplier = spec.get('atr_multiplier', 2.0)
        max_position_pct = spec.get('max_position_pct', 0.08)
        max_trade_amount = spec.get('max_trade_amount', spec.get('trade_amount', 20))
        fallback_amount = spec.get('trade_amount', 20)

        if atr is None or pd.isna(atr) or atr <= 0:
            trade_amount = fallback_amount
        else:
            atr_pct = atr / price
            # 风险仓位：基于 risk_per_trade 的波动率自适应计算
            risk_amount = total_usdt * risk_per_trade
            risk_based_amount = risk_amount / (atr_pct * atr_multiplier)

            # 仓位上限：防止极端低波动时仓位过大
            max_by_pct = total_usdt * max_position_pct

            # 取风险仓位与仓位上限的较小值，再用 max_trade_amount 作为绝对上限
            trade_amount = min(risk_based_amount, max_by_pct, max_trade_amount)

        trade_amount = max(trade_amount, 5)  # 最小下单金额

        amount = trade_amount / price
        return amount, trade_amount

    # ═══════════════════════════════════════════════════
    #  订单执行
    # ═══════════════════════════════════════════════════

    def _calc_slippage(self, side, price, atr=None):
        """滑点计算（委托 signal_engine）。"""
        return _calc_slippage_impl(side, price, atr)

    def _execute_order(self, symbol, side, amount, price, mode, strategy_type='trend', atr=None, is_stop_loss=False):
        """
        执行订单（P0-3 滑点建模 + P2-8 限价单混合模式）。
        原子化：余额变动 + 持仓写入/删除在同一把锁内一次 save。
        返回: (success, fill_price, fill_amount)

        订单类型策略（P2-8 混合模式）：
          - 入场买入：限价单（挂在当前价，省手续费），未成交则放弃
          - 止损/止盈卖出：市价单（保证执行）
          - 策略信号卖出：限价单（省手续费）
          is_stop_loss=True 时强制市价单。

        模拟交易：加入滑点建模（fill_price = 信号价 ± 滑点），使模拟结果更接近实盘。
        实盘交易：fill_price/fill_amount 从订单响应中提取真实成交数据。
        """
        FEE_RATE = 0.001

        if not config.LIVE_TRADE:
            # 模拟交易：加入滑点建模（P0-3）
            sim_price = self._calc_slippage(side, price, atr)

            if side == 'buy':
                cost = amount * sim_price
                success, raw_cost, fee = self.risk.execute_buy_update(
                    symbol, sim_price, amount, cost, mode, strategy_type, FEE_RATE
                )
                if not success:
                    logger.error(f"❌ 模拟购买失败：虚拟余额不足！(含手续费需: {raw_cost:.2f})")
                    return False, None, None
                logger.info(f"🧪 [模拟买入] 成交:{raw_cost:.2f} (滑点价:{sim_price:.2f} vs 信号价:{price:.2f}) | 手续费:{fee:.2f} | 余额:{self.risk.state['virtual_account']['balance']:.2f}")
                return True, sim_price, amount

            elif side == 'sell':
                pnl_pct = self.risk.execute_sell_update(symbol, sim_price, mode, FEE_RATE)
                if pnl_pct is None:
                    logger.error(f"❌ 模拟卖出失败：无持仓 {symbol}")
                    return False, None, None
                logger.info(f"🧪 [模拟卖出] pnl: {pnl_pct:.2f}% (滑点价:{sim_price:.2f} vs 信号价:{price:.2f}) | 余额: {self.risk.state['virtual_account']['balance']:.2f}")
                return True, sim_price, amount

            return False, None, None

        # 实盘交易（P2-8 限价单混合模式）
        # 判断订单类型：止损卖出 → 市价单（保证执行）；其他 → 限价单（省手续费）
        use_limit_order = not (is_stop_loss and side == 'sell')

        max_retries = 3 if (side == 'sell' and is_stop_loss) else 1
        base_backoff = 2.0
        order = None
        for attempt in range(1, max_retries + 1):
            try:
                precise_amount = self.exchange.amount_to_precision(symbol, amount)

                if use_limit_order:
                    # 限价单：买入挂略高于当前价（确保成交），卖出挂略低于当前价
                    if side == 'buy':
                        limit_price = self.exchange.price_to_precision(symbol, price * 1.001)
                        order = self.exchange.create_limit_buy_order(symbol, precise_amount, limit_price)
                    else:
                        limit_price = self.exchange.price_to_precision(symbol, price * 0.999)
                        order = self.exchange.create_limit_sell_order(symbol, precise_amount, limit_price)
                else:
                    # 市价单（止损/止盈强制市价）
                    if side == 'buy':
                        order = self.exchange.create_market_buy_order(symbol, precise_amount)
                    else:
                        order = self.exchange.create_market_sell_order(symbol, precise_amount)
                break
            except (ccxt.NetworkError, ccxt.DDoSProtection, ccxt.RateLimitExceeded) as e:
                if attempt < max_retries:
                    wait = base_backoff ** attempt
                    logger.warning(f"⚠️ [实盘{side}] 第{attempt}/{max_retries}次重试 ({type(e).__name__}): {e}，{wait}s 后重试")
                    time.sleep(wait)
                else:
                    logger.error(f"❌ [实盘{side}] {symbol} 重试 {max_retries} 次仍失败: {e}")
            except Exception as e:
                logger.error(f"❌ [实盘{side}] {symbol} 订单执行失败（不重试）: {e}")
                return False, None, None

        if order is None:
            return False, None, None

        # 校验成交信息
        fill_price = order.get('average') or order.get('price')
        fill_amount = order.get('filled')
        if not fill_price or not fill_amount:
            # 限价单可能未成交，检查状态并取消残留挂单
            order_status = order.get('status', '')
            if order_status in ('open', 'partially_filled', 'canceled', 'expired', 'rejected'):
                # 取消交易所残留挂单（防止后续意外成交导致状态不一致）
                if order_status in ('open', 'partially_filled'):
                    try:
                        self.exchange.cancel_order(order.get('id'), symbol)
                        logger.info(f"🧹 [实盘{side}] {symbol} 已取消未成交挂单 {order.get('id')}")
                    except Exception as cancel_err:
                        logger.warning(f"⚠️ [实盘{side}] {symbol} 取消挂单失败 {order.get('id')}: {cancel_err}")
                logger.warning(f"⚠️ [实盘{side}] {symbol} 限价单未成交 (状态: {order_status})，放弃此订单")
                return False, None, None
            logger.error(f"❌ [实盘{side}] {symbol} 订单 {order.get('id')} 无成交信息，视为失败")
            return False, None, None

        logger.info(f"✅ [实盘{side}] {symbol} 订单已执行: {order.get('id')} | 成交价:{fill_price} | 成交量:{fill_amount} | 类型: {'限价' if use_limit_order else '市价'}")
        if side == 'buy':
            actual_cost = fill_amount * fill_price
            self.risk.execute_buy_update(
                symbol, fill_price, fill_amount, actual_cost, mode, strategy_type
            )
            # 买入成功后在交易所挂止损单（实盘风控保护）
            self._place_exchange_stop_loss(symbol, fill_price, fill_amount, strategy_type, atr)
            return True, fill_price, fill_amount
        else:
            self.risk.execute_sell_update(symbol, fill_price, mode)
            # 卖出后取消交易所残留的止损单（防止仓位已清零但止损单仍挂着）
            self._cancel_exchange_stop_loss(symbol)
            return True, fill_price, fill_amount

    def _place_exchange_stop_loss(self, symbol, entry_price, amount, strategy_type, atr=None):
        """P0-2: 在交易所挂止损单，防止软件监控间隔（4分钟）内的跳空风险。

        止损价计算：
          - trend 仓位：取固定止损和 ATR 止损中更接近入场价的（更早触发，更保守）
          - meanrev 仓位：使用 meanrev_config.stop_loss_pct
        挂单失败仅告警不阻断流程（软件止损仍会生效）。
        """
        try:
            spec = self.risk.get_effective_config(symbol)

            if strategy_type == 'meanrev':
                mr_cfg = spec.get('meanrev_config', {})
                stop_loss_pct = mr_cfg.get('stop_loss_pct', 0.025)
                stop_price = entry_price * (1 - stop_loss_pct)
            else:
                # 趋势仓位：固定止损
                fixed_stop = entry_price * (1 - spec.get('stop_loss_pct', 0.04))
                stop_price = fixed_stop

                # 若有 ATR，取 ATR 止损和固定止损中更高（更保守）的
                atr_multi = spec.get('atr_multiplier', 2.0)
                if atr and not pd.isna(atr) and atr > 0:
                    atr_stop = entry_price - atr_multi * atr
                    stop_price = max(stop_price, atr_stop)

            # 规整精度并挂止损市价单
            precise_stop = self.exchange.price_to_precision(symbol, stop_price)
            precise_amount = self.exchange.amount_to_precision(symbol, amount)

            self.exchange.create_order(
                symbol=symbol,
                type='STOP_LOSS',
                side='sell',
                amount=precise_amount,
                price=precise_stop,
                params={'stopPrice': precise_stop, 'type': 'STOP_LOSS'}
            )
            logger.info(f"🛡️ [实盘止损单] {symbol} 已挂止损单: 止损价 {precise_stop} (量: {precise_amount})")

        except Exception as e:
            # 止损单挂失败仅告警，不阻断买入流程（软件止损仍会生效）
            logger.warning(f"⚠️ [实盘止损单] {symbol} 挂止损单失败（软件止损仍生效）: {e}")

    def _cancel_exchange_stop_loss(self, symbol):
        """卖出后取消交易所残留的止损单（防止仓位已清零但止损单仍挂着）。"""
        try:
            # 查询该 symbol 的所有 open 订单
            open_orders = self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                # 取消止损单（STOP_LOSS 类型或 stopPrice 参数存在）
                order_type = order.get('type', '')
                has_stop = 'stopPrice' in order.get('info', {})
                if order_type in ('STOP_LOSS', 'stop_loss', 'stop') or has_stop:
                    self.exchange.cancel_order(order['id'], symbol)
                    logger.info(f"🧹 [实盘止损单] {symbol} 已取消残留止损单 {order['id']}")
        except Exception as e:
            logger.warning(f"⚠️ [实盘止损单] {symbol} 取消残留止损单失败: {e}")

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

                # 市场情绪更新（每小时更新一次，长期规划-2）
                now_ts = time.time()
                if now_ts - self._sentiment_update_time > 3600:
                    new_scale = get_sentiment_scale()
                    if new_scale and new_scale.get('score') is not None:
                        self._sentiment_scale = new_scale
                        self._sentiment_update_time = now_ts
                    else:
                        # API 失败时不更新时间戳，10分钟后重试（而非等1小时）
                        self._sentiment_update_time = now_ts - 2700  # 3600-2700=900s=15min后重试

                # 异常检测（长期规划-3）：收集所有币种数据后统一检测
                symbol_changes = {}
                symbol_dfs = {}

                for symbol in config.SYMBOLS:
                  try:
                    df = self.fetch_data(symbol)
                    if df.empty:
                        continue

                    # 记录价格变化用于跨币种异常检测
                    if len(df) >= 2:
                        symbol_changes[symbol] = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
                    symbol_dfs[symbol] = df

                    # 单币种异常检测（长期规划-3）
                    spec = self.risk.get_effective_config(symbol)
                    anomaly_result = self.anomaly_detector.run_all_checks(
                        symbol, df, spec, None  # 跨币种检测在循环后统一做
                    )
                    if anomaly_result['alerts']:
                        self.anomaly_detector.send_alerts(anomaly_result['alerts'])
                    if anomaly_result['should_fuse']:
                        self.risk.trigger_global_fuse("异常检测触发全局熔断")

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
                            # 传入 atr 用于滑点建模，is_stop_loss=True 强制市价单
                            trailing_atr = AverageTrueRange(df['high'], df['low'], df['close'],
                                                            window=self.risk.get_effective_config(symbol).get('atr_period', 14)
                                                            ).average_true_range().iloc[-1] if not df.empty else None
                            success, fill_price, fill_amount = self._execute_order(
                                symbol, 'sell', pos['amount'], price, trailing_reason,
                                atr=trailing_atr, is_stop_loss=True)
                            if success:
                                logger.warning(f"🚨 {symbol} 触发 {trailing_reason}")
                                pnl = (fill_price / pos['entry_price'] - 1) * 100
                                send_notification(f"🆘 离场通知: {symbol}",
                                                  f"<b>原因</b>: {trailing_reason}\n<b>收益率</b>: {pnl:.2f}%")
                            continue

                    # --- 第二步：策略信号判定 ---
                    signal, mode, strategy_type, signal_score = self.get_strategy_signal(df, symbol)

                    # --- 第三步：信号确认 + 情绪过滤（长期规划-2） ---
                    confirmed_mode = None
                    confirmed_strategy_type = None
                    confirmed_score = 0
                    sentiment_blocked = False
                    if signal == "BUY" and not is_fused:
                        # 市场情绪过滤
                        if self._sentiment_scale:
                            if self._sentiment_scale.get('block_trend') and strategy_type == 'trend':
                                logger.info(f"😰 {symbol} 极度恐惧，禁止趋势跟踪入场: {mode}")
                                sentiment_blocked = True
                            elif self._sentiment_scale.get('block_meanrev') and strategy_type == 'meanrev':
                                logger.info(f"🤑 {symbol} 极度贪婪，禁止均值回归入场: {mode}")
                                sentiment_blocked = True

                    if signal == "BUY" and not is_fused and not sentiment_blocked:
                        prev = self.pending_signals.get(symbol, {})
                        # 均值回归信号条件严格（RSI超卖+阳线+量能），直接入场；趋势信号需连续2轮确认
                        if strategy_type == 'meanrev' or prev.get('signal') == 'BUY':
                            # 连续第2轮触发 BUY 信号（模式可不同），确认入场
                            confirmed_mode = mode
                            confirmed_strategy_type = strategy_type
                            # 取两轮评分的较高值
                            confirmed_score = max(signal_score, prev.get('score', 0))
                            logger.debug(f"✅ {symbol} 信号二次确认: {mode} (策略: {strategy_type}, 评分: {confirmed_score}, 前轮: {prev.get('mode')})")
                            self.pending_signals.pop(symbol, None)
                        else:
                            # 第1轮，记录信号，等待下一轮确认
                            self.pending_signals[symbol] = {'signal': 'BUY', 'mode': mode, 'strategy_type': strategy_type, 'score': signal_score}
                            logger.debug(f"⏳ {symbol} 等待信号二次确认: {mode} (第1/2轮, 评分: {signal_score})")
                    elif not sentiment_blocked:
                        # 非BUY信号或熔断状态，清除待确认状态
                        # 注意：情绪过滤导致的不入场不清除 pending（情绪恢复后可直接确认）
                        if symbol in self.pending_signals:
                            self.pending_signals.pop(symbol, None)

                    # --- 第四步：多时间框架过滤 + 评分映射仓位 + 执行买入 ---
                    if confirmed_mode and self.risk.can_open_position(symbol, total_usdt):
                        # 信号评分映射仓位（中期规划-1）
                        spec_for_score = self.risk.get_effective_config(symbol)
                        score_scale = self._score_to_position_scale(confirmed_score, spec_for_score)
                        if score_scale == 0:
                            logger.info(f"⚠️ {symbol} 信号评分 {confirmed_score} 低于最低阈值，放弃入场")
                            continue

                        # 多时间框架趋势检查
                        htf_trend = self._check_higher_tf_trend(symbol)

                        # 计算当前 regime（用于动态资金分配）
                        close_prices = df['close']
                        high_prices = df['high']
                        low_prices = df['low']
                        current_adx = ADXIndicator(high_prices, low_prices, close_prices).adx().iloc[-1]
                        current_bb = BollingerBands(close_prices,
                                                   window=spec_for_score.get('bb_period', 20),
                                                   window_dev=spec_for_score.get('bb_std', 2))
                        current_regime = self._detect_regime(
                            current_adx, current_bb.bollinger_hband().iloc[-1],
                            current_bb.bollinger_lband().iloc[-1], current_bb.bollinger_mavg().iloc[-1],
                            price, spec_for_score
                        )

                        # #9: ATR 窗口用 spec.get('atr_period', 14)
                        atr = AverageTrueRange(df['high'], df['low'], df['close'],
                                               window=spec_for_score.get('atr_period', 14)
                                               ).average_true_range().iloc[-1]
                        amount, trade_amount = self._calc_position_size(symbol, price, atr, total_usdt)

                        # 动态资金分配（中期规划-2）：根据 regime 调整仓位
                        regime_scale = self._get_regime_position_scale(symbol, current_regime, confirmed_strategy_type)
                        # 评分仓位 × regime仓位 = 最终仓位比例
                        final_scale = score_scale * regime_scale

                        # HTF 下跌趋势时降低仓位
                        if htf_trend == -1:
                            if confirmed_strategy_type == 'trend':
                                logger.info(f"⚠️ {symbol} HTF下跌趋势，禁止趋势跟踪入场 {confirmed_mode}")
                                continue
                            else:
                                final_scale *= 0.5  # 均值回归信号再减半

                        # 市场情绪仓位缩放（长期规划-2）
                        if self._sentiment_scale:
                            if confirmed_strategy_type == 'trend':
                                final_scale *= self._sentiment_scale.get('trend_scale', 1.0)
                            else:
                                final_scale *= self._sentiment_scale.get('meanrev_scale', 1.0)

                        # 仓位缩放下限：低于 15% 则放弃入场（避免无意义的最小单）
                        if final_scale < 0.15:
                            logger.info(f"⚠️ {symbol} 仓位缩放 {final_scale:.0%} 低于下限 15%，放弃入场")
                            continue

                        trade_amount = trade_amount * final_scale
                        amount = trade_amount / price

                        htf_label = "↗上升" if htf_trend == 1 else ("↘下跌" if htf_trend == -1 else "→震荡")
                        logger.info(f"📤 准备执行买入: {symbol}, 金额: {trade_amount:.2f}, 价格: {price:.2f}, "
                                    f"4h趋势: {htf_label}, 评分: {confirmed_score}(仓位{final_scale:.0%}), Regime: {current_regime}")
                        # 买入用限价单（P2-8），传入 atr 用于滑点建模
                        success, fill_price, fill_amount = self._execute_order(
                            symbol, 'buy', amount, price, confirmed_mode,
                            strategy_type=confirmed_strategy_type, atr=atr)
                        if success:
                            actual_cost = fill_amount * fill_price if config.LIVE_TRADE else trade_amount
                            safe_mode = confirmed_mode.replace("_", " ")
                            strategy_label = "趋势跟踪" if confirmed_strategy_type == 'trend' else "均值回归"
                            send_notification(f"✅ 买入成交: {symbol}",
                                              f"<b>价格</b>: {fill_price}\n<b>金额</b>: {actual_cost:.2f} USDT\n<b>模式</b>: {safe_mode}\n<b>策略</b>: {strategy_label}\n<b>4h趋势</b>: {htf_label}\n<b>信号评分</b>: {confirmed_score} (仓位{final_scale:.0%})")
                            # 买入后刷新可用余额，避免后续币种基于过期余额过度下单
                            if not config.LIVE_TRADE:
                                total_usdt = self.risk.state['virtual_account']['balance']
                            else:
                                # #18: 实盘也需刷新——本地扣减已用资金（含手续费偏差可接受，
                                #      下轮循环开头会重新 fetch_balance 拉准）
                                total_usdt = max(total_usdt - actual_cost, 0)

                    # --- 第五步：执行策略卖出（熔断时仍执行） ---
                    elif signal == "SELL" and pos:
                        # 策略信号卖出用限价单，传入 atr 用于滑点建模
                        sell_atr = AverageTrueRange(df['high'], df['low'], df['close'],
                                                    window=self.risk.get_effective_config(symbol).get('atr_period', 14)
                                                    ).average_true_range().iloc[-1] if not df.empty else None
                        success, fill_price, fill_amount = self._execute_order(
                            symbol, 'sell', pos['amount'], price, mode, atr=sell_atr)
                        if success:
                            pnl_pct = (fill_price / pos['entry_price'] - 1) * 100
                            send_notification(f"🔻 卖出成交: {symbol}",
                                              f"<b>收益率</b>: {pnl_pct:.2f}%")

                  except Exception as e:
                    # 单 symbol 异常不中断其他币种处理
                    logger.exception(f"{symbol} 处理异常: {e}")
                    continue

                # 跨币种异常检测（长期规划-3）：所有币种处理完后统一检测
                if symbol_changes:
                    cross_result = self.anomaly_detector.check_cross_symbol_anomaly(symbol_changes)
                    if cross_result[0]:  # is_anomaly
                        self.anomaly_detector.send_alerts([cross_result[1]])
                        if cross_result[2]:  # should_fuse
                            self.risk.trigger_global_fuse("跨币种系统性风险触发全局熔断")
                            send_notification("🚨 全局熔断触发",
                                            f"多币种同时异动，系统已暂停所有交易\n{cross_result[1]}")

                time.sleep(240)  # 4h框架下每4分钟检查一次，减少不必要的API调用
            except Exception as e:
                # #30: 用 logger.exception 记录完整 traceback，便于定位
                logger.exception(f"运行异常: {e}")
                time.sleep(10)


if __name__ == "__main__":
    AdvancedTradingBot().run()
