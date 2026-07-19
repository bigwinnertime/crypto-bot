"""
回测框架（P0-1）— 基于 backtrader。

将 crypto-bot 的 Regime 自适应双模式策略适配为 backtrader Strategy，
复用 config.py 中的币种差异化参数，输出完整的回测报告。

用法:
    python backtest.py                          # 默认回测 BTC/USDT 最近 365 天
    python backtest.py --symbol ETH/USDT --days 730
    python backtest.py --symbol SOL/USDT --start 2024-01-01 --end 2025-01-01
    python backtest.py --all                     # 回测所有配置币种
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

import ccxt
import pandas as pd
import backtrader as bt

import config

# 复用 bot_engine 的编码设置
os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['PYTHONIOENCODING'] = 'utf-8'
if hasattr(sys, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Backtest")


# ═══════════════════════════════════════════════════
#  数据获取
# ═══════════════════════════════════════════════════

def fetch_historical_data(symbol, timeframe='4h', days=365, start=None, end=None):
    """从币安拉取历史K线数据，转为 backtrader 格式 DataFrame。

    自动检测 binance.com 可用性，不可用时回退到 binance.us。
    """
    # 尝试 binance.com，若被地区限制则回退到 binance.us
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        exchange.load_markets()
    except Exception:
        logger.info("binance.com 不可用，回退到 binance.us")
        exchange = ccxt.binanceus({'enableRateLimit': True})

    if start:
        since = exchange.parse8601(f"{start}T00:00:00Z")
    else:
        since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000

    end_ts = exchange.milliseconds()
    if end:
        end_ts = exchange.parse8601(f"{end}T00:00:00Z")

    all_ohlcv = []
    while since < end_ts:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=500)
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        if len(ohlcv) < 500:
            break

    df = pd.DataFrame(all_ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['ts'], unit='ms')
    df.set_index('datetime', inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    df = df.astype(float)

    logger.info(f"获取 {symbol} {timeframe} 数据: {len(df)} 根K线 ({df.index[0]} ~ {df.index[-1]})")
    return df


# ═══════════════════════════════════════════════════
#  backtrader 数据 Feed
# ═══════════════════════════════════════════════════

class CryptoPandasData(bt.feeds.PandasData):
    """适配加密货币数据的 PandasData（无 open_interest 等字段）。"""
    params = (
        ('datetime', None),
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'volume'),
        ('openinterest', None),
    )


# ═══════════════════════════════════════════════════
#  backtrader 指标封装
# ═══════════════════════════════════════════════════

class ADX(bt.Indicator):
    """ADX 趋势强度指标（backtrader 内置 ADX 不含 +DI/-DI，这里用标准实现）。"""
    lines = ('adx',)
    params = (('period', 14),)

    def __init__(self):
        self.addminperiod(self.params.period * 2)
        up_move = self.data.high - self.data.high(-1)
        down_move = self.data.low(-1) - self.data.low
        plus_dm = bt.If(up_move > down_move, bt.If(up_move > 0, up_move, 0), 0)
        minus_dm = bt.If(down_move > up_move, bt.If(down_move > 0, down_move, 0), 0)
        atr = bt.indicators.AverageTrueRange(self.data, period=self.params.period)
        plus_di = 100 * bt.indicators.SmoothedMovingAverage(plus_dm, period=self.params.period) / atr
        minus_di = 100 * bt.indicators.SmoothedMovingAverage(minus_dm, period=self.params.period) / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        self.lines.adx = bt.indicators.SmoothedMovingAverage(dx, period=self.params.period)


# ═══════════════════════════════════════════════════
#  策略实现
# ═══════════════════════════════════════════════════

class RegimeAdaptiveStrategy(bt.Strategy):
    """
    Regime 自适应双模式策略 — backtrader 适配版。

    复用 config.py 的参数：
      - 趋势态(TREND)：MACD金叉/ADX量能/RSI上穿50/SMA20突破
      - 震荡态(RANGE)：布林下轨+RSI底背离/RSI超卖反弹
      - 中性态(NEUTRAL)：观望
    退出逻辑与 bot_engine._should_sell 一致。
    """

    params = (
        ('symbol_config', None),  # 从 config.STRATEGY_CONFIG 注入
    )

    def __init__(self):
        spec = self.params.symbol_config or config.DEFAULT_CONFIG

        # 指标
        self.rsi = bt.indicators.RSI(self.data.close, period=14)
        self.sma20 = bt.indicators.SMA(self.data.close, period=20)
        self.sma60 = bt.indicators.SMA(self.data.close, period=60)
        self.adx = ADX(self.data, period=spec.get('atr_period', 14))
        self.boll = bt.indicators.BollingerBands(
            self.data.close,
            period=spec.get('bb_period', 20),
            devfactor=spec.get('bb_std', 2)
        )
        self.atr = bt.indicators.ATR(self.data, period=spec.get('atr_period', 14))
        self.macd = bt.indicators.MACD(self.data.close)
        self.vol_ma = bt.indicators.SMA(self.data.volume, period=spec.get('volume_ma_period', 20))

        # 状态
        self.regime = 'NEUTRAL'
        self.order = None
        self.entry_price = None
        self.highest_price = None
        self.strategy_type = 'trend'
        self.entry_bar = None
        self.pending_buy_mode = None  # 信号二次确认
        self.pending_buy_strategy = None
        self.trade_log = []

        # 从 spec 取参数
        self.spec = spec

    def _detect_regime(self):
        """市场状态识别（与 bot_engine._detect_regime 一致，RANGE 判定改为 OR）。"""
        adx_val = self.adx[0]
        bb_upper = self.boll.lines.top[0]
        bb_lower = self.boll.lines.bot[0]
        price = self.data.close[0]

        bb_width = (bb_upper - bb_lower) / price if price > 0 else 0
        trend_adx = self.spec.get('regime_trend_adx', 22)
        range_adx = self.spec.get('regime_range_adx', 22)
        trend_bb = self.spec.get('regime_trend_bb_width', 0.03)
        range_bb = self.spec.get('regime_range_bb_width', 0.02)

        if adx_val >= trend_adx and bb_width >= trend_bb:
            return 'TREND'
        # OR 判定：ADX 弱 或 布林带收口
        if adx_val <= range_adx or bb_width <= range_bb:
            return 'RANGE'
        return 'NEUTRAL'

    def _should_sell(self):
        """卖出信号判定（与 bot_engine._should_sell 一致）。"""
        if not self.position:
            return None

        price = self.data.close[0]
        adx_val = self.adx[0]
        rsi_val = self.rsi[0]
        macd_line = self.macd.macd[0]
        macd_sig = self.macd.signal[0]
        macd_prev = self.macd.macd[-1]
        bb_lower = self.boll.lines.bot[0]
        bb_mid = self.boll.lines.mid[0]
        bb_upper = self.boll.lines.top[0]
        atr_val = self.atr[0]
        entry_price = self.entry_price
        profit_pct = (price - entry_price) / entry_price

        # ═══ 均值回归退出 ═══
        if self.strategy_type == 'meanrev':
            mr_cfg = self.spec.get('meanrev_config', {})
            mr_stop = mr_cfg.get('stop_loss_pct', 0.025)
            mr_rsi_exit = mr_cfg.get('rsi_exit', 50)
            mr_max_hold = mr_cfg.get('max_hold_hours', 24)

            hard_stop = entry_price * (1 - mr_stop)
            if price <= hard_stop:
                return "均值回归止损"

            if mr_cfg.get('bb_mid_exit', True) and price >= bb_mid and profit_pct > 0:
                return "均值回归止盈(布林中轨)"

            if rsi_val >= mr_rsi_exit and profit_pct > 0:
                return "均值回归RSI退出"

            holding_bars = len(self) - self.entry_bar
            holding_hours = holding_bars * 4  # 4h timeframe
            if holding_hours >= mr_max_hold:
                if profit_pct >= 0:
                    return "均值回归超时止盈"
                else:
                    return "均值回归超时止损"

            return None

        # ═══ 趋势跟踪退出 ═══
        atr_multi = self.spec.get('atr_multiplier', 2.0)
        profit_in_atr = (price - entry_price) / atr_val if atr_val > 0 else 0
        min_profit_pct = self.spec.get('min_profit_pct', 0.008)

        # 动态ATR倍数：盈利不足时收紧，盈利充足时放宽
        if profit_in_atr < 1.0:
            atr_multi = min(atr_multi, 1.5)
        elif profit_in_atr > 3.0:
            atr_multi = atr_multi * 1.25

        # ATR 追踪止损
        if atr_val > 0:
            trail_stop = self.highest_price - atr_multi * atr_val
            if price <= trail_stop:
                return "ATR追踪止损"

        # 固定止损
        hard_stop = entry_price * (1 - self.spec.get('stop_loss_pct', 0.04))
        if price <= hard_stop:
            return "固定止损"

        # 保本止损：盈利超 breakeven_trigger 后止损上移至成本价
        breakeven_trigger = self.spec.get('breakeven_trigger', 0.02)
        breakeven_buffer = self.spec.get('breakeven_buffer', 0.003)
        if profit_pct >= breakeven_trigger:
            breakeven_stop = entry_price * (1 + breakeven_buffer)
            if price <= breakeven_stop:
                return f"保本止损(盈利{profit_pct:.1%}后回撤)"

        # 主动止盈
        profit_target = self.spec.get('profit_target_atr', 6.0)
        if profit_in_atr >= profit_target:
            return f"主动止盈({profit_in_atr:.1f}×ATR)"

        # RSI 超买
        rsi_overbought_thr = self.spec.get('rsi_overbought', 70)
        if profit_pct > 0.05 and profit_pct > min_profit_pct and rsi_val > rsi_overbought_thr:
            return "RSI超买预警"

        # MACD 死叉 + ADX 回落
        strong_trend_threshold = self.spec.get('adx_threshold', 25) * 1.5
        macd_dead = (macd_prev > macd_sig and macd_line < macd_sig)
        if macd_dead and profit_pct > min_profit_pct and adx_val < strong_trend_threshold:
            return "MACD死叉+ADX回落"

        return None

    def _check_trailing_stop(self):
        """分阶段追踪止盈（与 risk_manager.update_trailing_stop 一致）。"""
        if not self.position or self.strategy_type == 'meanrev':
            return None

        price = self.data.close[0]
        if price > self.highest_price:
            self.highest_price = price
            return None

        drawdown = (self.highest_price - price) / self.highest_price
        highest_profit = (self.highest_price - self.entry_price) / self.entry_price

        trailing_stops = self.spec.get('trailing_stops', [])
        active_trigger = None
        for stop_cfg in sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True):
            if highest_profit >= stop_cfg['profit_threshold']:
                active_trigger = stop_cfg.get('trigger_drawdown', stop_cfg.get('trailing_pct', 0.02))
                break

        if active_trigger is None:
            return None

        # 时间衰减
        time_decay_cfg = self.spec.get('time_decay', {})
        if time_decay_cfg.get('enabled', False):
            time_multiplier = self._calc_time_multiplier()
            active_trigger = active_trigger / time_multiplier

        if drawdown >= active_trigger:
            return f"追踪止盈(回撤{drawdown:.2%})"

        return None

    def _calc_time_multiplier(self):
        """计算时间衰减系数。"""
        holding_bars = len(self) - self.entry_bar
        holding_hours = holding_bars * 4  # 4h timeframe
        intervals = self.spec.get('time_decay', {}).get('intervals', [])
        for interval in intervals:
            if holding_hours <= interval['hours']:
                return interval['multiplier']
        return 1.0

    def _should_buy(self):
        """买入信号判定（与 bot_engine._should_buy 一致）。"""
        price = self.data.close[0]
        adx_val = self.adx[0]
        rsi_val = self.rsi[0]
        rsi_prev = self.rsi[-1]
        rsi_3_ago = self.rsi[-3]
        sma20 = self.sma20[0]
        sma60 = self.sma60[0]
        macd_line = self.macd.macd[0]
        macd_sig = self.macd.signal[0]
        macd_prev = self.macd.macd[-1]
        bb_lower = self.boll.lines.bot[0]
        bb_mid = self.boll.lines.mid[0]
        bb_upper = self.boll.lines.top[0]
        vol_ma_val = self.vol_ma[0]
        vol_ratio = self.data.volume[0] / vol_ma_val if vol_ma_val > 0 else 0

        candle_body = self.data.close[0] - self.data.open[0]
        candle_range = self.data.high[0] - self.data.low[0]
        is_green = candle_body > 0
        body_ratio = abs(candle_body) / candle_range if candle_range > 0 else 0

        vol_thr = self.spec.get('volume_threshold', 1.5)
        adx_thr = self.spec.get('adx_threshold', 25)
        rsi_oversold_thr = self.spec.get('rsi_oversold', 35)
        min_body_ratio = self.spec.get('min_body_ratio', 0.30)
        quality_candle = body_ratio > min_body_ratio

        macd_golden = (macd_prev < macd_sig and macd_line > macd_sig)
        macd_above_zero = macd_line > -abs(macd_sig) * 0.5
        macd_dead = (macd_prev > macd_sig and macd_line < macd_sig)

        regime = self.regime

        if regime == 'TREND':
            if adx_val > adx_thr * 0.8:
                if macd_golden and macd_above_zero and vol_ratio >= vol_thr and is_green and quality_candle:
                    return "TREND_MACD_GOLDEN_CROSS", 'trend'
                if (adx_val > adx_thr and vol_ratio >= vol_thr and
                        price > sma20 and rsi_val > 50 and is_green and quality_candle):
                    return "TREND_ADX_VOL_CONFIRM", 'trend'

            rsi_cross_50 = (rsi_prev < 50 <= rsi_val)
            trend_ok = (price > sma60 or adx_val > adx_thr)
            if rsi_cross_50 and trend_ok and price > bb_mid and vol_ratio >= vol_thr * 0.8:
                return "TREND_RSI_50_CROSS", 'trend'

            if (price > sma20 and rsi_val > 50 and
                    vol_ratio >= vol_thr and not macd_dead and is_green and quality_candle):
                return "TREND_SMA20_BREAKOUT", 'trend'

        if regime in ('RANGE', 'NEUTRAL'):
            touch_lower = price <= bb_lower * 1.01
            bb_width = (bb_upper - bb_lower) / price
            bb_sufficient = bb_width > self.spec.get('regime_range_bb_width', 0.02)
            rsi_bouncing = (rsi_val > rsi_prev and rsi_val < rsi_oversold_thr)
            if touch_lower and bb_sufficient and rsi_bouncing and vol_ratio >= vol_thr * 0.8:
                return "MEANREV_BB_LOWER_RSI_DIVERGENCE", 'meanrev'

            rsi_oversold_bounce = (
                rsi_prev <= rsi_oversold_thr and rsi_val > rsi_prev and
                rsi_val < 45 and is_green and quality_candle and
                vol_ratio >= vol_thr * 0.8
            )
            if rsi_oversold_bounce:
                return "MEANREV_RSI_OVERSOLD_BOUNCE", 'meanrev'

            mr_body_ratio = min_body_ratio * 0.5
            mr_quality_candle = body_ratio > mr_body_ratio
            bb_squeeze_bounce = (
                touch_lower and is_green and mr_quality_candle and
                vol_ratio >= vol_thr * 0.7 and rsi_val < 45 and bb_sufficient
            )
            if bb_squeeze_bounce:
                return "MEANREV_BB_SQUEEZE_BOUNCE", 'meanrev'

        return None, None

    def next(self):
        """每根K线回调。"""
        self.regime = self._detect_regime()

        # 更新最高价
        if self.position:
            price = self.data.close[0]
            if price > self.highest_price:
                self.highest_price = price

        # 卖出逻辑（优先）
        if self.position:
            # 追踪止盈
            trailing_reason = self._check_trailing_stop()
            if trailing_reason:
                self._log_trade(trailing_reason)
                self.order = self.close()
                return

            # 策略卖出
            sell_reason = self._should_sell()
            if sell_reason:
                self._log_trade(sell_reason)
                self.order = self.close()
                return

        # 买入逻辑
        if not self.position:
            buy_reason, strategy_type = self._should_buy()

            if buy_reason:
                # 信号二次确认（趋势信号需连续2轮BUY；均值回归信号条件严格，直接入场）
                if self.pending_buy_mode is not None or strategy_type == 'meanrev':
                    # 计算仓位（简化：使用固定比例）
                    total_value = self.broker.getvalue()
                    spec = self.spec
                    risk_per_trade = spec.get('risk_per_trade', 0.01)
                    max_position_pct = spec.get('max_position_pct', 0.08)
                    max_trade_amount = spec.get('max_trade_amount', 100)

                    atr_val = self.atr[0]
                    price = self.data.close[0]

                    if atr_val > 0:
                        atr_pct = atr_val / price
                        risk_based = total_value * risk_per_trade / (atr_pct * spec.get('atr_multiplier', 2.0))
                        max_by_pct = total_value * max_position_pct
                        trade_amount = min(risk_based, max_by_pct, max_trade_amount)
                    else:
                        trade_amount = spec.get('trade_amount', 20)

                    trade_amount = max(trade_amount, 5)

                    # HTF趋势检查（简化版：用SMA60判断长期趋势）
                    # 价格<SMA60 视为下跌趋势：禁止趋势跟踪入场，均值回归仓位减半
                    price_below_sma60 = self.data.close[0] < self.sma60[0]
                    if price_below_sma60:
                        if strategy_type == 'trend':
                            self.pending_buy_mode = None
                            return  # 禁止趋势跟踪入场
                        else:
                            trade_amount = trade_amount * 0.5  # 均值回归仓位减半

                    size = trade_amount / price

                    self.order = self.buy(size=size)
                    self.entry_price = price
                    self.highest_price = price
                    self.strategy_type = strategy_type
                    self.entry_bar = len(self)
                    self.pending_buy_mode = None
                    logger.info(f"买入 {buy_reason} @ {price:.2f} 策略={strategy_type}")
                else:
                    # 首次信号，记录待确认
                    self.pending_buy_mode = buy_reason
                    self.pending_buy_strategy = strategy_type
            else:
                self.pending_buy_mode = None

    def _log_trade(self, reason):
        """记录交易日志。"""
        price = self.data.close[0]
        pnl_pct = (price / self.entry_price - 1) * 100 if self.entry_price else 0
        holding_bars = len(self) - self.entry_bar if self.entry_bar else 0
        self.trade_log.append({
            'date': self.data.datetime.date(0),
            'entry_price': self.entry_price,
            'exit_price': price,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'strategy_type': self.strategy_type,
            'holding_bars': holding_bars,
        })
        logger.info(f"卖出 {reason} @ {price:.2f} PnL={pnl_pct:.2f}% 持仓{holding_bars}根")

    def notify_order(self, order):
        """订单状态回调。"""
        if order.status in [order.Completed]:
            self.order = None
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            logger.warning(f"订单被拒绝/取消: {order.Status}")
            self.order = None

    def notify_trade(self, trade):
        """交易完成回调。"""
        if trade.isclosed:
            logger.debug(f"交易完成: PnL={trade.pnl:.2f}")


# ═══════════════════════════════════════════════════
#  回测引擎 & 报告
# ═══════════════════════════════════════════════════

class BacktestReport:
    """回测报告生成器。"""

    def __init__(self, symbol, strategy_instance, initial_cash):
        self.symbol = symbol
        self.strategy = strategy_instance
        self.initial_cash = initial_cash

    def generate(self):
        """生成并打印回测报告。"""
        trades = self.strategy.trade_log

        print("\n" + "=" * 60)
        print(f"📊 回测报告: {self.symbol}")
        print("=" * 60)

        # 账户摘要
        final_value = self.strategy.broker.getvalue()
        roi = ((final_value / self.initial_cash) - 1) * 100
        print(f"\n💰 账户摘要:")
        print(f"   初始资金:     {self.initial_cash:.2f} USDT")
        print(f"   最终净值:     {final_value:.2f} USDT")
        print(f"   总收益率:     {roi:+.2f}%")

        # 交易统计
        if not trades:
            print("\n📭 无交易记录")
            print("=" * 60 + "\n")
            return

        total_trades = len(trades)
        wins = [t for t in trades if t['pnl_pct'] > 0]
        losses = [t for t in trades if t['pnl_pct'] <= 0]
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

        avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t['pnl_pct'] for t in losses) / len(losses)) if losses else 0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')

        # 最大连续亏损
        max_consec_loss = 0
        cur_consec = 0
        for t in trades:
            if t['pnl_pct'] <= 0:
                cur_consec += 1
                max_consec_loss = max(max_consec_loss, cur_consec)
            else:
                cur_consec = 0

        # 按策略类型统计
        trend_trades = [t for t in trades if t['strategy_type'] == 'trend']
        meanrev_trades = [t for t in trades if t['strategy_type'] == 'meanrev']

        print(f"\n📊 交易统计:")
        print(f"   总交易次数:   {total_trades}")
        print(f"   盈利次数:     {len(wins)} ✅")
        print(f"   亏损次数:     {len(losses)} ❌")
        print(f"   胜率:         {win_rate:.1f}%")
        print(f"   盈亏比:       {profit_factor:.2f}")
        print(f"   平均盈利:     {avg_win:+.2f}%")
        print(f"   平均亏损:     {-avg_loss:+.2f}%")
        print(f"   最大连续亏损: {max_consec_loss} 笔")

        print(f"\n📋 策略分布:")
        print(f"   趋势跟踪:     {len(trend_trades)} 笔 "
              f"({'胜率 {:.1f}%'.format(len([t for t in trend_trades if t['pnl_pct'] > 0]) / len(trend_trades) * 100) if trend_trades else 'N/A'})")
        print(f"   均值回归:     {len(meanrev_trades)} 笔 "
              f"({'胜率 {:.1f}%'.format(len([t for t in meanrev_trades if t['pnl_pct'] > 0]) / len(meanrev_trades) * 100) if meanrev_trades else 'N/A'})")

        # 退出原因统计
        print(f"\n🏁 退出原因统计:")
        reason_counts = {}
        for t in trades:
            reason = t['reason']
            if reason not in reason_counts:
                reason_counts[reason] = {'count': 0, 'total_pnl': 0}
            reason_counts[reason]['count'] += 1
            reason_counts[reason]['total_pnl'] += t['pnl_pct']

        for reason, stats in sorted(reason_counts.items(), key=lambda x: x[1]['count'], reverse=True):
            avg_pnl = stats['total_pnl'] / stats['count']
            print(f"   {reason:<30} {stats['count']:>3} 笔  平均 {avg_pnl:+.2f}%")

        # 最近10笔交易
        print(f"\n📝 最近 10 笔交易:")
        print(f"   {'日期':<12} {'入场':>10} {'出场':>10} {'收益':>8} {'策略':>6} {'原因'}")
        print(f"   {'-'*70}")
        for t in trades[-10:]:
            print(f"   {str(t['date']):<12} {t['entry_price']:>10.2f} {t['exit_price']:>10.2f} "
                  f"{t['pnl_pct']:>+7.2f}% {'趋势' if t['strategy_type'] == 'trend' else '均值':>6} {t['reason']}")

        print("=" * 60 + "\n")


# ═══════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════

def run_backtest(symbol, days=365, start=None, end=None, initial_cash=10000.0, plot=False):
    """运行单个币种回测。"""
    spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

    # 获取数据
    df = fetch_historical_data(symbol, timeframe=config.TIMEFRAME, days=days, start=start, end=end)
    if len(df) < 60:
        logger.error(f"{symbol} 数据不足（{len(df)}根），至少需要60根K线")
        return

    # 创建回测引擎
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=0.001)  # 0.1% 手续费

    # 添加数据
    data = CryptoPandasData(dataname=df)
    cerebro.adddata(data)

    # 添加策略
    cerebro.addstrategy(RegimeAdaptiveStrategy, symbol_config=spec)

    # 添加分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')

    # 运行
    logger.info(f"开始回测 {symbol} (初始资金: {initial_cash:.0f} USDT)")
    results = cerebro.run()
    strat = results[0]

    final_value = cerebro.broker.getvalue()
    logger.info(f"回测完成 {symbol} (最终净值: {final_value:.2f} USDT)")

    # 生成报告
    report = BacktestReport(symbol, strat, initial_cash)
    report.generate()

    # 分析器结果
    try:
        sharpe = strat.analyzers.sharpe.get_analysis()
        dd = strat.analyzers.drawdown.get_analysis()
        print(f"📈 风险指标:")
        print(f"   夏普比率:     {sharpe.get('sharperatio', 'N/A')}")
        print(f"   最大回撤:     {dd.get('max', {}).get('drawdown', 'N/A'):.2f}%" if isinstance(dd.get('max', {}).get('drawdown'), (int, float)) else f"   最大回撤:     N/A")
        print(f"   回撤持续:     {dd.get('max', {}).get('len', 'N/A')} 根K线")
    except Exception as e:
        logger.warning(f"分析器结果提取失败: {e}")

    # 绘图
    if plot:
        try:
            cerebro.plot(style='candlestick', volume=True)
        except Exception as e:
            logger.warning(f"绘图失败（可能缺少 matplotlib）: {e}")

    return strat


def main():
    parser = argparse.ArgumentParser(description='crypto-bot 回测框架')
    parser.add_argument('--symbol', type=str, default='BTC/USDT', help='交易对 (如 BTC/USDT)')
    parser.add_argument('--all', action='store_true', help='回测所有配置币种')
    parser.add_argument('--days', type=int, default=365, help='回测天数')
    parser.add_argument('--start', type=str, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--cash', type=float, default=10000.0, help='初始资金 (USDT)')
    parser.add_argument('--plot', action='store_true', help='显示K线图')

    args = parser.parse_args()

    if args.all:
        for symbol in config.SYMBOLS:
            try:
                run_backtest(symbol, days=args.days, start=args.start, end=args.end,
                             initial_cash=args.cash, plot=False)
            except Exception as e:
                logger.error(f"{symbol} 回测失败: {e}")
    else:
        symbol = args.symbol.upper()
        if symbol not in config.STRATEGY_CONFIG:
            logger.warning(f"{symbol} 不在 STRATEGY_CONFIG 中，将使用 DEFAULT_CONFIG")
        run_backtest(symbol, days=args.days, start=args.start, end=args.end,
                     initial_cash=args.cash, plot=args.plot)


if __name__ == "__main__":
    main()
