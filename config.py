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
BB_PERIOD = 20             # 布林带周期（全局默认，币种级可覆盖）
BB_STD = 2                 # 布林带标准差

# --- 币种差异化策略参数 ---
STRATEGY_CONFIG = {
    'BTC/USDT': {
        'adx_threshold': 22,        # 4h框架下ADX需更强趋势才入场
        'rsi_oversold': 35,
        'rsi_overbought': 70,
        'trade_amount': 30,
        'stop_loss_pct': 0.04,      # 4h框架止损稍宽，减少噪音触发

        'volume_threshold': 1.2,    # 量能放大阈值（从1.5降至1.2，减少过度过滤）
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

        'risk_per_trade': 0.01,       # 每笔交易风险占账户 1%（控制仓位下限）
        'max_trade_amount': 100,      # 最大单笔金额上限（USDT，旧参数保留兼容）
        'max_position_pct': 0.08,     # 单笔仓位占账户比例上限 8%（≈800 USDT，控制仓位上限）
        'min_body_ratio': 0.30,       # 阳线实体占比阈值（过滤十字星/弱信号K线）
        'profit_target_atr': 6.0,     # 主动止盈：4h框架ATR更大，目标提升至6×ATR（约3-5%）
        'min_profit_pct': 0.008,      # 最小盈利保护：盈利低于0.8%时不主动卖出
        'breakeven_trigger': 0.02,    # 保本止损：盈利超2%后止损上移至成本价
        'breakeven_buffer': 0.003,   # 保本线上方留0.3%缓冲
        'min_signal_score': 40,      # 信号最低评分阈值（低于此值放弃入场）

        # 时间衰减：4h框架下延长观察期，96h后才开始收紧（72h仅18根K线，对趋势跟踪过早）
        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 1.0},
                {'hours': 96,  'multiplier': 0.95},
                {'hours': 144, 'multiplier': 0.85},
                {'hours': float('inf'), 'multiplier': 0.75}
            ]
        },

        # Regime 市场状态识别参数（收窄 ADX 间隔：趋势22/震荡22，消除中间地带）
        'regime_trend_adx': 22,          # ADX≥此值 + 布林带扩张 → 趋势态
        'regime_range_adx': 22,          # ADX≤此值 或 布林带收口 → 震荡态
        'regime_trend_bb_width': 0.03,   # 趋势态最小布林带宽度
        'regime_range_bb_width': 0.02,   # 震荡态最大布林带宽度

        # 均值回归策略退出参数（仅震荡态入场仓位使用）
        'meanrev_config': {
            'stop_loss_pct': 0.025,       # 均值回归止损更紧（2.5%）
            'rsi_exit': 50,               # RSI回升至此值止盈
            'bb_mid_exit': True,          # 价格触及布林中轨止盈
            'max_hold_hours': 12,         # 超时从16h缩短至12h（减少微亏拖累）
        },
    },
    'ETH/USDT': {
        'adx_threshold': 22,
        'rsi_oversold': 35,
        'rsi_overbought': 70,
        'trade_amount': 20,
        'stop_loss_pct': 0.05,

        'volume_threshold': 1.2,    # 量能放大阈值（从1.5降至1.2，避免过滤过多信号）
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
        'max_position_pct': 0.07,     # ETH单笔仓位上限 7%（≈700 USDT）
        'min_body_ratio': 0.30,
        'profit_target_atr': 6.0,    # 提升至 6×ATR
        'min_profit_pct': 0.008,     # 最小盈利保护：0.8%
        'breakeven_trigger': 0.02,   # 保本止损：盈利超2%后止损上移至成本价
        'breakeven_buffer': 0.003,  # 保本线上方留0.3%缓冲

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 1.0},
                {'hours': 96,  'multiplier': 0.95},
                {'hours': 144, 'multiplier': 0.85},
                {'hours': float('inf'), 'multiplier': 0.75}
            ]
        },

        # Regime 市场状态识别参数
        'regime_trend_adx': 22,
        'regime_range_adx': 22,
        'regime_trend_bb_width': 0.03,
        'regime_range_bb_width': 0.02,

        # 均值回归策略退出参数（仅震荡态入场仓位使用）
        'meanrev_config': {
            'stop_loss_pct': 0.025,
            'rsi_exit': 50,
            'bb_mid_exit': True,
            'max_hold_hours': 16,         # 超时从24h缩短至16h
        },
    },
    'SOL/USDT': {
        'adx_threshold': 25,        # SOL波动大，要求更强趋势
        'rsi_oversold': 30,
        'rsi_overbought': 75,
        'trade_amount': 10,
        'stop_loss_pct': 0.045,     # SOL固定止损从6%收紧至4.5%（熊市中6%止损过宽）

        'volume_threshold': 1.3,    # SOL量能要求（从1.8降至1.3，1.8过滤了89%的信号）
        'volume_ma_period': 20,

        'atr_period': 14,
        'atr_multiplier': 2.0,       # ATR倍数从2.5收紧至2.0（SOL熊市中2.5倍追踪止损过宽）
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
        'max_position_pct': 0.06,     # SOL波动大，单笔仓位上限 6%（≈600 USDT）
        'min_body_ratio': 0.35,       # SOL波动大，要求更大实体占比
        'profit_target_atr': 7.0,    # SOL 波动大，止盈目标提升至 7×ATR
        'min_profit_pct': 0.010,     # SOL 最小盈利保护：1.0%
        'breakeven_trigger': 0.025,  # SOL保本止损：盈利超2.5%后止损上移（SOL波动大，门槛稍高）
        'breakeven_buffer': 0.004,  # SOL保本缓冲0.4%

        'time_decay': {
            'enabled': True,
            'intervals': [
                {'hours': 24,  'multiplier': 1.0},
                {'hours': 48,  'multiplier': 1.0},
                {'hours': 96,  'multiplier': 0.95},
                {'hours': 144, 'multiplier': 0.85},
                {'hours': float('inf'), 'multiplier': 0.75}
            ]
        },

        # Regime 市场状态识别参数（SOL波动大，趋势态要求稍高）
        'regime_trend_adx': 25,
        'regime_range_adx': 25,
        'regime_trend_bb_width': 0.04,
        'regime_range_bb_width': 0.025,

        # 均值回归策略退出参数（SOL止损收紧，缩短超时）
        'meanrev_config': {
            'stop_loss_pct': 0.03,        # SOL均值回归止损3%
            'rsi_exit': 50,
            'bb_mid_exit': True,
            'max_hold_hours': 12,         # 超时12h（快进快出，减少微亏拖累）
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
    'volume_threshold': 1.2,
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
    'max_position_pct': 0.08,       # 默认单笔仓位占账户比例上限
    'min_body_ratio': 0.30,         # 默认阳线实体占比阈值
    'profit_target_atr': 6.0,       # 4h框架下提升止盈目标
    'min_profit_pct': 0.008,        # 最小盈利保护（默认0.8%）
    'breakeven_trigger': 0.02,      # 保本止损：盈利超2%后止损上移至成本价
    'breakeven_buffer': 0.003,     # 保本线上方留0.3%缓冲
    'min_signal_score': 40,        # 信号最低评分阈值

    # 时间衰减配置（4h框架下延长观察期，96h后才开始收紧）
    'time_decay': {
        'enabled': True,
        'intervals': [
            {'hours': 24, 'multiplier': 1.0},
            {'hours': 48, 'multiplier': 1.0},
            {'hours': 96, 'multiplier': 0.95},
            {'hours': 144, 'multiplier': 0.85},
            {'hours': float('inf'), 'multiplier': 0.75}
        ]
    },

    # Regime 市场状态识别参数（默认值）
    'regime_trend_adx': 22,
    'regime_range_adx': 22,
    'regime_trend_bb_width': 0.03,
    'regime_range_bb_width': 0.02,

    # 均值回归策略退出参数
    'meanrev_config': {
        'stop_loss_pct': 0.025,
        'rsi_exit': 50,
        'bb_mid_exit': True,
        'max_hold_hours': 12,
    },
}

# --- 风险控制参数 ---
MAX_TOTAL_EXPOSURE = 0.7   # 总仓位价值占账户总资产的最大比例 (70%)

# 熔断机制（按币种独立）
DRAWDOWN_FUSE = 0.08            # 单根K线跌幅超 8% 触发熔断（防闪崩）
DRAWDOWN_FUSE_MULTI_BAR = 0.10  # 多根K线累计跌幅超 10% 触发熔断（防阴跌）
DRAWDOWN_FUSE_MULTI_BAR_COUNT = 3  # 多根熔断检查的K线根数（检查最近3根累计跌幅）
FUSE_DURATION = 28800           # 熔断锁定时间：8 小时 (适配4h框架，覆盖2根K线)

# 账户级最大回撤保护
MAX_DRAWDOWN_PCT = 0.15    # 账户净值从最高点回撤 15% 暂停所有交易
DRAWDOWN_COOLDOWN = 86400  # 回撤冷却时间：24 小时 (防止过早恢复交易)

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
