import os
from dotenv import load_dotenv

# 加载 .env 文件（使用项目根目录路径，确保无论从何处启动都能找到）
# 注意：config.py 位于项目根目录，通过 __file__ 获取绝对路径
_dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(dotenv_path=_dotenv_path)

# --- API 认证 ---
API_KEY = os.getenv('BINANCE_API_KEY')
SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')

# --- 交易市场配置 ---
SYMBOLS = ['BTC/USDT','ETH/USDT','SOL/USDT']
TIMEFRAME = '1h'           # K线周期
#TRADE_AMOUNT_USDT = 20     # 每次下单的固定金额（USDT）

# --- 策略核心参数 ---
RSI_PERIOD = 14            # RSI 计算周期
BB_PERIOD = 20             # 布林带周期（新增布林带策略）
BB_STD = 2                 # 布林带标准差

# --- 币种差异化策略参数 ---
STRATEGY_CONFIG = {
    'BTC/USDT': {
        'adx_threshold': 20,        # 从 22 降至 20，更易触发趋势信号
        'rsi_oversold': 35,         # 从 40 降至 35，RSI 更有意义
        'rsi_overbought': 70,       # 从 75 降至 70，捕捉中期顶部
        'trade_amount': 30,
        'stop_loss_pct': 0.03,

        'volume_threshold': 1.2,    # 从 1.5 降至 1.2，放宽量能要求
        'volume_ma_period': 20,

        'atr_period': 14,
        'atr_multiplier': 2.0,
        'use_atr_stop': True,

        # 布林带参数（新增，信号辅助）
        'bb_period': 20,
        'bb_std': 2,

        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.02,
            'high_vol_threshold': 0.05,
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.2,
        },

        'trailing_stops': [
            {'profit_threshold': 0.02, 'trigger_drawdown': 0.02,  'trailing_pct': 0.015},
            {'profit_threshold': 0.05, 'trigger_drawdown': 0.025, 'trailing_pct': 0.02},
            {'profit_threshold': 0.10, 'trigger_drawdown': 0.03,  'trailing_pct': 0.025},
        ],

        'risk_per_trade': 0.01,       # 每笔交易风险占账户 1%
        'max_trade_amount': 100,      # 最大单笔金额上限
        'profit_target_atr': 3.0,     # 主动止盈：盈利达 3×ATR 时锁定利润

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 1,  'multiplier': 1.0},
                {'hours': 4,  'multiplier': 0.9},
                {'hours': 12, 'multiplier': 0.7},
                {'hours': 24, 'multiplier': 0.5},
                {'hours': float('inf'), 'multiplier': 0.3}
            ]
        }
    },
    'ETH/USDT': {
        'adx_threshold': 20,        # 从 22 降至 20
        'rsi_oversold': 35,          # 从 40 降至 35
        'rsi_overbought': 70,        # 从 75 降至 70
        'trade_amount': 20,
        'stop_loss_pct': 0.05,

        'volume_threshold': 1.2,    # 从 1.5 降至 1.2
        'volume_ma_period': 20,

        'atr_period': 14,
        'atr_multiplier': 2.0,
        'use_atr_stop': True,

        'bb_period': 20,
        'bb_std': 2,

        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.02,
            'high_vol_threshold': 0.05,
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.2,
        },

        'trailing_stops': [
            {'profit_threshold': 0.03, 'trigger_drawdown': 0.02,  'trailing_pct': 0.02},
            {'profit_threshold': 0.06, 'trigger_drawdown': 0.025, 'trailing_pct': 0.025},
            {'profit_threshold': 0.12, 'trigger_drawdown': 0.03,  'trailing_pct': 0.03},
        ],

        'risk_per_trade': 0.01,
        'max_trade_amount': 80,
        'profit_target_atr': 3.0,

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 1,  'multiplier': 1.0},
                {'hours': 4,  'multiplier': 0.9},
                {'hours': 12, 'multiplier': 0.7},
                {'hours': 24, 'multiplier': 0.5},
                {'hours': float('inf'), 'multiplier': 0.3}
            ]
        }
    },
    'SOL/USDT': {
        'adx_threshold': 22,        # 从 25 降至 22
        'rsi_oversold': 30,         # 从 25 升至 30，更实际
        'rsi_overbought': 75,        # 从 80 降至 75
        'trade_amount': 10,
        'stop_loss_pct': 0.05,

        'volume_threshold': 1.5,    # 从 2.0 降至 1.5（SOL波动较大，保持稍高要求）
        'volume_ma_period': 20,

        'atr_period': 14,
        'atr_multiplier': 2.5,
        'use_atr_stop': True,

        'bb_period': 20,
        'bb_std': 2,

        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.03,
            'high_vol_threshold': 0.08,
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.3,
        },

        'trailing_stops': [
            {'profit_threshold': 0.04, 'trigger_drawdown': 0.025, 'trailing_pct': 0.025},
            {'profit_threshold': 0.08, 'trigger_drawdown': 0.03,  'trailing_pct': 0.03},
            {'profit_threshold': 0.15, 'trigger_drawdown': 0.035, 'trailing_pct': 0.035},
        ],

        'risk_per_trade': 0.01,
        'max_trade_amount': 50,
        'profit_target_atr': 3.5,     # SOL 波动大，止盈目标稍宽

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 1,  'multiplier': 1.0},
                {'hours': 4,  'multiplier': 0.9},
                {'hours': 12, 'multiplier': 0.7},
                {'hours': 24, 'multiplier': 0.5},
                {'hours': float('inf'), 'multiplier': 0.3}
            ]
        }
    }
}


