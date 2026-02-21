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
        'stop_loss_pct': 0.03,     # 2% 固定硬止损
        'trailing_stop_pct': 0.02 # 1.5% 追踪止盈回调
    },
    'ETH/USDT': {
        'adx_threshold': 30,
        'rsi_oversold': 40,
        'rsi_overbought': 75,
        'trade_amount': 20,
        'stop_loss_pct': 0.05,     # 2% 固定硬止损
        'trailing_stop_pct': 0.4 # 1.5% 追踪止盈回调
    },
    'SOL/USDT': {
        'adx_threshold': 35,    # SOL 波动剧烈，需要 35 以上的高强度才追涨，防止被骗炮
        'rsi_oversold': 25,     # 跌得更深才买入
        'rsi_overbought': 80,   # 涨得更高才卖出
        'trade_amount': 10,      # 高波动币种减小单笔金额
        'stop_loss_pct': 0.05,     # 5% 固定硬止损
        'trailing_stop_pct': 0.04  # 4% 追踪止盈回调
    }
}

# 兜底配置（防止新增币种忘记写配置）
DEFAULT_CONFIG = {
    'adx_threshold': 25,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
    'trade_amount': 10,
    'stop_loss_pct': 0.03,
    'trailing_stop_pct': 0.02
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
