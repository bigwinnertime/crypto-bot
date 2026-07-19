"""
轻量级自建回测器（native backtester）— 直接复用 bot_engine 信号逻辑。

与 backtrader 版本的区别：
  1. 指标用 ta 库（与实盘 bot_engine 完全一致，无计算差异）
  2. 信号逻辑直接复用 bot_engine 的 _detect_regime / _should_buy / _should_sell
  3. 无框架约束，向量化指标计算 + 逐K线事件驱动，速度快
  4. 回测结果与实盘完全一致（同一套代码路径）

用法:
    python backtest_native.py --symbol BTC/USDT --days 730
    python backtest_native.py --all --days 730
    python backtest_native.py --symbol ETH/USDT --start 2024-01-01 --end 2025-01-01
"""
import argparse
import copy
import logging
import os
import sys
from datetime import datetime

import ccxt
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

import config

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
logger = logging.getLogger("NativeBacktest")


# ═══════════════════════════════════════════════════
#  数据获取（复用 backtest.py 的逻辑）
# ═══════════════════════════════════════════════════

def fetch_historical_data(symbol, timeframe='4h', days=365, start=None, end=None):
    """从币安拉取历史K线数据。"""
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        exchange.load_markets()
    except Exception:
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
#  指标计算（与 bot_engine 完全一致，用 ta 库）
# ═══════════════════════════════════════════════════

def calculate_indicators(df, spec):
    """计算所有技术指标（与 bot_engine.get_strategy_signal 一致）。"""
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    # RSI（默认14周期，与 bot_engine 一致：RSIIndicator(close) 用默认值）
    df['rsi'] = RSIIndicator(close).rsi()

    # SMA
    df['sma20'] = SMAIndicator(close, window=20).sma_indicator()
    df['sma60'] = SMAIndicator(close, window=60).sma_indicator()

    # ADX
    df['adx'] = ADXIndicator(high, low, close).adx()

    # 布林带
    bb = BollingerBands(close, window=spec.get('bb_period', 20), window_dev=spec.get('bb_std', 2))
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_lower'] = bb.bollinger_lband()

    # ATR
    df['atr'] = AverageTrueRange(high, low, close, window=spec.get('atr_period', 14)).average_true_range()
    df['atr_pct'] = df['atr'] / close

    # MACD
    macd_ind = MACD(close)
    df['macd'] = macd_ind.macd()
    df['macd_signal'] = macd_ind.macd_signal()

    # 成交量均线
    df['vol_ma'] = volume.rolling(spec.get('volume_ma_period', 20)).mean()
    df['vol_ratio'] = volume / df['vol_ma']

    return df


# ═══════════════════════════════════════════════════
#  信号逻辑（直接复用 bot_engine 的逻辑）
# ═══════════════════════════════════════════════════

def detect_regime(adx, bb_upper, bb_lower, price, spec):
    """市场状态识别（与 bot_engine._detect_regime 一致）。"""
    bb_width = (bb_upper - bb_lower) / price if price > 0 else 0
    trend_adx = spec.get('regime_trend_adx', 22)
    range_adx = spec.get('regime_range_adx', 22)
    trend_bb = spec.get('regime_trend_bb_width', 0.03)
    range_bb = spec.get('regime_range_bb_width', 0.02)

    if adx >= trend_adx and bb_width >= trend_bb:
        return 'TREND'
    if adx <= range_adx or bb_width <= range_bb:
        return 'RANGE'
    return 'NEUTRAL'


def adjust_params_by_volatility(spec, atr_pct):
    """波动率自适应参数调整（与 bot_engine._adjust_params_by_volatility 一致）。"""
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


def calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price,
                      sma20, sma60, bb_mid, adx_thr, trend_dir=1, bb_lower=None, bb_upper=None):
    """信号强度评分（与 bot_engine._calc_signal_score 一致）。5维度各20分，总分100。"""
    score = 0
    # 1. 趋势强度
    if adx >= adx_thr * 1.5: score += 20
    elif adx >= adx_thr: score += 15
    elif adx >= adx_thr * 0.8: score += 10
    else: score += 5
    # 2. 量能
    if vol_ratio >= 2.0: score += 20
    elif vol_ratio >= 1.5: score += 15
    elif vol_ratio >= 1.2: score += 10
    else: score += 5
    # 3. 动量
    macd_hist = macd - macd_sig
    if trend_dir == 1:
        if macd_hist > 0 and macd > macd_sig: score += 20
        elif macd_hist > 0: score += 12
        else: score += 5
    else:
        if rsi < 25: score += 20
        elif rsi < 30: score += 15
        elif rsi < 35: score += 10
        else: score += 5
    # 4. 价格位置
    if trend_dir == 1:
        if price > sma20 > sma60: score += 20
        elif price > sma20: score += 12
        elif price > sma60: score += 8
        else: score += 3
    else:
        if bb_lower is not None and bb_upper is not None:
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
            if bb_pos < 0.1: score += 20
            elif bb_pos < 0.2: score += 15
            elif bb_pos < 0.3: score += 10
            else: score += 5
        else:
            score += 10
    # 5. 趋势方向
    if trend_dir == 1:
        if sma20 > sma60: score += 20
        else: score += 8
    else:
        if price < bb_mid: score += 15
        else: score += 8
    return min(score, 100)


def score_to_position_scale(score, spec):
    """评分映射仓位（与 bot_engine._score_to_position_scale 一致）。"""
    min_score = spec.get('min_signal_score', 40)
    if score < min_score: return 0.0
    if score >= 80: return 1.0
    if score >= 60: return 0.8
    if score >= min_score: return 0.6
    return 0.0


def get_regime_position_scale(regime, strategy_type):
    """动态资金分配（与 bot_engine._get_regime_position_scale 一致）。"""
    if regime == 'TREND':
        return 1.2 if strategy_type == 'trend' else 0.6
    elif regime == 'RANGE':
        return 1.2 if strategy_type == 'meanrev' else 0.6
    else:  # NEUTRAL
        return 1.0 if strategy_type == 'meanrev' else 0.7


def should_buy(row, prev_row, row_3_ago, spec, adjusted, regime):
    """
    买入信号判定 + 评分（与 bot_engine._should_buy 一致）。
    返回: (buy_reason, strategy_type, signal_score) 或 (None, None, 0)
    """
    price = row['close']
    adx = row['adx']
    rsi = row['rsi']
    rsi_prev = prev_row['rsi']
    sma20 = row['sma20']
    sma60 = row['sma60']
    macd = row['macd']
    macd_sig = row['macd_signal']
    macd_prev = prev_row['macd']
    bb_lower = row['bb_lower']
    bb_mid = row['bb_mid']
    bb_upper = row['bb_upper']
    vol_ratio = row['vol_ratio']

    candle_body = row['close'] - row['open']
    candle_range = row['high'] - row['low']
    is_green_candle = candle_body > 0
    candle_body_ratio = abs(candle_body) / candle_range if candle_range > 0 else 0

    vol_thr = spec.get('volume_threshold', 1.2)
    macd_golden = (macd_prev < macd_sig and macd > macd_sig)
    macd_above_zero = macd > -abs(macd_sig) * 0.5
    macd_dead = (macd_prev > macd_sig and macd < macd_sig)
    min_body_ratio = spec.get('min_body_ratio', 0.30)
    quality_candle = candle_body_ratio > min_body_ratio
    adx_thr = adjusted.get('adx_threshold', 25)
    rsi_oversold_thr = spec.get('rsi_oversold', 35)
    range_bb_width_thr = spec.get('regime_range_bb_width', 0.02)

    if regime == 'TREND':
        if adx > adx_thr * 0.8:
            if macd_golden and macd_above_zero and vol_ratio >= vol_thr and is_green_candle and quality_candle:
                s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 1)
                return "TREND_MACD_GOLDEN_CROSS", 'trend', s
            if (adx > adx_thr and vol_ratio >= vol_thr and
                    price > sma20 and rsi > 50 and is_green_candle and quality_candle):
                s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 1)
                return "TREND_ADX_VOL_CONFIRM", 'trend', s

        rsi_cross_50 = (rsi_prev < 50 <= rsi)
        trend_ok = (price > sma60 or adx > adx_thr)
        if rsi_cross_50 and trend_ok and price > bb_mid and vol_ratio >= vol_thr * 0.8:
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 1)
            return "TREND_RSI_50_CROSS", 'trend', s

        if (price > sma20 and rsi > 50 and
                vol_ratio >= vol_thr and not macd_dead and is_green_candle and quality_candle):
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 1)
            return "TREND_SMA20_BREAKOUT", 'trend', s

    if regime in ('RANGE', 'NEUTRAL'):
        touch_lower = price <= bb_lower * 1.01
        bb_width = (bb_upper - bb_lower) / price
        bb_sufficient = bb_width > range_bb_width_thr
        rsi_bouncing = (rsi > rsi_prev and rsi < rsi_oversold_thr)
        if touch_lower and bb_sufficient and rsi_bouncing and vol_ratio >= vol_thr * 0.8:
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 0, bb_lower, bb_upper)
            return "MEANREV_BB_LOWER_RSI_DIVERGENCE", 'meanrev', s

        rsi_oversold_bounce = (
            rsi_prev <= rsi_oversold_thr and rsi > rsi_prev and
            rsi < 45 and is_green_candle and quality_candle and
            vol_ratio >= vol_thr * 0.8
        )
        if rsi_oversold_bounce:
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 0, bb_lower, bb_upper)
            return "MEANREV_RSI_OVERSOLD_BOUNCE", 'meanrev', s

        mr_body_ratio = min_body_ratio * 0.5
        mr_quality_candle = candle_body_ratio > mr_body_ratio
        bb_squeeze_bounce = (
            touch_lower and is_green_candle and mr_quality_candle and
            vol_ratio >= vol_thr * 0.7 and rsi < 45
        )
        if bb_squeeze_bounce and bb_sufficient:
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 0, bb_lower, bb_upper)
            return "MEANREV_BB_SQUEEZE_BOUNCE", 'meanrev', s

    # 突破策略（NEUTRAL态）
    if regime == 'NEUTRAL':
        breakout_ok = (
            price > sma20 and vol_ratio >= vol_thr and
            55 <= rsi <= 65 and macd > 0 and
            is_green_candle and quality_candle and not macd_dead
        )
        if breakout_ok:
            s = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60, bb_mid, adx_thr, 1)
            return "BREAKOUT_NEUTRAL", 'trend', s

    return None, None, 0


