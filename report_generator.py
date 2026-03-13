import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ReportGenerator:
    def __init__(self, state_file='bot_state.json'):
        self.state_file = state_file

    def get_performance_report(self):
        """生成完整的账户与历史分析报表"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
        except Exception as e:
            logger.error(f"❌ 读取状态文件失败: {e}")
            return

        acc = state.get('virtual_account', {})
        positions = state.get('positions', {})
        history = state.get('trade_history', [])

        # --- 1. 账户实时摘要 ---
        current_balance = acc.get('balance', 0)
        # 计算在途仓位当前价值 (简单处理：以成本计，若想更准需要传入实时价)
        pos_value = sum(p.get('cost', 0) for p in positions.values())
        total_equity = current_balance + pos_value
        initial_balance = acc.get('initial_balance', 10000)
        total_roi = ((total_equity / initial_balance) - 1) * 100
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
                # 计算持仓期间最高涨幅
                high_gain = (data['highest_price'] / data['entry_price'] - 1) * 100
                print(f"🔹 {symbol}: 入场 {data['entry_price']:.2f} | 最高点涨幅 +{high_gain:.2f}%")

        # --- 3. 历史交易分析 ---
        print("\n📈 历史交易统计:")
        if not history:
            print("   暂无历史交易数据 (等待首笔平仓...)")
        else:
            # 标准化数据结构以适配不同的字段名
            normalized_history = []
            for trade in history:
                # 计算盈亏金额（如果不存在pnl_amount字段）
                pnl_amount = trade.get('pnl_amount', 0)
                if pnl_amount == 0:
                    # 使用固定的交易金额来计算盈亏（根据币种设定）
                    symbol = trade.get('symbol', '')
                    if 'BTC' in symbol:
                        trade_amount = 30  # BTC默认交易金额
                    elif 'ETH' in symbol:
                        trade_amount = 20  # ETH默认交易金额
                    else:
                        trade_amount = 10  # 其他币种默认交易金额
                    
                    # 根据收益率计算盈亏金额
                    pnl_pct = trade.get('pnl_pct', 0)
                    pnl_amount = trade_amount * (pnl_pct / 100)
                
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
