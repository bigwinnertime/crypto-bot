import os
from dotenv import load_dotenv

# 加载 .env 文件中的 API Key
load_dotenv()

# --- API 认证 ---
API_KEY = os.getenv('BINANCE_API_KEY')
SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')

# --- 交易市场配置 ---
SYMBOLS = ['BTC/USDT','ETH/USDT','SOL/USDT']
TIMEFRAME = '1h'           # K线周期
#TRADE_AMOUNT_USDT = 20     # 每次下单的固定金额（USDT）

# --- 策略核心参数 ---
#ADX_THRESHOLD = 25         # 判断趋势强弱的门槛 (ADX > 25 视为有趋势)
RSI_PERIOD = 14            # RSI 计算周期
#RSI_OVERSOLD = 30          # 震荡市：RSI 低于此值考虑买入
#RSI_OVERBOUGHT = 70        # 震荡市：RSI 高于此值考虑卖出
BB_PERIOD = 20             # 布林带周期
BB_STD = 2                 # 布林带标准差

# --- 币种差异化策略参数 ---
STRATEGY_CONFIG = {
    'BTC/USDT': {
        'adx_threshold': 30,    # BTC 波动较小，20 即可确立趋势
        'rsi_oversold': 40,
        'rsi_overbought': 75,
        'trade_amount': 30,      # BTC 可以仓位稍大
        'stop_loss_pct': 0.03,     # 3% 固定硬止损
        
        # 成交量确认参数
        'volume_threshold': 1.5,    # 成交量需大于均值的1.5倍
        'volume_ma_period': 20,     # 成交量均线周期
        
        # ATR动态止损参数
        'atr_period': 14,          # ATR计算周期
        'atr_multiplier': 2.0,     # ATR止损倍数（动态止损 = 入场价 - ATR * multiplier）
        'use_atr_stop': True,      # 是否使用ATR动态止损
        
        # 波动率适配参数
        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.02,    # 低波动阈值（ATR% < 2%）
            'high_vol_threshold': 0.05,   # 高波动阈值（ATR% > 5%）
            'low_vol_multiplier': 0.8,    # 低波动时参数缩小
            'high_vol_multiplier': 1.2,   # 高波动时参数放大
        },
        
        # 分阶段追踪止盈配置
        'trailing_stops': [
            {
                'profit_threshold': 0.02,      # 盈利2%时开启追踪
                'trigger_drawdown': 0.02,     # 回撤2%时触发卖出
                'trailing_pct': 0.015         # 历史追踪比例（向后兼容）
            },
            {
                'profit_threshold': 0.05,      # 盈利5%时开启追踪
                'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
                'trailing_pct': 0.02
            },
            {
                'profit_threshold': 0.10,      # 盈利10%时开启追踪
                'trigger_drawdown': 0.03,     # 回撤3%时触发卖出
                'trailing_pct': 0.025
            },
        ],
        
        # 时间衰减配置
        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 1, 'multiplier': 1.0},    # 1小时内，不调整
                {'hours': 4, 'multiplier': 0.8},    # 1-4小时，追踪比例×0.8
                {'hours': 12, 'multiplier': 0.6},   # 4-12小时，追踪比例×0.6
                {'hours': 24, 'multiplier': 0.5},   # 12-24小时，追踪比例×0.5
                {'hours': float('inf'), 'multiplier': 0.4}  # 24小时以上，追踪比例×0.4
            ]
        }
    },
    'ETH/USDT': {
        'adx_threshold': 30,
        'rsi_oversold': 40,
        'rsi_overbought': 75,
        'trade_amount': 20,
        'stop_loss_pct': 0.05,     # 5% 固定硬止损
        
        # 成交量确认参数
        'volume_threshold': 1.5,
        'volume_ma_period': 20,
        
        # ATR动态止损参数
        'atr_period': 14,
        'atr_multiplier': 2.0,
        'use_atr_stop': True,
        
        # 波动率适配参数
        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.02,
            'high_vol_threshold': 0.05,
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.2,
        },
        
        # 分阶段追踪止盈配置
        'trailing_stops': [
            {
                'profit_threshold': 0.03,      # 盈利3%时开启追踪
                'trigger_drawdown': 0.02,     # 回撤2%时触发卖出
                'trailing_pct': 0.02          # 历史追踪比例（向后兼容）
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
        
        # 时间衰减配置
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
    },
    'SOL/USDT': {
        'adx_threshold': 30,    # SOL 波动剧烈，需要 35 以上的高强度才追涨，防止被骗炮
        'rsi_oversold': 25,     # 跌得更深才买入
        'rsi_overbought': 80,   # 涨得更高才卖出
        'trade_amount': 10,      # 高波动币种减小单笔金额
        'stop_loss_pct': 0.05,     # 5% 固定硬止损
        
        # 成交量确认参数
        'volume_threshold': 2.0,    # SOL波动大，需要更高的成交量确认
        'volume_ma_period': 20,
        
        # ATR动态止损参数
        'atr_period': 14,
        'atr_multiplier': 2.5,     # SOL波动大，ATR倍数提高
        'use_atr_stop': True,
        
        # 波动率适配参数
        'volatility_adjust': {
            'enabled': True,
            'low_vol_threshold': 0.03,    # SOL的低波动阈值更高
            'high_vol_threshold': 0.08,   # SOL的高波动阈值更高
            'low_vol_multiplier': 0.8,
            'high_vol_multiplier': 1.3,   # 高波动时参数放大更多
        },
        
        # 分阶段追踪止盈配置
        'trailing_stops': [
            {
                'profit_threshold': 0.04,      # 盈利4%时开启追踪
                'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
                'trailing_pct': 0.025         # 历史追踪比例（向后兼容）
            },
            {
                'profit_threshold': 0.08,      # 盈利8%时开启追踪
                'trigger_drawdown': 0.03,     # 回撤3%时触发卖出
                'trailing_pct': 0.03
            },
            {
                'profit_threshold': 0.15,      # 盈利15%时开启追踪
                'trigger_drawdown': 0.035,    # 回撤3.5%时触发卖出
                'trailing_pct': 0.035
            },
        ],
        
        # 时间衰减配置
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
    
    # 默认时间衰减配置
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

# --- 风险控制参数 ---
MAX_TOTAL_EXPOSURE = 0.7   # 总仓位价值占账户总资产的最大比例 (70%)
DRAWDOWN_FUSE = 0.05       # 熔断阈值：单周期价格跌幅超 5% 停止交易
FUSE_DURATION = 14400      # 熔断锁定时间：4 小时 (单位：秒)

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
