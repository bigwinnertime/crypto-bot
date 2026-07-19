"""
轻量级自建回测器（native backtester）— 复用 signal_engine 信号逻辑。

与 backtrader 版本的区别：
  1. 指标用 ta 库（与实盘 bot_engine 完全一致，无计算差异）
  2. 信号逻辑复用 signal_engine 模块（消除重复代码，确保与实盘一致）
  3. 无框架约束，向量化指标计算 + 逐K线事件驱动

用法:
    python backtest_native.py --symbol BTC/USDT --days 730
    python backtest_native.py --all --days 730
    python backtest_native.py --symbol ETH/USDT --start 2024-01-01 --end 2025-01-01
"""
import argparse
import logging
import os
import sys

import ccxt
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

import config
from signal_engine import (
    detect_regime,
    adjust_params_by_volatility,
    should_buy,
    should_sell,
    score_to_position_scale,
    get_regime_position_scale,
    check_trailing_stop,
    calc_slippage,
)

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
#  数据获取
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

    df['rsi'] = RSIIndicator(close).rsi()
    df['sma20'] = SMAIndicator(close, window=20).sma_indicator()
    df['sma60'] = SMAIndicator(close, window=60).sma_indicator()
    df['adx'] = ADXIndicator(high, low, close).adx()

    bb = BollingerBands(close, window=spec.get('bb_period', 20), window_dev=spec.get('bb_std', 2))
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_lower'] = bb.bollinger_lband()

    df['atr'] = AverageTrueRange(high, low, close, window=spec.get('atr_period', 14)).average_true_range()
    df['atr_pct'] = df['atr'] / close

    macd_ind = MACD(close)
    df['macd'] = macd_ind.macd()
    df['macd_signal'] = macd_ind.macd_signal()

    df['vol_ma'] = volume.rolling(spec.get('volume_ma_period', 20)).mean()
    df['vol_ratio'] = volume / df['vol_ma']

    return df


# ═══════════════════════════════════════════════════
#  DataFrame 行适配层（调用 signal_engine）
# ═══════════════════════════════════════════════════

def should_buy_from_row(row, prev_row, row_3_ago, spec, adjusted, regime):
    """从 DataFrame 行提取指标，调用 signal_engine.should_buy。"""
    rsi_3_ago = row_3_ago['rsi'] if row_3_ago is not None else None
    candle_body = row['close'] - row['open']
    candle_range = row['high'] - row['low']
    is_green = candle_body > 0
    candle_body_ratio = abs(candle_body) / candle_range if candle_range > 0 else 0
    return should_buy(
        price=row['close'], adx=row['adx'], rsi=row['rsi'],
        rsi_prev=prev_row['rsi'], rsi_3_ago=rsi_3_ago,
        sma20=row['sma20'], sma60=row['sma60'],
        macd=row['macd'], macd_sig=row['macd_signal'], macd_prev=prev_row['macd'],
        bb_lower=row['bb_lower'], bb_mid=row['bb_mid'], bb_upper=row['bb_upper'],
        vol_ratio=row['vol_ratio'], spec=spec, adjusted=adjusted,
        is_green_candle=is_green, candle_body_ratio=candle_body_ratio, regime=regime
    )


def should_sell_from_row(price, entry_price, highest_price, strategy_type, holding_hours,
                         row, prev_row, spec, adjusted):
    """从 DataFrame 行提取指标，调用 signal_engine.should_sell。"""
    return should_sell(
        price=price, entry_price=entry_price, highest_price=highest_price,
        strategy_type=strategy_type, holding_hours=holding_hours,
        adx=row['adx'], rsi=row['rsi'],
        macd=row['macd'], macd_sig=row['macd_signal'], macd_prev=prev_row['macd'],
        bb_lower=row['bb_lower'], bb_mid=row['bb_mid'], bb_upper=row['bb_upper'],
        atr=row['atr'], spec=spec, adjusted=adjusted
    )


# ═══════════════════════════════════════════════════
#  回测引擎
# ═══════════════════════════════════════════════════

class NativeBacktester:
    """轻量级回测引擎，复用 signal_engine 信号逻辑，确保与实盘一致。"""

    def __init__(self, symbol, initial_cash=10000.0, fee_rate=0.001):
        self.symbol = symbol
        self.spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate

        self.cash = initial_cash
        self.position = None
        self.trade_log = []
        self.pending_buy = None

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

            adjusted = adjust_params_by_volatility(self.spec, atr_pct)
            regime = detect_regime(row['adx'], row['bb_upper'], row['bb_lower'], row['bb_mid'], price, self.spec)

            # === 持仓时：先检查追踪止盈和卖出信号 ===
            if self.position:
                pos = self.position
                holding_hours = (i - pos['entry_idx']) * 4

                if price > pos['highest_price']:
                    pos['highest_price'] = price

                if pos['strategy_type'] == 'trend':
                    trailing_reason = check_trailing_stop(
                        pos['entry_price'], pos['highest_price'], price, self.spec, holding_hours
                    )
                    if trailing_reason:
                        self._close_position(price, trailing_reason, row.name)
                        continue

                sell_reason = should_sell_from_row(
                    price, pos['entry_price'], pos['highest_price'], pos['strategy_type'],
                    holding_hours, row, prev_row, self.spec, adjusted
                )
                if sell_reason:
                    self._close_position(price, sell_reason, row.name)
                    continue

            # === 无持仓时：检查买入信号 ===
            if not self.position:
                buy_reason, strategy_type, signal_score = should_buy_from_row(
                    row, prev_row, row_3_ago, self.spec, adjusted, regime
                )

                if buy_reason:
                    score_scale = score_to_position_scale(signal_score, self.spec)
                    if score_scale == 0:
                        self.pending_buy = None
                        continue

                    price_below_sma60 = price < row['sma60']
                    if price_below_sma60 and strategy_type == 'trend':
                        self.pending_buy = None
                        continue

                    regime_scale = get_regime_position_scale(regime, strategy_type)
                    final_scale = score_scale * regime_scale
                    if price_below_sma60:
                        final_scale *= 0.5

                    if strategy_type == 'meanrev':
                        self._open_position(price, atr, buy_reason, strategy_type, i, row.name, final_scale)
                        self.pending_buy = None
                    elif self.pending_buy is not None:
                        self._open_position(price, atr, buy_reason, strategy_type, i, row.name, final_scale)
                        self.pending_buy = None
                    else:
                        self.pending_buy = (buy_reason, signal_score)
                else:
                    self.pending_buy = None

        if self.position:
            last_row = df.iloc[-1]
            self._close_position(last_row['close'], "回测结束平仓", last_row.name)

        return self.trade_log

    def _open_position(self, price, atr, mode, strategy_type, idx, timestamp, position_scale=1.0):
        """开仓。"""
        total_value = self.cash
        trade_amount = self._calc_position_size(price, atr, total_value) * position_scale
        fill_price = calc_slippage('buy', price, atr)
        cost = trade_amount * (1 + self.fee_rate)

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
        fill_price = calc_slippage('sell', price)
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
            'holding_bars': _timestamp_index(pos['entry_time'], timestamp),
        })

        self.position = None


def _timestamp_index(entry_time, exit_time):
    """计算持仓K线数。"""
    try:
        if hasattr(entry_time, 'date') and hasattr(exit_time, 'date'):
            delta = exit_time - entry_time
            return int(delta.total_seconds() / 3600 / 4)
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

    max_consec_loss = 0
    cur_consec = 0
    for t in trades:
        if t['pnl_pct'] <= 0:
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0

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
