import json
from datetime import datetime
import logging
import time

import config

logger = logging.getLogger(__name__)

class ReportGenerator:
    def __init__(self, state_file='bot_state.json'):
        self.state_file = state_file

    def _load_state_safe(self):
        """
        #5: 读 bot_state.json。RiskManager.save_state 已改为原子写入（os.replace），
        读到的要么是旧文件要么是新文件，不会半截。这里再加一层 JSONDecodeError 重试防御：
        极少数情况下 os.replace 与 open 撞在同一瞬间，重试一次即可读到完整新文件。
        """
        for attempt in range(3):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                if attempt < 2:
                    logger.warning(f"⚠️ 状态文件 JSON 解析失败（第{attempt+1}次），0.2s 后重试: {e}")
                    time.sleep(0.2)
                else:
                    raise
            except Exception as e:
                raise
        return None

    def get_performance_report(self):
        """生成完整的账户与历史分析报表"""
        try:
            state = self._load_state_safe()
            if state is None:
                return
        except Exception as e:
            logger.error(f"❌ 读取状态文件失败: {e}")
            return

        acc = state.get('virtual_account', {})
        positions = state.get('positions', {})
        history = state.get('trade_history', [])

        # --- 1. 账户实时摘要 ---
        current_balance = acc.get('balance', 0)
        # 计算在途仓位当前价值（用 current_price 计算市值，而非入场成本）
        pos_value = 0
        for symbol, p in positions.items():
            pos_amount = p.get('amount', 0)
            pos_price = p.get('current_price', p.get('entry_price', 0))
            pos_value += pos_amount * pos_price
        total_equity = current_balance + pos_value
        initial_balance = acc.get('initial_balance', 10000)
        total_roi = ((total_equity / initial_balance) - 1) * 100 if initial_balance > 0 else 0
        total_fees = acc.get('total_fees', 0)

        print("\n" + "="*40)
        print(f"💰 账户实时摘要 ({datetime.now().strftime('%m-%d %H:%M')})")
        print("="*40)
        print(f"💵 初始本金: {initial_balance:.2f} USDT")
        print(f"🏦 账户净值: {total_equity:.2f} USDT (ROI: {total_roi:.2f}%)")
        print(f"💳 可用余额: {current_balance:.2f} USDT")
        print(f"🛡️ 累计手续费: {total_fees:.2f} USDT")

        if total_fees > 0:
            fee_drag = (total_fees / initial_balance) * 100
            print(f"⚠️ 手续费损耗占比: {fee_drag:.2f}% (相对于初始本金)")
        
        # --- 2. 当前持仓 ---
        print("\n📦 当前持仓记录:")
        if not positions:
            print("   暂无持仓")
        else:
            for symbol, data in positions.items():
                entry_price = data.get('entry_price', 0)
                highest_price = data.get('highest_price', entry_price)
                current_price = data.get('current_price', entry_price)
                high_gain = (highest_price / entry_price - 1) * 100 if entry_price > 0 else 0
                unrealized_pnl = ((current_price / entry_price) - 1) * 100 if entry_price > 0 else 0
                pos_amount = data.get('amount', 0)
                pos_value = pos_amount * current_price
                strategy = '趋势' if data.get('strategy_type') == 'trend' else '均值'
                print(f"🔹 {symbol} [{strategy}]: 入场 {entry_price:.2f} | 现价 {current_price:.2f} | "
                      f"未实现盈亏 {unrealized_pnl:+.2f}% | 市值 {pos_value:.2f} | 最高涨幅 +{high_gain:.2f}%")

        # --- 3. 历史交易分析 ---
        print("\n📈 历史交易统计:")
        if not history:
            print("   暂无历史交易数据 (等待首笔平仓...)")
        else:
            # 标准化数据结构以适配不同的字段名
            normalized_history = []
            for trade in history:
                # 计算盈亏金额（仅当 pnl_amount 字段不存在时才回退计算）
                pnl_amount = trade.get('pnl_amount')
                if pnl_amount is None:
                    # 回退路径：用 entry_price 和 amount 估算（比 trade_amount 更准确）
                    entry_p = trade.get('entry_price', trade.get('entry', 0))
                    exit_p = trade.get('sell_price', trade.get('exit', 0))
                    trade_amt = trade.get('amount', 0)
                    # 估算 PnL = (出场价 - 入场价) × 数量 - 双边手续费
                    raw_pnl = (exit_p - entry_p) * trade_amt
                    fee_est = entry_p * trade_amt * config.FEE_RATE + exit_p * trade_amt * config.FEE_RATE if hasattr(config, 'FEE_RATE') else 0
                    pnl_amount = raw_pnl - fee_est
                
                normalized_trade = {
                    'symbol': trade.get('symbol', ''),
                    'entry_price': trade.get('entry_price', trade.get('entry', 0)),
                    'sell_price': trade.get('sell_price', trade.get('exit', 0)),
                    'amount': trade.get('amount', 0),
                    'pnl_pct': trade.get('pnl_pct', 0),
                    'pnl_amount': pnl_amount,
                    'exit_reason': trade.get('exit_reason', trade.get('reason', '')),
                    'sell_time': trade.get('sell_time', trade.get('time', ''))
                }
                normalized_history.append(normalized_trade)
            
            total_trades = len(normalized_history)
            wins = len([trade for trade in normalized_history if trade['pnl_pct'] > 0])
            losses = len([trade for trade in normalized_history if trade['pnl_pct'] < 0])
            win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
            total_pnl = sum(trade['pnl_amount'] for trade in normalized_history)
            
            # 计算平均盈亏
            avg_win = sum(trade['pnl_amount'] for trade in normalized_history if trade['pnl_pct'] > 0) / wins if wins > 0 else 0
            avg_loss = sum(trade['pnl_amount'] for trade in normalized_history if trade['pnl_pct'] < 0) / losses if losses > 0 else 0
            
            # 计算最大连续亏损次数
            consecutive_losses = 0
            max_consecutive_losses = 0
            for trade in normalized_history:
                if trade['pnl_pct'] < 0:
                    consecutive_losses += 1
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                else:
                    consecutive_losses = 0
            
            print(f"✅ 总成交: {total_trades} 笔")
            print(f"🏆 胜率: {win_rate:.2f}%")
            print(f"💰 累计净利润: {total_pnl:.2f} USDT")
            print(f"📊 平均盈利: {avg_win:.2f} USDT")
            print(f"📉 平均亏损: {avg_loss:.2f} USDT")
            print(f"🔥 最大连续亏损: {max_consecutive_losses} 笔")
            
            print("\n📝 最近 5 笔交易记录:")
            # 获取最近5笔交易
            recent_trades = normalized_history[-5:] if len(normalized_history) >= 5 else normalized_history
            print(f"{'时间':<8} {'币种':<12} {'收益率':<10} {'离场原因'}")
            print("-" * 50)
            for trade in recent_trades:
                time_str = trade['sell_time'][-8:-3] if isinstance(trade['sell_time'], str) and len(trade['sell_time']) > 5 else trade['sell_time']
                pnl_str = f"{trade['pnl_pct']:+.2f}%"
                print(f"{time_str:<8} {trade['symbol']:<12} {pnl_str:<10} {trade['exit_reason']}")

        print("="*40 + "\n")

# 使用示例
if __name__ == "__main__":
    reporter = ReportGenerator()
    reporter.get_performance_report()
