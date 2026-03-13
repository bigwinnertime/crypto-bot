#!/usr/bin/env python3
"""
测试分离式追踪止盈策略
- 盈利阈值和回撤触发条件分离设置
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from risk_manager import RiskManager
from datetime import datetime, timedelta

def test_separated_trailing_stop():
    """测试分离式追踪止盈策略"""
    print("🧪 开始测试分离式追踪止盈策略...\n")
    
    # 创建风险管理器
    risk = RiskManager()
    
    # 模拟ETH配置：盈利3%开启追踪，回撤2%触发卖出
    test_config = {
        'ETH/USDT': {
            'trailing_stops': [
                {
                    'profit_threshold': 0.03,      # 盈利3%时开启追踪
                    'trigger_drawdown': 0.02,     # 回撤2%时触发卖出
                    'trailing_pct': 0.02          # 向后兼容
                },
                {
                    'profit_threshold': 0.06,      # 盈利6%时开启追踪
                    'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
                    'trailing_pct': 0.025
                },
                {
                    'profit_threshold': 0.12,      # 盈利12%时开启追踪
                    'trigger_drawdown': 0.03,     # 回撤3%时触发卖出
                    'trailing_pct': 0.03
                },
            ],
            'time_decay': {
                'enabled': True,
                'intervals': [
                    {'hours': 1, 'multiplier': 1.0},
                    {'hours': 4, 'multiplier': 0.8},
                    {'hours': 12, 'multiplier': 0.6},
                    {'hours': 24, 'multiplier': 0.5},
                    {'hours': float('inf'), 'multiplier': 0.4}
                ]
            }
        }
    }
    
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
    
    # 手动设置运行时配置
    if 'runtime_config' not in risk.state:
        risk.state['runtime_config'] = {}
    risk.state['runtime_config'][symbol] = test_config[symbol]
    
    print(f"📊 模拟持仓: {symbol}")
    print(f"入场价: ${entry_price}")
    print(f"策略: 盈利3%开启追踪，回撤2%触发卖出")
    print()
    
    # 测试场景1: 分离式触发条件验证
    test_scenarios = [
        {'price': 2060, 'desc': '上涨3% - 刚好开启追踪'},
        {'price': 2070, 'desc': '上涨3.5% - 追踪已激活'},
        {'price': 2030, 'desc': '回撤1.5% - 未达到2%触发线'},
        {'price': 2020, 'desc': '回撤2% - 刚好触发卖出！'},
    ]
    
    print("🎯 测试分离式触发条件:")
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
        print(f"  触发回撤阈值: {status['active_trigger_drawdown']:.2%}" if status['active_trigger_drawdown'] else "  触发回撤阈值: 未激活")
        print(f"  调整后阈值: {status['adjusted_trigger_drawdown']:.2%}" if status['adjusted_trigger_drawdown'] else "  调整后阈值: N/A")
        print(f"  止盈信号: {stop_reason}" if stop_reason else "  止盈信号: 无")
        print()
        
        if stop_reason:
            print("✅ 成功触发分离式追踪止盈！")
            break
    
    # 测试场景2: 不同阶段的独立设置
    print("🔄 测试不同阶段的独立设置:")
    print("-" * 60)
    
    # 重置持仓
    risk.state['positions'][symbol] = {
        'entry_price': 2000.0,
        'amount': 0.01,
        'cost': 20,
        'highest_price': 2000.0,
        'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    stage_tests = [
        {'profit': 0.04, 'drawdown': 0.022, 'desc': '4%盈利，2.2%回撤 - 应该触发（>2%）'},
        {'profit': 0.07, 'drawdown': 0.024, 'desc': '7%盈利，2.4%回撤 - 不应该触发（<2.5%）'},
        {'profit': 0.13, 'drawdown': 0.031, 'desc': '13%盈利，3.1%回撤 - 应该触发（>3%）'},
    ]
    
    for i, test in enumerate(stage_tests):
        # 重置状态
        risk.state['positions'][symbol]['highest_price'] = entry_price * (1 + test['profit'])
        
        # 模拟回撤
        current_price = risk.state['positions'][symbol]['highest_price'] * (1 - test['drawdown'])
        
        status = risk.get_trailing_stop_status(symbol, current_price)
        stop_reason = risk.update_trailing_stop(symbol, current_price)
        
        print(f"测试 {i+1}: {test['desc']}")
        print(f"  触发阈值: {status['active_trigger_drawdown']:.2%}")
        print(f"  实际回撤: {status['current_drawdown_pct']:.2%}")
        print(f"  结果: {'✅ 触发' if stop_reason else '❌ 未触发'}")
        print()
    
    # 测试场景3: 时间衰减对分离式设置的影响
    print("⏰ 测试时间衰减对分离式设置的影响:")
    print("-" * 60)
    
    # 设置固定盈利状态
    risk.state['positions'][symbol]['highest_price'] = 2240  # 12%盈利
    
    time_scenarios = [
        {'hours': 0.5, 'desc': '持仓0.5小时 - 无衰减'},
        {'hours': 6, 'desc': '持仓6小时 - 40%衰减'},
        {'hours': 30, 'desc': '持仓30小时 - 60%衰减'},
    ]
    
    for scenario in time_scenarios:
        hours = scenario['hours']
        desc = scenario['desc']
        
        # 修改入场时间
        past_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        risk.state['positions'][symbol]['time'] = past_time
        
        status = risk.get_trailing_stop_status(symbol, 2240)
        
        print(f"{desc}")
        print(f"  原始触发阈值: {status['active_trigger_drawdown']:.2%}")
        print(f"  时间衰减系数: {status['time_multiplier']:.2f}")
        print(f"  调整后触发阈值: {status['adjusted_trigger_drawdown']:.2%}")
        print()
    
    print("✅ 分离式追踪止盈测试完成!")

if __name__ == "__main__":
    test_separated_trailing_stop()