# 兜底配置（防止新增币种忘记写配置）
DEFAULT_CONFIG = {
    'adx_threshold': 25,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
    'trade_amount': 10,
    'stop_loss_pct': 0.03,
    
    # 成交量确认参数
    'volume_threshold': 1.5,
    'volume_ma_period': 20,
    
    # ATR动态止损参数
    'atr_period': 14,
    'atr_multiplier': 2.0,
    'use_atr_stop': True,
    
    # 布林带参数
    'bb_period': 20,
    'bb_std': 2,
    
    # 波动率适配参数
    'volatility_adjust': {
        'enabled': True,
        'low_vol_threshold': 0.02,
        'high_vol_threshold': 0.05,
        'low_vol_multiplier': 0.8,
        'high_vol_multiplier': 1.2,
    },
    
    # 默认分阶段追踪止盈配置
    'trailing_stops': [
        {
            'profit_threshold': 0.02,      # 盈利2%时开启追踪
            'trigger_drawdown': 0.015,    # 回撤1.5%时触发卖出
            'trailing_pct': 0.015         # 历史追踪比例（向后兼容）
        },
        {
            'profit_threshold': 0.05,      # 盈利5%时开启追踪
            'trigger_drawdown': 0.02,     # 回撤2%时触发卖出
            'trailing_pct': 0.02
        },
        {
            'profit_threshold': 0.10,      # 盈利10%时开启追踪
            'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
            'trailing_pct': 0.025
        },
    ],

    # 波动率自适应仓位
    'risk_per_trade': 0.01,
    'max_trade_amount': 100,
    'profit_target_atr': 3.0,

    # 默认时间衰减配置（持仓越久，止损越紧）
    'time_decay': {
        'enabled': True,
        'intervals': [
            {'hours': 1, 'multiplier': 1.0},
            {'hours': 4, 'multiplier': 0.9},
            {'hours': 12, 'multiplier': 0.7},
            {'hours': 24, 'multiplier': 0.5},
            {'hours': float('inf'), 'multiplier': 0.3}
        ]
    }
}

# --- 风险控制参数 ---
MAX_TOTAL_EXPOSURE = 0.7   # 总仓位价值占账户总资产的最大比例 (70%)
DRAWDOWN_FUSE = 0.08       # 熔断阈值：单周期价格跌幅超 8% 触发熔断
FUSE_DURATION = 7200       # 熔断锁定时间：2 小时 (单位：秒)

# 账户级最大回撤保护
MAX_DRAWDOWN_PCT = 0.15    # 账户净值从最高点回撤 15% 暂停所有交易
DRAWDOWN_COOLDOWN = 14400  # 回撤冷却时间：4 小时 (单位：秒)

# 相关性分组（同组币种限制同时持仓数量）
CORRELATION_GROUPS = {
    'L1': ['BTC/USDT', 'ETH/USDT'],    # 高相关性 Layer1
}
MAX_CORRELATED_POSITIONS = 1  # 同组最多持仓数

# 多时间框架配置
HIGHER_TIMEFRAME = '4h'    # 高级时间框架用于趋势过滤

# 是否开启实盘下单：True 为真实下单，False 为模拟运行
LIVE_TRADE = False

# --- 邮件通知配置中心 ---
EMAIL_SMTP_SERVER = os.getenv('EMAIL_SMTP_SERVER', 'smtp.qq.com')
EMAIL_SMTP_PORT = int(os.getenv('EMAIL_SMTP_PORT', 465))
EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECEIVER = os.getenv('EMAIL_RECEIVER')


# --- 电报机器人通知配置中心 ---
TELEGRAM_TOKEN= os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID= os.getenv('TELEGRAM_CHAT_ID')
