import os
import sys

os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['PYTHONIOENCODING'] = 'utf-8'

if hasattr(sys, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

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
        logging.FileHandler("bot_run.log", encoding='utf-8', errors='replace'),
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

        # 信号确认：已重构为单线确认即入场，不再记录上一轮信号

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
        返回: 1 (上升趋势), -1 (下降趋势), 0 (震荡/数据不足)
        """
        htf = getattr(config, 'HIGHER_TIMEFRAME', '4h')
        df_htf = self.fetch_data(symbol, timeframe=htf, limit=60)
        if df_htf.empty or len(df_htf) < 60:
            return 0

        close = df_htf['close']
        high = df_htf['high']
        low = df_htf['low']
        price = close.iloc[-1]

        sma20_htf = SMAIndicator(close, window=20).sma_indicator().iloc[-1]
        sma60_htf = SMAIndicator(close, window=60).sma_indicator().iloc[-1]
        adx_htf = ADXIndicator(high, low, close).adx().iloc[-1]

        # 上升趋势: SMA20 > SMA60 + 价格 > SMA20 + ADX > 20
        if sma20_htf > sma60_htf and price > sma20_htf and adx_htf > 20:
            return 1
        # 下降趋势: SMA20 < SMA60 + 价格 < SMA20 + ADX > 20
        if sma20_htf < sma60_htf and price < sma20_htf and adx_htf > 20:
            return -1

        return 0

    # ═══════════════════════════════════════════════════
    #  信号生成
    # ═══════════════════════════════════════════════════

    def get_strategy_signal(self, df, symbol):
        """策略信号生成器 v4"""
        if len(df) < 60:
            logger.warning(f"{symbol} 数据不足（{len(df)}根），跳过信号判定")
            return "HOLD", "INSUFFICIENT_DATA"

        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

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

        # 成交量方向分析：买入量占比
        candle_body = close.iloc[-1] - df['open'].iloc[-1]
        candle_range = high.iloc[-1] - low.iloc[-1]
        buy_vol_ratio = (volume.iloc[-1] * (candle_body / candle_range)) if candle_range > 0 else 0
        is_green_candle = candle_body > 0

        # 波动率自适应参数
        adjusted = self._adjust_params_by_volatility(spec, atr_pct)

        # 卖出信号（持仓时优先判断）
        sell_reason = self._should_sell(
            symbol, price, adx_val, rsi_val, macd_line, macd_sig, macd_prev,
            bb_lower, atr, spec, adjusted
        )
        if sell_reason:
            return "SELL", sell_reason

        # 买入信号
        buy_reason = self._should_buy(
            price, adx_val, rsi_val, rsi_prev, rsi_3_ago, sma20, sma60,
            macd_line, macd_sig, macd_prev,
            bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
            is_green_candle
        )
        if buy_reason:
            return "BUY", buy_reason

        regime = "【趋势】" if adx_val > adjusted['adx_threshold'] else "【震荡】"
        logger.debug(f"{symbol} {regime} ADX={adx_val:.1f} RSI={rsi_val:.1f} → 持币观望")
        return "HOLD", "NONE"

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
                     bb_lower, atr, spec, adjusted):
        """
        统一卖出决策树：
          1. ATR 追踪止损
          2. 固定止损
          3. 主动止盈（盈利达 N×ATR）
          4. RSI 超买（盈利 > 5% 时）
          5. MACD 死叉 + ADX 趋弱（仅弱趋势中）
        """
        pos = self.risk.state['positions'].get(symbol)
        if not pos:
            return None

        entry_price = pos['entry_price']
        highest_price = pos.get('highest_price', entry_price)
        atr_multi = spec.get('atr_multiplier', 2.0)
        profit_pct = (price - entry_price) / entry_price
        profit_in_atr = (price - entry_price) / atr if atr > 0 else 0

        # 1. ATR 追踪止损
        trail_stop = highest_price - atr_multi * atr
        if price <= trail_stop:
            return f"ATR追踪止损 (价{price:.2f}≤线{trail_stop:.2f})"

        # 2. 固定止损
        hard_stop = entry_price * (1 - spec.get('stop_loss_pct', 0.03))
        if price <= hard_stop:
            return f"固定止损 (价{price:.2f}≤线{hard_stop:.2f})"

        # 3. 主动止盈：盈利达到 N×ATR 时锁定利润
        profit_target = spec.get('profit_target_atr', 3.0)
        if profit_in_atr >= profit_target:
            return f"主动止盈 (盈利{profit_in_atr:.1f}×ATR ≥ {profit_target:.0f}×ATR)"

        # 4. RSI 超买（仅在盈利 > 5% 时触发）
        if profit_pct > 0.05 and rsi > adjusted['rsi_overbought']:
            return f"RSI超买预警 (RSI={rsi:.1f}>阈值{adjusted['rsi_overbought']:.0f})"

        # 5. MACD 死叉 + ADX 趋弱（仅在弱趋势/震荡中使用，强趋势中忽略）
        strong_trend_threshold = adjusted['adx_threshold'] * 1.5
        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        if macd_dead and adx < strong_trend_threshold:
            if adx < adjusted['adx_threshold'] * 0.75:
                return "MACD死叉+ADX趋弱"

        return None

    # ═══════════════════════════════════════════════════
    #  买入信号（四套逻辑 OR 叠加）
    # ═══════════════════════════════════════════════════

    def _should_buy(self, price, adx, rsi, rsi_prev, rsi_3_ago,
                    sma20, sma60,
                    macd, macd_sig, macd_prev,
                    bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
                    is_green_candle=True):
        """
        四套买入逻辑（任意一套命中 + 量能确认 → 买入）：
          A. 趋势跟随：MACD 金叉 + ADX 确认 + 量能
          B. RSI 反弹：RSI 上穿 50 + 价格站稳布林中轨 + 趋势确认 + 量能
          C. 布林支撑：价格触下轨 + 真正 RSI 底背离 + 量能
          D. SMA20 突破：价格站上 SMA20 + RSI 健康 + 量能
        """
        vol_thr = spec.get('volume_threshold', 1.5)

        # ── A. 趋势跟随（MACD 金叉 + 量能放大）
        macd_golden = (macd_prev < macd_sig and macd > macd_sig)
        if adx > adjusted['adx_threshold'] * 0.8:
            if macd_golden and vol_ratio >= vol_thr and is_green_candle:
                return "TREND_MACD_GOLDEN_CROSS"
            if (adx > adjusted['adx_threshold'] and
                    vol_ratio >= vol_thr and
                    price > sma20 and rsi > 50 and is_green_candle):
                return "TREND_ADX_VOL_CONFIRM"

        # ── B. RSI 均线交叉反弹（RSI 上穿 50 + 趋势过滤）
        rsi_cross_50 = (rsi_prev < 50 <= rsi)
        # 趋势过滤: 价格在 SMA60 之上或 ADX 显示趋势
        trend_ok = (price > sma60 or adx > adjusted['adx_threshold'])
        if rsi_cross_50 and trend_ok and price > bb_mid and vol_ratio >= vol_thr * 0.8:
            return "RSI_50_CROSS_BOUNCE"

        # ── C. 布林下轨支撑 + RSI 底背离
        touch_lower = price <= bb_lower * 1.005
        bb_width = (bb_upper - bb_lower) / price
        bb_sufficient = bb_width > 0.02
        rsi_bouncing = (rsi_3_ago is not None and rsi > rsi_3_ago and rsi < 35)
        if touch_lower and bb_sufficient and rsi_bouncing and vol_ratio >= vol_thr * 0.8:
            return "BB_LOWER_RSI_DIVERGENCE"

        # ── D. SMA20 突破
        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        if (price > sma20 and rsi > 55 and
                vol_ratio >= vol_thr and not macd_dead and is_green_candle):
            return "SMA20_BREAKOUT"

        return None

    # ═══════════════════════════════════════════════════
    #  波动率自适应仓位计算
    # ═══════════════════════════════════════════════════

    def _calc_position_size(self, symbol, price, atr, total_usdt):
        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        risk_per_trade = spec.get('risk_per_trade', 0.01)
        max_trade_amount = spec.get('max_trade_amount', spec.get('trade_amount', 20))
        atr_multiplier = spec.get('atr_multiplier', 2.0)
        fallback_amount = spec.get('trade_amount', 20)

        if atr <= 0:
            trade_amount = fallback_amount
        else:
            risk_amount = total_usdt * risk_per_trade
            trade_amount = risk_amount / (atr / price * atr_multiplier)

        trade_amount = max(trade_amount, 5)
        trade_amount = min(trade_amount, max_trade_amount)

        amount = trade_amount / price
        return amount, trade_amount

    # ═══════════════════════════════════════════════════
    #  订单执行
    # ═══════════════════════════════════════════════════

    def _execute_order(self, symbol, side, amount, price, mode):
        FEE_RATE = 0.001

        if not config.LIVE_TRADE:
            success, main_val, fee, trade_pnl = self.risk.execute_virtual_trade(symbol, side, amount, price, FEE_RATE)
            
            if side == 'buy':
                if not success:
                    logger.error(f"❌ 模拟购买失败：虚拟余额不足！(含手续费需: {main_val:.2f})")
                    return False
                
                logger.info(f"🧪 [模拟买入] 成交:{main_val:.2f} | 手续费:{fee:.2f} | 剩余余额:{self.risk.state['virtual_account']['balance']:.2f}")

            elif side == 'sell':
                if not success:
                    return False
                logger.info(f"🧪 [模拟卖出] 净收入:{main_val:.2f} | 单笔净盈亏:{trade_pnl:.2f}")

            return True

        try:
            if side == 'buy':
                order = self.exchange.create_market_buy_order(symbol, amount)
                logger.info(f"✅ [实盘买入] {symbol} 订单已执行: {order['id']}")
                return True
            elif side == 'sell':
                order = self.exchange.create_market_sell_order(symbol, amount)
                logger.info(f"✅ [实盘卖出] {symbol} 订单已执行: {order['id']}")
                return True
        except Exception as e:
            logger.error(f"❌ [实盘{side}] 订单执行失败: {e}")
            return False

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

                    # 按币种独立熔断检查
                    if self.risk.check_circuit_breaker(symbol, df):
                        send_notification(f"🚨 熔断: {symbol}", "检测到异常跌幅，该币种已暂停交易。")
                        continue

                    price = df['close'].iloc[-1]
                    pos = self.risk.state['positions'].get(symbol)

                    # --- 第一步：追踪止盈（风控层） ---
                    trailing_reason = self.risk.update_trailing_stop(symbol, price, df)
                    if trailing_reason and pos:
                        if self._execute_order(symbol, 'sell', pos['amount'], price, trailing_reason):
                            logger.warning(f"🚨 {symbol} 触发 {trailing_reason}")
                            pnl = (price / pos['entry_price'] - 1) * 100
                            send_notification(f"🆘 离场通知: {symbol}",
                                              f"<b>原因</b>: {trailing_reason}\n<b>收益率</b>: {pnl:.2f}%")
                            self.risk.execute_sell_update(symbol, price, trailing_reason)
                        continue

                    # --- 第二步：策略信号判定 ---
                    signal, mode = self.get_strategy_signal(df, symbol)

                    # --- 第三步：信号确认（重构：取消延迟，单线确认即入场） ---
                    if signal == "BUY":
                        confirmed_mode = mode
                    else:
                        confirmed_mode = None

                    # --- 第四步：多时间框架过滤 + 执行买入 ---
                    if confirmed_mode and self.risk.can_open_position(symbol, total_usdt):
                        # 多时间框架趋势检查
                        htf_trend = self._check_higher_tf_trend(symbol)
                        if htf_trend == -1:
                            logger.info(f"⚠️ {symbol} 高级时间框架处于下跌趋势，抑制买入信号 {confirmed_mode}")
                            continue

                        atr = AverageTrueRange(df['high'], df['low'], df['close'],
                                               window=14).average_true_range().iloc[-1]
                        amount, trade_amount = self._calc_position_size(symbol, price, atr, total_usdt)

                        htf_label = "↗上升" if htf_trend == 1 else "→震荡"
                        logger.info(f"📤 准备执行买入: {symbol}, 金额: {trade_amount:.2f}, 价格: {price:.2f}, 4h趋势: {htf_label}")
                        if self._execute_order(symbol, 'buy', amount, price, confirmed_mode):
                            self.risk.execute_buy_update(symbol, price, amount, trade_amount, confirmed_mode)
                            safe_mode = confirmed_mode.replace("_", " ")
                            send_notification(f"✅ 买入成交: {symbol}",
                                              f"<b>价格</b>: {price}\n<b>金额</b>: {trade_amount:.2f} USDT\n<b>模式</b>: {safe_mode}\n<b>4h趋势</b>: {htf_label}")

                    # --- 第五步：执行策略卖出 ---
                    elif signal == "SELL" and pos:
                        if self._execute_order(symbol, 'sell', pos['amount'], price, mode):
                            pnl_pct = self.risk.execute_sell_update(symbol, price, mode)
                            send_notification(f"🔻 卖出成交: {symbol}",
                                              f"<b>收益率</b>: {pnl_pct:.2f}%")

                time.sleep(60)
            except Exception as e:
                logger.error(f"运行异常: {e}")
                time.sleep(10)


if __name__ == "__main__":
    AdvancedTradingBot().run()
