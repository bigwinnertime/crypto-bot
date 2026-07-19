"""
信号引擎模块 — 统一的策略信号逻辑。

bot_engine.py（实盘）和 backtest_native.py（回测）共同 import 此模块，
确保回测结果与实盘完全一致，消除 340+ 行重复代码和 6 处逻辑不一致。

公共 API:
  - detect_regime()              市场状态识别
  - adjust_params_by_volatility() 波动率自适应参数调整
  - should_buy()                 买入信号判定 + 评分
  - should_sell()                卖出信号判定
  - calc_signal_score()          信号强度评分
  - score_to_position_scale()    评分映射仓位
  - get_regime_position_scale()  动态资金分配
  - check_trailing_stop()        分阶段追踪止盈
  - calc_slippage()              滑点计算
"""
import copy
import logging

logger = logging.getLogger(__name__)


def detect_regime(adx, bb_upper, bb_lower, bb_mid, price, spec):
    """
    市场状态识别：根据 ADX 强度 + 布林带宽度判断当前为趋势态/震荡态/中性。
    返回: 'TREND' | 'RANGE' | 'NEUTRAL'

    RANGE 判定用 OR（ADX 弱 或 布林带收口），降低 NEUTRAL 占比。
    """
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
    """根据波动率动态调整参数。"""
    vol_adjust = spec.get('volatility_adjust', {})
    if not vol_adjust.get('enabled', False):
        return spec

    low_vol_threshold = vol_adjust.get('low_vol_threshold', 0.02)
    high_vol_threshold = vol_adjust.get('high_vol_threshold', 0.05)
    low_vol_multiplier = vol_adjust.get('low_vol_multiplier', 0.8)
    high_vol_multiplier = vol_adjust.get('high_vol_multiplier', 1.2)

    if atr_pct < low_vol_threshold:
        multiplier = low_vol_multiplier
    elif atr_pct > high_vol_threshold:
        multiplier = high_vol_multiplier
    else:
        return spec

    adjusted = copy.deepcopy(spec)
    if 'adx_threshold' in adjusted:
        adjusted['adx_threshold'] = spec['adx_threshold'] * multiplier
    if 'rsi_oversold' in adjusted:
        adjusted['rsi_oversold'] = spec['rsi_oversold'] * multiplier
    if 'rsi_overbought' in adjusted:
        adjusted['rsi_overbought'] = spec['rsi_overbought'] / multiplier
    return adjusted


def calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price,
                      sma20, sma60, bb_mid, adx_thr, trend_dir=1, bb_lower=None, bb_upper=None):
    """
    信号强度评分。5个维度各20分，总分100：
      1. 趋势强度（ADX）
      2. 量能确认
      3. 动量（MACD/RSI）
      4. 价格位置
      5. 趋势方向
    """
    score = 0

    # 1. 趋势强度 (0-20)
    if adx >= adx_thr * 1.5:
        score += 20
    elif adx >= adx_thr:
        score += 15
    elif adx >= adx_thr * 0.8:
        score += 10
    else:
        score += 5

    # 2. 量能确认 (0-20)
    if vol_ratio >= 2.0:
        score += 20
    elif vol_ratio >= 1.5:
        score += 15
    elif vol_ratio >= 1.2:
        score += 10
    else:
        score += 5

    # 3. 动量 (0-20)
    macd_hist = macd - macd_sig
    if trend_dir == 1:
        if macd_hist > 0 and macd > macd_sig:
            score += 20
        elif macd_hist > 0:
            score += 12
        else:
            score += 5
    else:
        if rsi < 25:
            score += 20
        elif rsi < 30:
            score += 15
        elif rsi < 35:
            score += 10
        else:
            score += 5

    # 4. 价格位置 (0-20)
    if trend_dir == 1:
        if price > sma20 > sma60:
            score += 20
        elif price > sma20:
            score += 12
        elif price > sma60:
            score += 8
        else:
            score += 3
    else:
        if bb_lower is not None and bb_upper is not None:
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
            if bb_pos < 0.1:
                score += 20
            elif bb_pos < 0.2:
                score += 15
            elif bb_pos < 0.3:
                score += 10
            else:
                score += 5
        else:
            score += 10

    # 5. 趋势方向一致性 (0-20)
    if trend_dir == 1:
        if sma20 > sma60:
            score += 20
        else:
            score += 8
    else:
        if price < bb_mid:
            score += 15
        else:
            score += 8

    return min(score, 100)


def score_to_position_scale(score, spec):
    """评分映射仓位：≥80满仓, 60-80八折, 40-60六折, <40放弃。"""
    min_score = spec.get('min_signal_score', 40)
    if score < min_score:
        return 0.0
    if score >= 80:
        return 1.0
    if score >= 60:
        return 0.8
    if score >= min_score:
        return 0.6
    return 0.0


def get_regime_position_scale(regime, strategy_type):
    """动态资金分配：根据 regime 和策略类型调整仓位。"""
    if regime == 'TREND':
        return 1.2 if strategy_type == 'trend' else 0.6
    elif regime == 'RANGE':
        return 1.2 if strategy_type == 'meanrev' else 0.6
    else:  # NEUTRAL
        return 1.0 if strategy_type == 'meanrev' else 0.7


