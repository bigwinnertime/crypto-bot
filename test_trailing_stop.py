#!/usr/bin/env python3
"""
测试新的分阶段追踪止盈 + 时间衰减策略
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from risk_manager import RiskManager
import config
from datetime import datetime, timedelta

def test_trailing_stop_strategy():
    """测试追踪止盈策略"""
    print("🧪 开始测试分阶段追踪止盈策略...\n")
    
    # 创建风险管理器
    risk = RiskManager()
    
    # 模拟一个ETH持仓
    symbol = 'ETH/USDT'
    entry_price = 2000.0
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 手动创建持仓记录
    risk.state['positions'][symbol] = {
        'entry_price': entry_price,
        'amount': 0.01,
        'cost': 20,
        'highest_price': entry_price,
        'time': current_time
    }
    
    print(f"📊 模拟持仓: {symbol}")
    print(f"入场价: ${entry_price}")
    print(f"入场时间: {current_time}")
    print()
    
    # 测试场景1: 价格上涨到不同阶段
    test_scenarios = [
        {'price': 2060, 'desc': '上涨3% - 达到第一追踪门槛'},
        {'price': 2120, 'desc': '上涨6% - 达到第二追踪门槛'},
        {'price': 2180, 'desc': '上涨9% - 仍在第二阶段'},
        {'price': 2240, 'desc': '上涨12% - 达到第三追踪门槛'},
        {'price': 2280, 'desc': '上涨14% - 最高点'},
        {'price': 2236, 'desc': '回撤2% - 触发追踪止盈(时间衰减前)'},
    ]
    
    print("🎯 测试分阶段追踪止盈:")
    print("-" * 60)
    
    for scenario in test_scenarios:
        price = scenario['price']
        desc = scenario['desc']
        
        # 更新最高价
        if price > risk.state['positions'][symbol]['highest_price']:
            risk.state['positions'][symbol]['highest_price'] = price
        
        # 获取状态
        status = risk.get_trailing_stop_status(symbol, price)
        stop_reason = risk.update_trailing_stop(symbol, price)
        
        print(f"价格: ${price} | {desc}")
        print(f"  最高盈利: {status['highest_profit_pct']:+.2%}")
        print(f"  当前回撤: {status['current_drawdown_pct']:+.2%}")
        print(f"  活跃追踪比例: {status['active_trailing_pct']:.2%}" if status['active_trailing_pct'] else "  活跃追踪比例: 未激活")
        print(f"  调整后比例: {status['adjusted_trailing_pct']:.2%}" if status['adjusted_trailing_pct'] else "  调整后比例: N/A")
        print(f"  止盈信号: {stop_reason}" if stop_reason else "  止盈信号: 无")
        print()
    
    # 测试场景2: 时间衰减效果
    print("⏰ 测试时间衰减效果:")
    print("-" * 60)
    
    # 模拟不同持仓时间
    time_scenarios = [
        {'hours': 0.5, 'desc': '持仓0.5小时'},
        {'hours': 2, 'desc': '持仓2小时'},
        {'hours': 6, 'desc': '持仓6小时'},
        {'hours': 16, 'desc': '持仓16小时'},
        {'hours': 30, 'desc': '持仓30小时'},
    ]
    
    # 设置一个固定的盈利状态
    test_price = 2240  # 12%盈利
    risk.state['positions'][symbol]['highest_price'] = test_price
    
    for scenario in time_scenarios:
        hours = scenario['hours']
        desc = scenario['desc']
        
        # 修改入场时间来模拟不同持仓时长
        past_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        risk.state['positions'][symbol]['time'] = past_time
        
        status = risk.get_trailing_stop_status(symbol, test_price)
        
        print(f"{desc}")
        print(f"  原始追踪比例: {status['active_trailing_pct']:.2%}")
        print(f"  时间衰减系数: {status['time_multiplier']:.2f}")
        print(f"  调整后追踪比例: {status['adjusted_trailing_pct']:.2%}")
        print()
    
    # 测试场景3: 完整交易流程
    print("🔄 完整交易流程测试:")
    print("-" * 60)
    
    # 重置持仓
    risk.state['positions'][symbol] = {
        'entry_price': 2000.0,
        'amount': 0.01,
        'cost': 20,
        'highest_price': 2000.0,
        'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    price_sequence = [
        (2000, "入场"),
        (2060, "上涨3%"),
        (2120, "上涨6%"),
        (2180, "上涨9%"),
        (2240, "上涨12% - 最高点"),
        (2195, "回撤2% - 时间衰减后触发追踪止盈")
    ]
    
    for price, desc in price_sequence:
        if price > risk.state['positions'][symbol]['highest_price']:
            risk.state['positions'][symbol]['highest_price'] = price
        
        stop_reason = risk.update_trailing_stop(symbol, price)
        status = risk.get_trailing_stop_status(symbol, price)
        
        print(f"价格: ${price} | {desc}")
        if stop_reason:
            print(f"  🎯 {stop_reason}")
            break
        else:
            print(f"  继续持有 (追踪比例: {status['adjusted_trailing_pct']:.2%})")
    
    print("\n✅ 测试完成!")

if __name__ == "__main__":
    test_trailing_stop_strategy()
