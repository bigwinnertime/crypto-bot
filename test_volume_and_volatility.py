#!/usr/bin/env python3
"""
测试成交量确认和动态参数调整功能
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bot_engine import AdvancedTradingBot

def create_test_data(price_start, price_trend, volume_base, volume_trend, periods=100):
    """创建测试数据"""
    dates = pd.date_range(end=datetime.now(), periods=periods, freq='1H')
    
    # 价格数据
    prices = [price_start]
    for i in range(1, periods):
        change = price_trend + np.random.randn() * 0.01
        prices.append(prices[-1] * (1 + change))
    
    # 成交量数据
    volumes = [volume_base]
    for i in range(1, periods):
        change = volume_trend + np.random.randn() * 0.1
        volumes.append(volumes[-1] * (1 + change))
    
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': [p * 1.01 for p in prices],
        'low': [p * 0.99 for p in prices],
        'close': prices,
        'volume': volumes
    })
    df.set_index('timestamp', inplace=True)
    
    return df

def test_volume_confirmation():
    """测试成交量确认功能"""
    print("🧪 测试成交量确认功能...\n")
    
    bot = AdvancedTradingBot()
    symbol = 'ETH/USDT'
    
    # 场景1: 趋势买入信号 + 高成交量（应该触发买入）
    print("📊 场景1: 趋势买入信号 + 高成交量确认")
    print("-" * 60)
    
    df1 = create_test_data(
        price_start=2000,
        price_trend=0.002,  # 上涨趋势
        volume_base=1000,
        volume_trend=0.05,   # 成交量放大
        periods=100
    )
    
    signal1, mode1 = bot.get_strategy_signal(df1, symbol)
    print(f"信号: {signal1}, 模式: {mode1}")
    print(f"预期: BUY (成交量确认通过)")
    print()
    
    # 场景2: 趋势买入信号 + 低成交量（应该拒绝买入）
    print("📊 场景2: 趋势买入信号 + 低成交量拒绝")
    print("-" * 60)
    
    df2 = create_test_data(
        price_start=2000,
        price_trend=0.002,  # 上涨趋势
        volume_base=1000,
        volume_trend=-0.05,  # 成交量萎缩
        periods=100
    )
    
    signal2, mode2 = bot.get_strategy_signal(df2, symbol)
    print(f"信号: {signal2}, 模式: {mode2}")
    print(f"预期: HOLD (成交量不足)")
    print()
    
    # 场景3: 震荡买入信号 + 成交量确认
    print("📊 场景3: 震荡买入信号 + 成交量确认")
    print("-" * 60)
    
    # 创建震荡行情（ADX低）
    df3 = create_test_data(
        price_start=2000,
        price_trend=0.0001,  # 横盘震荡
        volume_base=1000,
        volume_trend=0.03,   # 成交量放大
        periods=100
    )
    
    signal3, mode3 = bot.get_strategy_signal(df3, symbol)
    print(f"信号: {signal3}, 模式: {mode3}")
    print()

def test_volatility_adjustment():
    """测试波动率动态参数调整"""
    print("\n🧪 测试波动率动态参数调整...\n")
    
    bot = AdvancedTradingBot()
    symbol = 'ETH/USDT'
    
    # 场景1: 低波动市场
    print("📊 场景1: 低波动市场 (ATR% < 2%)")
    print("-" * 60)
    
    df_low_vol = create_test_data(
        price_start=2000,
        price_trend=0.001,  # 小幅波动
        volume_base=1000,
        volume_trend=0.02,
        periods=100
    )
    
    # 手动测试参数调整
    from ta.volatility import AverageTrueRange
    atr = AverageTrueRange(df_low_vol['high'], df_low_vol['low'], df_low_vol['close'], 
                           window=14).average_true_range().iloc[-1]
    price = df_low_vol['close'].iloc[-1]
    atr_pct = atr / price
    
    print(f"ATR: {atr:.2f}, 价格: {price:.2f}, ATR%: {atr_pct:.2%}")
    
    # 获取调整后的参数
    spec = bot.config.STRATEGY_CONFIG.get(symbol, bot.config.DEFAULT_CONFIG)
    adjusted = bot._adjust_params_by_volatility(spec, atr_pct)
    
    print(f"原始ADX阈值: {spec['adx_threshold']}")
    print(f"调整后ADX阈值: {adjusted['adx_threshold']}")
    print(f"预期: 参数缩小 (更严格)")
    print()
    
    # 场景2: 高波动市场
    print("📊 场景2: 高波动市场 (ATR% > 5%)")
    print("-" * 60)
    
    df_high_vol = create_test_data(
        price_start=2000,
        price_trend=0.005,  # 大幅波动
        volume_base=1000,
        volume_trend=0.05,
        periods=100
    )
    
    # 添加更多波动
    for i in range(len(df_high_vol)):
        if i % 10 == 0:
            df_high_vol.iloc[i, df_high_vol.columns.get_loc('high')] *= 1.05
            df_high_vol.iloc[i, df_high_vol.columns.get_loc('low')] *= 0.95
    
    atr = AverageTrueRange(df_high_vol['high'], df_high_vol['low'], df_high_vol['close'], 
                           window=14).average_true_range().iloc[-1]
    price = df_high_vol['close'].iloc[-1]
    atr_pct = atr / price
    
    print(f"ATR: {atr:.2f}, 价格: {price:.2f}, ATR%: {atr_pct:.2%}")
    
    adjusted = bot._adjust_params_by_volatility(spec, atr_pct)
    
    print(f"原始ADX阈值: {spec['adx_threshold']}")
    print(f"调整后ADX阈值: {adjusted['adx_threshold']}")
    print(f"预期: 参数放大 (更宽松)")
    print()

def test_atr_stop_loss():
    """测试ATR动态止损"""
    print("\n🧪 测试ATR动态止损...\n")
    
    from risk_manager import RiskManager
    
    risk = RiskManager()
    symbol = 'ETH/USDT'
    
    # 创建测试数据
    df = create_test_data(
        price_start=2000,
        price_trend=0.003,  # 上涨
        volume_base=1000,
        volume_trend=0.02,
        periods=100
    )
    
    # 模拟持仓
    entry_price = 2000
    risk.state['positions'][symbol] = {
        'entry_price': entry_price,
        'amount': 0.01,
        'cost': 20,
        'highest_price': 2100,  # 最高涨到2100
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
    
    # 计算ATR
    from ta.volatility import AverageTrueRange
    atr = AverageTrueRange(df['high'], df['low'], df['close'], 
                           window=14).average_true_range().iloc[-1]
    
    atr_stop_price = entry_price - atr * 2.0
    
    print(f"入场价: {entry_price:.2f}")
    print(f"ATR: {atr:.2f}")
    print(f"ATR止损价: {atr_stop_price:.2f} (入场价 - ATR × 2)")
    print()
    
    # 测试不同价格
    test_prices = [
        {'price': 2050, 'desc': '价格正常波动'},
        {'price': atr_stop_price + 10, 'desc': '接近ATR止损线'},
        {'price': atr_stop_price - 5, 'desc': '跌破ATR止损线'},
    ]
    
    for test in test_prices:
        price = test['price']
        desc = test['desc']
        
        stop_reason = risk.update_trailing_stop(symbol, price, df)
        
        print(f"价格: {price:.2f} | {desc}")
        print(f"止损信号: {stop_reason if stop_reason else '无'}")
        print()

def main():
    print("=" * 60)
    print("🚀 成交量确认 & 动态参数调整 测试套件")
    print("=" * 60)
    print()
    
    test_volume_confirmation()
    test_volatility_adjustment()
    test_atr_stop_loss()
    
    print("\n✅ 所有测试完成！")

if __name__ == "__main__":
    main()
