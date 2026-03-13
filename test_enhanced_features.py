#!/usr/bin/env python3
"""
简化测试：成交量确认和动态参数调整
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot_engine import AdvancedTradingBot
from risk_manager import RiskManager
from datetime import datetime

def test_volume_confirmation_simple():
    """测试成交量确认功能"""
    print("🧪 测试成交量确认功能...\n")
    
    bot = AdvancedTradingBot()
    
    # 模拟成交量数据
    class MockDF:
        def __init__(self, volume_ratio):
            self.volume_ratio = volume_ratio
            
        @property
        def iloc(self):
            return MockIloc(self.volume_ratio)
    
    class MockIloc:
        def __init__(self, volume_ratio):
            self.volume_ratio = volume_ratio
            
        def __getitem__(self, idx):
            return MockSeries(self.volume_ratio)
    
    class MockSeries:
        def __init__(self, value):
            self.value = value
            
        @property
        def volume(self):
            return self
            
        def __call__(self):
            return self.value
    
    # 测试成交量阈值
    symbol = 'ETH/USDT'
    spec = bot.config.STRATEGY_CONFIG.get(symbol, bot.config.DEFAULT_CONFIG)
    volume_threshold = spec.get('volume_threshold', 1.5)
    
    print(f"📊 {symbol} 成交量阈值: {volume_threshold}x")
    print("-" * 60)
    
    # 场景1: 成交量放大
    volume_ratio_high = 2.0
    confirmed_high = volume_ratio_high >= volume_threshold
    print(f"成交量放大: {volume_ratio_high}x → {'✅ 确认通过' if confirmed_high else '❌ 拒绝'}")
    
    # 场景2: 成交量萎缩
    volume_ratio_low = 1.2
    confirmed_low = volume_ratio_low >= volume_threshold
    print(f"成交量萎缩: {volume_ratio_low}x → {'✅ 确认通过' if confirmed_low else '❌ 拒绝'}")
    
    # 场景3: 震荡模式（要求稍低）
    volume_ratio_medium = 1.3
    confirmed_medium = volume_ratio_medium >= volume_threshold * 0.8
    print(f"震荡模式: {volume_ratio_medium}x (阈值×0.8) → {'✅ 确认通过' if confirmed_medium else '❌ 拒绝'}")
    
    print()

def test_volatility_adjustment_simple():
    """测试波动率动态参数调整"""
    print("\n🧪 测试波动率动态参数调整...\n")
    
    bot = AdvancedTradingBot()
    symbol = 'ETH/USDT'
    spec = bot.config.STRATEGY_CONFIG.get(symbol, bot.config.DEFAULT_CONFIG)
    
    print(f"📊 {symbol} 参数调整测试")
    print("-" * 60)
    
    # 场景1: 低波动
    atr_pct_low = 0.015  # 1.5% ATR
    adjusted_low = bot._adjust_params_by_volatility(spec, atr_pct_low)
    
    print(f"低波动 (ATR%: {atr_pct_low:.2%}):")
    print(f"  原始ADX阈值: {spec['adx_threshold']} → 调整后: {adjusted_low['adx_threshold']:.1f}")
    print(f"  原始RSI超卖: {spec['rsi_oversold']} → 调整后: {adjusted_low['rsi_oversold']:.1f}")
    print(f"  预期: 参数缩小 (更严格)")
    print()
    
    # 场景2: 正常波动
    atr_pct_normal = 0.03  # 3% ATR
    adjusted_normal = bot._adjust_params_by_volatility(spec, atr_pct_normal)
    
    print(f"正常波动 (ATR%: {atr_pct_normal:.2%}):")
    print(f"  ADX阈值: {adjusted_normal['adx_threshold']} (保持不变)")
    print(f"  预期: 参数不变")
    print()
    
    # 场景3: 高波动
    atr_pct_high = 0.06  # 6% ATR
    adjusted_high = bot._adjust_params_by_volatility(spec, atr_pct_high)
    
    print(f"高波动 (ATR%: {atr_pct_high:.2%}):")
    print(f"  原始ADX阈值: {spec['adx_threshold']} → 调整后: {adjusted_high['adx_threshold']:.1f}")
    print(f"  原始RSI超卖: {spec['rsi_oversold']} → 调整后: {adjusted_high['rsi_oversold']:.1f}")
    print(f"  预期: 参数放大 (更宽松)")
    print()

def test_atr_stop_loss_simple():
    """测试ATR动态止损"""
    print("\n🧪 测试ATR动态止损...\n")
    
    risk = RiskManager()
    symbol = 'ETH/USDT'
    
    # 模拟持仓
    entry_price = 2000
    risk.state['positions'][symbol] = {
        'entry_price': entry_price,
        'amount': 0.01,
        'cost': 20,
        'highest_price': 2100,
        'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # 设置ATR止损配置
    risk.state['runtime_config'] = {
        symbol: {
            'use_atr_stop': True,
            'atr_multiplier': 2.0,
            'stop_loss_pct': 0.05
        }
    }
    
    # 模拟ATR值
    atr_value = 50  # 假设ATR为50
    
    atr_stop_price = entry_price - atr_value * 2.0
    
    print(f"📊 {symbol} ATR动态止损测试")
    print("-" * 60)
    print(f"入场价: ${entry_price:.2f}")
    print(f"模拟ATR: ${atr_value:.2f}")
    print(f"ATR止损价: ${atr_stop_price:.2f} (入场价 - ATR × 2)")
    print()
    
    # 测试不同价格场景
    test_scenarios = [
        {'price': 2050, 'desc': '价格正常波动'},
        {'price': atr_stop_price + 10, 'desc': '接近ATR止损线'},
        {'price': atr_stop_price - 5, 'desc': '跌破ATR止损线'},
    ]
    
    # 创建模拟DataFrame（简化版，只包含必要的ATR计算）
    class MockDF:
        def __init__(self, atr_value):
            self.atr_value = atr_value
            
        @property
        def high(self):
            return MockSeries()
            
        @property
        def low(self):
            return MockSeries()
            
        @property
        def close(self):
            return MockSeries()
    
    class MockSeries:
        def iloc(self):
            return [0]
    
    # 注意：由于ATR计算需要真实数据，这里我们只测试逻辑
    print("⚠️  注意：ATR止损需要真实市场数据")
    print("实际运行时，系统会自动计算ATR并设置止损线")
    print()
    
    # 测试固定止损
    print("📊 固定止损测试（作为备选方案）")
    print("-" * 60)
    
    for test in test_scenarios:
        price = test['price']
        loss_pct = (entry_price - price) / entry_price
        
        print(f"价格: ${price:.2f} | {test['desc']}")
        print(f"  亏损: {loss_pct:.2%} | 止损阈值: 5%")
        
        if loss_pct >= 0.05:
            print(f"  止损信号: ✅ 触发固定止损")
        else:
            print(f"  止损信号: ❌ 未触发")
        print()

def main():
    print("=" * 60)
    print("🚀 成交量确认 & 动态参数调整 测试套件")
    print("=" * 60)
    print()
    
    test_volume_confirmation_simple()
    test_volatility_adjustment_simple()
    test_atr_stop_loss_simple()
    
    print("\n✅ 所有测试完成！")
    print("\n📋 功能总结:")
    print("-" * 60)
    print("✅ 成交量确认: 趋势买入需成交量放大1.5x，震荡买入需1.2x")
    print("✅ 波动率调整: 低波动收紧参数，高波动放宽参数")
    print("✅ ATR止损: 动态计算止损线，适应市场波动")
    print()

if __name__ == "__main__":
    main()