def should_buy(price, adx, rsi, rsi_prev, rsi_3_ago,
               sma20, sma60,
               macd, macd_sig, macd_prev,
               bb_lower, bb_mid, bb_upper, vol_ratio, spec, adjusted,
               is_green_candle=True, candle_body_ratio=0.0, regime='NEUTRAL'):
    """
    Regime 自适应入场 + 信号强度评分。
    返回: (buy_reason, strategy_type, signal_score) 或 (None, None, 0)
    """
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
                score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                          bb_mid, adx_thr, trend_dir=1)
                return "TREND_MACD_GOLDEN_CROSS", 'trend', score
            if (adx > adx_thr and vol_ratio >= vol_thr and
                    price > sma20 and rsi > 50 and is_green_candle and quality_candle):
                score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                          bb_mid, adx_thr, trend_dir=1)
                return "TREND_ADX_VOL_CONFIRM", 'trend', score

        rsi_cross_50 = (rsi_prev < 50 <= rsi)
        trend_ok = (price > sma60 or adx > adx_thr)
        if rsi_cross_50 and trend_ok and price > bb_mid and vol_ratio >= vol_thr * 0.8:
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=1)
            return "TREND_RSI_50_CROSS", 'trend', score

        if (price > sma20 and rsi > 50 and
                vol_ratio >= vol_thr and not macd_dead and is_green_candle and quality_candle):
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=1)
            return "TREND_SMA20_BREAKOUT", 'trend', score

    if regime in ('RANGE', 'NEUTRAL'):
        touch_lower = price <= bb_lower * 1.01
        bb_width = (bb_upper - bb_lower) / price
        bb_sufficient = bb_width > range_bb_width_thr
        rsi_bouncing = (rsi > rsi_prev and rsi < rsi_oversold_thr)
        if touch_lower and bb_sufficient and rsi_bouncing and vol_ratio >= vol_thr * 0.8:
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=0, bb_lower=bb_lower, bb_upper=bb_upper)
            return "MEANREV_BB_LOWER_RSI_DIVERGENCE", 'meanrev', score

        rsi_oversold_bounce = (
            rsi_prev <= rsi_oversold_thr and rsi > rsi_prev and
            rsi < 45 and is_green_candle and quality_candle and
            vol_ratio >= vol_thr * 0.8
        )
        if rsi_oversold_bounce:
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=0, bb_lower=bb_lower, bb_upper=bb_upper)
            return "MEANREV_RSI_OVERSOLD_BOUNCE", 'meanrev', score

        mr_body_ratio = min_body_ratio * 0.5
        mr_quality_candle = candle_body_ratio > mr_body_ratio
        bb_squeeze_bounce = (
            touch_lower and is_green_candle and mr_quality_candle and
            vol_ratio >= vol_thr * 0.7 and rsi < 45
        )
        if bb_squeeze_bounce and bb_sufficient:
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=0, bb_lower=bb_lower, bb_upper=bb_upper)
            return "MEANREV_BB_SQUEEZE_BOUNCE", 'meanrev', score

    # 突破策略（NEUTRAL态）
    if regime == 'NEUTRAL':
        breakout_ok = (
            price > sma20 and vol_ratio >= vol_thr and
            55 <= rsi <= 65 and macd > 0 and
            is_green_candle and quality_candle and not macd_dead
        )
        if breakout_ok:
            score = calc_signal_score(adx, vol_ratio, rsi, macd, macd_sig, price, sma20, sma60,
                                      bb_mid, adx_thr, trend_dir=1)
            return "BREAKOUT_NEUTRAL", 'trend', score

    return None, None, 0


def should_sell(price, entry_price, highest_price, strategy_type, holding_hours,
                adx, rsi, macd, macd_sig, macd_prev,
                bb_lower, bb_mid, bb_upper, atr, spec, adjusted):
    """
    统一卖出决策树。根据持仓策略类型走不同退出逻辑。
    返回: sell_reason (str) 或 None
    """
    profit_pct = (price - entry_price) / entry_price

    # ═══ 均值回归退出（快进快出） ═══
    if strategy_type == 'meanrev':
        mr_cfg = spec.get('meanrev_config', {})
        mr_stop = mr_cfg.get('stop_loss_pct', 0.025)
        mr_rsi_exit = mr_cfg.get('rsi_exit', 50)
        mr_bb_mid_exit = mr_cfg.get('bb_mid_exit', True)
        mr_max_hold = mr_cfg.get('max_hold_hours', 12)

        hard_stop = entry_price * (1 - mr_stop)
        if price <= hard_stop:
            return f"均值回归止损"

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

    # ═══ 趋势跟踪退出（让利润奔跑） ═══
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
    """分阶段追踪止盈。"""
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


def calc_slippage(side, price, atr=None):
    """
    滑点计算（ATR自适应）。
    买入上浮（付出更高），卖出下浮（收到更低）。
    滑点 = max(ATR的5%, 固定0.1%)
    """
    import pandas as pd
    if atr is not None and not pd.isna(atr) and atr > 0:
        slippage = max(atr * 0.05, price * 0.001)
    else:
        slippage = price * 0.001
    if side == 'buy':
        return price + slippage
    else:
        return price - slippage