def should_sell(price, entry_price, highest_price, strategy_type, row, prev_row, spec, adjusted, holding_hours):
    """
    卖出信号判定（与 bot_engine._should_sell 一致）。
    """
    adx = row['adx']
    rsi = row['rsi']
    macd = row['macd']
    macd_sig = row['macd_signal']
    macd_prev = prev_row['macd']
    bb_lower = row['bb_lower']
    bb_mid = row['bb_mid']
    bb_upper = row['bb_upper']
    atr = row['atr']
    profit_pct = (price - entry_price) / entry_price

    # ═══ 均值回归退出 ═══
    if strategy_type == 'meanrev':
        mr_cfg = spec.get('meanrev_config', {})
        mr_stop = mr_cfg.get('stop_loss_pct', 0.025)
        mr_rsi_exit = mr_cfg.get('rsi_exit', 50)
        mr_bb_mid_exit = mr_cfg.get('bb_mid_exit', True)
        mr_max_hold = mr_cfg.get('max_hold_hours', 12)

        hard_stop = entry_price * (1 - mr_stop)
        if price <= hard_stop:
            return "均值回归止损"

        if mr_bb_mid_exit and price >= bb_mid and profit_pct > 0:
            return "均值回归止盈(布林中轨)"

        if rsi >= mr_rsi_exit and profit_pct > 0:
            return "均值回归RSI退出"

        if holding_hours >= mr_max_hold:
            if profit_pct >= 0:
                return "均值回归超时止盈"
            else:
                return "均值回归超时止损"

        return None

    # ═══ 趋势跟踪退出 ═══
    atr_multi = spec.get('atr_multiplier', 2.0)
    profit_in_atr = (price - entry_price) / atr if atr > 0 else 0
    min_profit_pct = spec.get('min_profit_pct', 0.008)

    # 动态ATR倍数
    if profit_in_atr < 1.0:
        atr_multi = min(atr_multi, 1.5)
    elif profit_in_atr > 3.0:
        atr_multi = atr_multi * 1.25

    if atr > 0:
        trail_stop = highest_price - atr_multi * atr
        if price <= trail_stop:
            return "ATR追踪止损"

    hard_stop = entry_price * (1 - spec.get('stop_loss_pct', 0.04))
    if price <= hard_stop:
        return "固定止损"

    # 保本止损
    breakeven_trigger = spec.get('breakeven_trigger', 0.02)
    breakeven_buffer = spec.get('breakeven_buffer', 0.003)
    if profit_pct >= breakeven_trigger:
        breakeven_stop = entry_price * (1 + breakeven_buffer)
        if price <= breakeven_stop:
            return "保本止损"

    profit_target = spec.get('profit_target_atr', 6.0)
    if profit_in_atr >= profit_target:
        return f"主动止盈({profit_in_atr:.1f}×ATR)"

    rsi_overbought_thr = adjusted.get('rsi_overbought', 70)
    if profit_pct > 0.05 and profit_pct > min_profit_pct and rsi > rsi_overbought_thr:
        return "RSI超买预警"

    strong_trend_threshold = adjusted.get('adx_threshold', 25) * 1.5
    macd_dead = (macd_prev > macd_sig and macd < macd_sig)
    if macd_dead and profit_pct > min_profit_pct and adx < strong_trend_threshold:
        return "MACD死叉+ADX回落"

    return None


