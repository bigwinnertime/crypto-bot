import json
import re
import pandas as pd
from datetime import datetime

class ReportGenerator:
    def __init__(self, log_file='bot_main.log', state_file='bot_state.json'):
        self.log_file = log_file
        self.state_file = state_file

    def get_performance_report(self):
        with open(self.state_file, 'r') as f:
            state = json.load(f)
            
        acc = state.get('virtual_account', {})
        positions = state.get('positions', {})
        
        # 计算账户总值
        current_balance = acc.get('balance', 0)
        pos_value = sum(p.get('cost', 0) for p in positions.values()) # 以成本价计
        total_equity = current_balance + pos_value
        
        roi = ((total_equity / acc.get('initial_balance', 10000)) - 1) * 100
        total_fees = acc.get('total_fees', 0)
        
        print("\n" + "💰 虚拟账户详细报表 " + "="*20)
        print(f"💵 初始本金: {acc.get('initial_balance'):.2f} USDT")
        print(f"🏦 可用余额: {current_balance:.2f} USDT")
        print(f"📦 在途仓位成本: {pos_value:.2f} USDT")
        print(f"🛡️ 累计手续费支出: {total_fees:.2f} USDT")  # --- 展示手续费 ---
        print("-" * 30)
        print(f"📈 累计净盈亏 (扣费后): {acc.get('total_pnl'):.2f} USDT")
        print(f"🚀 账户总净值: {total_equity:.2f} USDT")
        print(f"📊 总收益率 (ROI): {roi:.2f}%")
        print(f"🔢 交易动作总数: {acc.get('trade_count')} 次")
        
        if total_fees > 0:
            fee_drag = (total_fees / acc.get('initial_balance')) * 100
            print(f"⚠️ 手续费损耗占比: {fee_drag:.2f}% (相对于本金)")
        print("="*42)

    def parse_trades_from_log(self):
        """从日志中提取买入和卖出记录"""
        trades = []
        # 正则表达式匹配日志中的成交信息
        # 匹配示例: 🔻 卖出成交: BTC/USDT 价格: 65000.0 本次盈亏: 2.50%
        pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*(买入|卖出|离场)通知: ([\w/]+).*价格: ([\d.]+)"
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    match = re.search(pattern, line)
                    if match:
                        trades.append({
                            'time': match.group(1),
                            'type': match.group(2),
                            'symbol': match.group(3),
                            'price': float(match.group(4))
                        })
        except FileNotFoundError:
            print("❌ 未找到日志文件，请确保机器人已运行并产生记录。")
        return pd.DataFrame(trades)

    def get_current_summary(self):
        """获取当前账户和持仓摘要"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            print("\n" + "="*40)
            print(f"📊 账户实时摘要 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
            print("="*40)
            
            fused_status = "🔴 已熔断" if state.get('is_fused') else "🟢 运行中"
            print(f"系统状态: {fused_status}")
            
            positions = state.get('positions', {})
            if not positions:
                print("当前持仓: 无")
            else:
                for symbol, data in positions.items():
                    print(f"🔹 {symbol}: 入场价 {data['entry_price']} | 最高价 {data.get('highest_price', 'N/A')}")
            print("="*40)
        except Exception as e:
            print(f"读取状态失败: {e}")

    def analyze_performance(self, df):
        """计算历史胜率和盈亏比"""
        if df.empty:
            print("暂无历史交易数据。")
            return

        print("\n📈 历史交易统计:")
        # 这里逻辑可以进一步根据买卖对匹配计算 PnL
        # 简单展示最近 5 次动作
        print(df.tail(5).to_string(index=False))
        
        counts = df['symbol'].value_counts()
        print(f"\n交易最频繁的币种: {counts.idxmax()} ({counts.max()}次动作)")

if __name__ == "__main__":
    reporter = ReportGenerator()
    reporter.get_current_summary()
    df_trades = reporter.parse_trades_from_log()
    reporter.analyze_performance(df_trades)
