import json
import pandas as pd
from datetime import datetime

class ReportGenerator:
    def __init__(self, state_file='bot_state.json'):
        self.state_file = state_file

    def get_performance_report(self):
        """生成完整的账户与历史分析报表"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
        except Exception as e:
            print(f"❌ 读取状态文件失败: {e}")
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
            df = pd.DataFrame(history)
            total_trades = len(df)
            wins = len(df[df['pnl_pct'] > 0])
            win_rate = (wins / total_trades) * 100
            total_pnl = df['pnl_amount'].sum()
            
            print(f"✅ 总成交: {total_trades} 笔")
            print(f"🏆 胜率: {win_rate:.2f}%")
            print(f"💰 累计净利润: {total_pnl:.2f} USDT")
            
            print("\n📝 最近 5 笔交易记录:")
            # 格式化输出最近5笔
            recent = df.tail(5)[['sell_time', 'symbol', 'pnl_pct', 'exit_reason']]
            # 美化时间显示，只留时分
            recent['sell_time'] = recent['sell_time'].apply(lambda x: x[-8:-3] if isinstance(x, str) else x)
            print(recent.to_string(index=False))

        print("="*40 + "\n")

# 使用示例
if __name__ == "__main__":
    reporter = ReportGenerator()
    reporter.get_performance_report()