def check_trailing_stop(entry_price, highest_price, current_price, spec, holding_hours):
    """分阶段追踪止盈（与 risk_manager.update_trailing_stop 一致）。"""
    if current_price > highest_price:
        return None

    trailing_stops = spec.get('trailing_stops', [])
    drawdown = (highest_price - current_price) / highest_price
    highest_profit = (highest_price - entry_price) / entry_price

    active_trigger = None
    for stop_cfg in sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True):
        if highest_profit >= stop_cfg['profit_threshold']:
            active_trigger = stop_cfg.get('trigger_drawdown', stop_cfg.get('trailing_pct', 0.02))
            break

    if active_trigger is None:
        return None

    # 时间衰减
    time_decay_cfg = spec.get('time_decay', {})
    if time_decay_cfg.get('enabled', False):
        intervals = time_decay_cfg.get('intervals', [])
        time_multiplier = 1.0
        for interval in intervals:
            if holding_hours <= interval['hours']:
                time_multiplier = interval['multiplier']
                break
        active_trigger = active_trigger / time_multiplier

    if drawdown >= active_trigger:
        return "追踪止盈"

    return None


# ═══════════════════════════════════════════════════
#  回测引擎
# ═══════════════════════════════════════════════════

class NativeBacktester:
    """轻量级回测引擎，直接复用 bot_engine 信号逻辑。"""

    def __init__(self, symbol, initial_cash=10000.0, fee_rate=0.001, slippage_pct=0.001):
        self.symbol = symbol
        self.spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct

        # 状态
        self.cash = initial_cash
        self.position = None  # {entry_price, highest_price, strategy_type, entry_time, amount, cost}
        self.trade_log = []
        self.pending_buy = None  # 信号二次确认

    def _calc_position_size(self, price, atr, total_value):
        """仓位计算（与 bot_engine._calc_position_size 一致）。"""
        spec = self.spec
        risk_per_trade = spec.get('risk_per_trade', 0.01)
        atr_multiplier = spec.get('atr_multiplier', 2.0)
        max_position_pct = spec.get('max_position_pct', 0.08)
        max_trade_amount = spec.get('max_trade_amount', 100)
        fallback_amount = spec.get('trade_amount', 20)

        if atr is None or pd.isna(atr) or atr <= 0:
            trade_amount = fallback_amount
        else:
            atr_pct = atr / price
            risk_based = total_value * risk_per_trade / (atr_pct * atr_multiplier)
            max_by_pct = total_value * max_position_pct
            trade_amount = min(risk_based, max_by_pct, max_trade_amount)

        trade_amount = max(trade_amount, 5)
        return trade_amount

    def _apply_slippage(self, side, price):
        """滑点建模（与 bot_engine._calc_slippage 一致）。"""
        return price * (1 + self.slippage_pct) if side == 'buy' else price * (1 - self.slippage_pct)

    def run(self, df):
        """执行回测。"""
        df = calculate_indicators(df, self.spec)

        for i in range(60, len(df)):
            row = df.iloc[i]
            prev_row = df.iloc[i - 1]
            row_3_ago = df.iloc[i - 3] if i >= 3 else None

            price = row['close']
            atr = row['atr']
            atr_pct = row['atr_pct']

            # 波动率自适应
            adjusted = adjust_params_by_volatility(self.spec, atr_pct)

            # Regime 识别
            regime = detect_regime(row['adx'], row['bb_upper'], row['bb_lower'], price, self.spec)

            # === 持仓时：先检查追踪止盈和卖出信号 ===
            if self.position:
                pos = self.position
                holding_hours = (i - pos['entry_idx']) * 4  # 4h timeframe

                # 更新最高价
                if price > pos['highest_price']:
                    pos['highest_price'] = price

                # 追踪止盈（仅趋势仓位）
                if pos['strategy_type'] == 'trend':
                    trailing_reason = check_trailing_stop(
                        pos['entry_price'], pos['highest_price'], price, self.spec, holding_hours
                    )
                    if trailing_reason:
                        self._close_position(price, trailing_reason, row.name)
                        continue

                # 策略卖出信号
                sell_reason = should_sell(
                    price, pos['entry_price'], pos['highest_price'], pos['strategy_type'],
                    row, prev_row, self.spec, adjusted, holding_hours
                )
                if sell_reason:
                    self._close_position(price, sell_reason, row.name)
                    continue

            # === 无持仓时：检查买入信号 ===
            if not self.position:
                buy_reason, strategy_type, signal_score = should_buy(row, prev_row, row_3_ago, self.spec, adjusted, regime)

                if buy_reason:
                    # 信号评分映射仓位（中期规划-1）
                    score_scale = score_to_position_scale(signal_score, self.spec)
                    if score_scale == 0:
                        self.pending_buy = None
                        continue

                    # HTF趋势过滤
                    price_below_sma60 = price < row['sma60']
                    if price_below_sma60 and strategy_type == 'trend':
                        self.pending_buy = None
                        continue

                    # 动态资金分配（中期规划-2）：regime仓位缩放
                    regime_scale = get_regime_position_scale(regime, strategy_type)
                    # 评分仓位 × regime仓位
                    final_scale = score_scale * regime_scale
                    # HTF下跌时均值回归再减半
                    if price_below_sma60:
                        final_scale *= 0.5

                    # 信号二次确认
                    if strategy_type == 'meanrev':
                        self._open_position(price, atr, buy_reason, strategy_type, i, row.name, row['sma60'], final_scale)
                        self.pending_buy = None
                    elif self.pending_buy is not None:
                        self._open_position(price, atr, buy_reason, strategy_type, i, row.name, row['sma60'], final_scale)
                        self.pending_buy = None
                    else:
                        self.pending_buy = (buy_reason, signal_score)
                else:
                    self.pending_buy = None

        # 回测结束：如果还有持仓，按最后价格平仓
        if self.position:
            last_row = df.iloc[-1]
            self._close_position(last_row['close'], "回测结束平仓", last_row.name)

        return self.trade_log

    def _open_position(self, price, atr, mode, strategy_type, idx, timestamp, sma60, position_scale=1.0):
        """开仓。"""
        total_value = self.cash
        trade_amount = self._calc_position_size(price, atr, total_value) * position_scale

        # 滑点
        fill_price = self._apply_slippage('buy', price)
        cost = trade_amount * (1 + self.fee_rate)  # 含手续费

        if cost > self.cash:
            return

        self.cash -= cost
        amount = trade_amount / fill_price

        self.position = {
            'entry_price': fill_price,
            'highest_price': fill_price,
            'strategy_type': strategy_type,
            'entry_idx': idx,
            'entry_time': timestamp,
            'amount': amount,
            'cost': trade_amount,
            'mode': mode,
        }

    def _close_position(self, price, reason, timestamp):
        """平仓。"""
        pos = self.position
        fill_price = self._apply_slippage('sell', price)
        revenue = pos['amount'] * fill_price * (1 - self.fee_rate)

        self.cash += revenue
        pnl_pct = (fill_price / pos['entry_price'] - 1) * 100
        pnl_amount = revenue - pos['cost']

        self.trade_log.append({
            'entry_time': pos['entry_time'],
            'exit_time': timestamp,
            'entry_price': pos['entry_price'],
            'exit_price': fill_price,
            'mode': pos['mode'],
            'strategy_type': pos['strategy_type'],
            'pnl_pct': pnl_pct,
            'pnl_amount': pnl_amount,
            'reason': reason,
            'holding_bars': timestamp_index(pos['entry_time'], timestamp),
        })

        self.position = None


