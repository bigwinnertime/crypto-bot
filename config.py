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
TIMEFRAME = '4h'           # K线周期（4h：每根K线波动更大，手续费占比更低）
#TRADE_AMOUNT_USDT = 20     # 每次下单的固定金额（USDT）

# --- 策略核心参数 ---
RSI_PERIOD = 14            # RSI 计算周期
BB_PERIOD = 20             # 布林带周期（新增布林带策略）
BB_STD = 2                 # 布林带标准差

# --- 币种差异化策略参数 ---
STRATEGY_CONFIG = {
    'BTC/USDT': {
        'adx_threshold': 22,        # 4h框架下ADX需更强趋势才入场
        'rsi_oversold': 35,
        'rsi_overbought': 70,
        'trade_amount': 30,
        'stop_loss_pct': 0.04,      # 4h框架止损稍宽，减少噪音触发

        'volume_threshold': 1.5,    # 收紧量能要求，减少低质量信号
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

        # 追踪止盈：门槛从2%提升至5%，给利润更大运行空间
        'trailing_stops': [
            {'profit_threshold': 0.05, 'trigger_drawdown': 0.025, 'trailing_pct': 0.02},
            {'profit_threshold': 0.10, 'trigger_drawdown': 0.03,  'trailing_pct': 0.025},
            {'profit_threshold': 0.18, 'trigger_drawdown': 0.04,  'trailing_pct': 0.035},
        ],

        'risk_per_trade': 0.01,       # 每笔交易风险占账户 1%
        'max_trade_amount': 100,      # 最大单笔金额上限
        'profit_target_atr': 6.0,     # 主动止盈：4h框架ATR更大，目标提升至6×ATR（约3-5%）
        'min_profit_pct': 0.008,      # 最小盈利保护：盈利低于0.8%时不主动卖出

        # 时间衰减：4h框架下延长观察期，48h后才开始收紧
        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 12,  'multiplier': 1.0},
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 0.9},
                {'hours': 72,  'multiplier': 0.75},
                {'hours': float('inf'), 'multiplier': 0.6}
            ]
        },

        # Regime 市场状态识别参数
        'regime_trend_adx': 25,          # ADX≥此值 + 布林带扩张 → 趋势态
        'regime_range_adx': 20,          # ADX≤此值 + 布林带收口 → 震荡态
        'regime_trend_bb_width': 0.03,   # 趋势态最小布林带宽度
        'regime_range_bb_width': 0.02,   # 震荡态最大布林带宽度

        # 均值回归策略退出参数（仅震荡态入场仓位使用）
        'meanrev_config': {
            'stop_loss_pct': 0.025,       # 均值回归止损更紧（2.5%）
            'rsi_exit': 50,               # RSI回升至此值止盈
            'bb_mid_exit': True,          # 价格触及布林中轨止盈
            'max_hold_hours': 24,         # 最长持仓时间，超时强制退出
        },
    },
    'ETH/USDT': {
        'adx_threshold': 22,
        'rsi_oversold': 35,
        'rsi_overbought': 70,
        'trade_amount': 20,
        'stop_loss_pct': 0.05,

        'volume_threshold': 1.5,    # 收紧量能要求
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

        # 追踪止盈：门槛从3%提升至6%
        'trailing_stops': [
            {'profit_threshold': 0.06, 'trigger_drawdown': 0.03,  'trailing_pct': 0.025},
            {'profit_threshold': 0.12, 'trigger_drawdown': 0.035, 'trailing_pct': 0.03},
            {'profit_threshold': 0.20, 'trigger_drawdown': 0.045, 'trailing_pct': 0.04},
        ],

        'risk_per_trade': 0.01,
        'max_trade_amount': 80,
        'profit_target_atr': 6.0,    # 提升至 6×ATR
        'min_profit_pct': 0.008,     # 最小盈利保护：0.8%

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 12,  'multiplier': 1.0},
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 0.9},
                {'hours': 72,  'multiplier': 0.75},
                {'hours': float('inf'), 'multiplier': 0.6}
            ]
        },

        # Regime 市场状态识别参数
        'regime_trend_adx': 25,
        'regime_range_adx': 20,
        'regime_trend_bb_width': 0.03,
        'regime_range_bb_width': 0.02,

        # 均值回归策略退出参数（仅震荡态入场仓位使用）
        'meanrev_config': {
            'stop_loss_pct': 0.025,
            'rsi_exit': 50,
            'bb_mid_exit': True,
            'max_hold_hours': 24,
        },
    },
    'SOL/USDT': {
        'adx_threshold': 25,        # SOL波动大，要求更强趋势
        'rsi_oversold': 30,
        'rsi_overbought': 75,
        'trade_amount': 10,
        'stop_loss_pct': 0.06,      # SOL波动更大，止损稍宽

        'volume_threshold': 1.8,    # SOL量能要求更高
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

        # 追踪止盈：门槛从4%提升至8%
        'trailing_stops': [
            {'profit_threshold': 0.08, 'trigger_drawdown': 0.035, 'trailing_pct': 0.03},
            {'profit_threshold': 0.15, 'trigger_drawdown': 0.04,  'trailing_pct': 0.035},
            {'profit_threshold': 0.25, 'trigger_drawdown': 0.05,  'trailing_pct': 0.045},
        ],

        'risk_per_trade': 0.01,
        'max_trade_amount': 50,
        'profit_target_atr': 7.0,    # SOL 波动大，止盈目标提升至 7×ATR
        'min_profit_pct': 0.010,     # SOL 最小盈利保护：1.0%

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 12,  'multiplier': 1.0},
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 0.9},
                {'hours': 72,  'multiplier': 0.75},
                {'hours': float('inf'), 'multiplier': 0.6}
            ]
        },

        # Regime 市场状态识别参数（SOL波动大，阈值更宽）
        'regime_trend_adx': 28,
        'regime_range_adx': 22,
        'regime_trend_bb_width': 0.04,
        'regime_range_bb_width': 0.025,

        # 均值回归策略退出参数（SOL止损稍宽）
        'meanrev_config': {
            'stop_loss_pct': 0.035,
            'rsi_exit': 50,
            'bb_mid_exit': True,
            'max_hold_hours': 24,
        },
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
    
    # 默认分阶段追踪止盈配置（门槛提升，给利润更大运行空间）
    'trailing_stops': [
        {
            'profit_threshold': 0.05,      # 盈利5%时才开启追踪
            'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
            'trailing_pct': 0.02
        },
        {
            'profit_threshold': 0.10,      # 盈利10%时开启追踪
            'trigger_drawdown': 0.03,     # 回撤3%时触发卖出
            'trailing_pct': 0.025
        },
        {
            'profit_threshold': 0.18,      # 盈利18%时开启追踪
            'trigger_drawdown': 0.04,     # 回撤4%时触发卖出
            'trailing_pct': 0.035
        },
    ],

    # 波动率自适应仓位
    'risk_per_trade': 0.01,
    'max_trade_amount': 100,
    'profit_target_atr': 6.0,       # 4h框架下提升止盈目标
    'min_profit_pct': 0.008,        # 最小盈利保护（默认0.8%）

    # 时间衰减配置（4h框架下延长观察期）
    'time_decay': {
        'enabled': True,
        'intervals': [
            {'hours': 12, 'multiplier': 1.0},
            {'hours': 24, 'multiplier': 1.0},
            {'hours': 48, 'multiplier': 0.9},
            {'hours': 72, 'multiplier': 0.75},
            {'hours': float('inf'), 'multiplier': 0.6}
        ]
    },

    # Regime 市场状态识别参数
    'regime_trend_adx': 25,
    'regime_range_adx': 20,
    'regime_trend_bb_width': 0.03,
    'regime_range_bb_width': 0.02,

    # 均值回归策略退出参数
    'meanrev_config': {
        'stop_loss_pct': 0.025,
        'rsi_exit': 50,
        'bb_mid_exit': True,
        'max_hold_hours': 24,
    },
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
HIGHER_TIMEFRAME = '1d'    # 高级时间框架：日线用于趋势过滤（主框架升至4h后，高级框架升至1d）

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