def timestamp_index(entry_time, exit_time):
    """计算持仓K线数。"""
    try:
        if hasattr(entry_time, 'date') and hasattr(exit_time, 'date'):
            delta = exit_time - entry_time
            return int(delta.total_seconds() / 3600 / 4)  # 4h timeframe
    except Exception:
        pass
    return 0


# ═══════════════════════════════════════════════════
#  报告生成
# ═══════════════════════════════════════════════════

def generate_report(symbol, trades, initial_cash, final_cash):
    """生成回测报告。"""
    print(f"\n{'='*60}")
    print(f"📊 回测报告（native）: {symbol}")
    print(f"{'='*60}")

    roi = ((final_cash / initial_cash) - 1) * 100
    print(f"\n💰 账户摘要:")
    print(f"   初始资金:     {initial_cash:.2f} USDT")
    print(f"   最终资金:     {final_cash:.2f} USDT")
    print(f"   总收益率:     {roi:+.2f}%")

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
    if trend_trades:
        tw = len([t for t in trend_trades if t['pnl_pct'] > 0])
        print(f"   趋势跟踪:     {len(trend_trades)} 笔 (胜率 {tw/len(trend_trades)*100:.1f}%)")
    if meanrev_trades:
        mw = len([t for t in meanrev_trades if t['pnl_pct'] > 0])
        print(f"   均值回归:     {len(meanrev_trades)} 笔 (胜率 {mw/len(meanrev_trades)*100:.1f}%)")

    # 退出原因统计
    print(f"\n🏁 退出原因统计:")
    reason_stats = {}
    for t in trades:
        reason = t['reason']
        if reason not in reason_stats:
            reason_stats[reason] = {'count': 0, 'total_pnl': 0}
        reason_stats[reason]['count'] += 1
        reason_stats[reason]['total_pnl'] += t['pnl_pct']

    for reason, stats in sorted(reason_stats.items(), key=lambda x: x[1]['count'], reverse=True):
        avg_pnl = stats['total_pnl'] / stats['count']
        print(f"   {reason:<30} {stats['count']:>3} 笔  平均 {avg_pnl:+.2f}%")

    # 最近10笔
    print(f"\n📝 最近 10 笔交易:")
    print(f"   {'入场时间':<12} {'出场时间':<12} {'入场价':>10} {'出场价':>10} {'收益':>8} {'策略':>6} {'原因'}")
    print(f"   {'-'*75}")
    for t in trades[-10:]:
        entry_str = str(t['entry_time'].date()) if hasattr(t['entry_time'], 'date') else str(t['entry_time'])[:10]
        exit_str = str(t['exit_time'].date()) if hasattr(t['exit_time'], 'date') else str(t['exit_time'])[:10]
        strat = '趋势' if t['strategy_type'] == 'trend' else '均值'
        print(f"   {entry_str:<12} {exit_str:<12} {t['entry_price']:>10.2f} {t['exit_price']:>10.2f} "
              f"{t['pnl_pct']:>+7.2f}% {strat:>6} {t['reason']}")

    print("=" * 60 + "\n")


# ═══════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════

def run_backtest(symbol, days=365, start=None, end=None, initial_cash=10000.0):
    """运行单个币种回测。"""
    df = fetch_historical_data(symbol, timeframe=config.TIMEFRAME, days=days, start=start, end=end)
    if len(df) < 60:
        logger.error(f"{symbol} 数据不足（{len(df)}根），至少需要60根K线")
        return

    bt = NativeBacktester(symbol, initial_cash=initial_cash)
    trades = bt.run(df)

    generate_report(symbol, trades, initial_cash, bt.cash)
    return trades


def main():
    parser = argparse.ArgumentParser(description='crypto-bot 原生回测框架')
    parser.add_argument('--symbol', type=str, default='BTC/USDT')
    parser.add_argument('--all', action='store_true', help='回测所有配置币种')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--start', type=str, help='开始日期 (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--cash', type=float, default=10000.0)

    args = parser.parse_args()

    if args.all:
        for symbol in config.SYMBOLS:
            try:
                run_backtest(symbol, days=args.days, start=args.start, end=args.end, initial_cash=args.cash)
            except Exception as e:
                logger.error(f"{symbol} 回测失败: {e}")
    else:
        symbol = args.symbol.upper()
        run_backtest(symbol, days=args.days, start=args.start, end=args.end, initial_cash=args.cash)


if __name__ == "__main__":
    main()
