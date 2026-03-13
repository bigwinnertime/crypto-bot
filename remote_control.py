import telebot
import json
import config
import os
import ccxt
import time
import logging
from dotenv import load_dotenv
from risk_manager import RiskManager
from state_manager import state_mgr

logger = logging.getLogger("TradingBot.Remote")
risk = RiskManager()

load_dotenv()

# 初始化 Bot
bot = telebot.TeleBot(os.getenv('TELEGRAM_TOKEN'))
ADMIN_ID = int(os.getenv('TELEGRAM_CHAT_ID'))

# 提取私钥并处理换行符
raw_key = os.getenv('BINANCE_SECRET_KEY', '')

# 关键点：将字符串中的 "\n" 替换为真正的换行字符，并去掉可能误加的引号
formatted_key = raw_key.replace('\\n', '\n').strip('"').strip("'")


# 初始化交易所 (以币安为例，可根据需要更换)
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': formatted_key,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot', 'secretType': 'ed25519'}
})

def auth(message):
    return message.from_user.id == ADMIN_ID

# --- 1. 状态查询处理器 ---
@bot.message_handler(commands=['status'])
def get_status(message):
    if not auth(message): return
    
    loading_msg = bot.reply_to(message, "⏳ 正在同步账户数据...")
    
    try:
        # A. 模拟/虚拟账户状态 (从你的 config 或本地变量读取)
        v_status = "📉 **虚拟账户 (Paper Trading)**\n"
        for symbol, cfg in config.STRATEGY_CONFIG.items():
            v_status += f"🔸 {symbol}: SL:{cfg.get('stop_loss_pct')}, TS:{cfg.get('trailing_stop_pct')}\n"

        # B. 真实交易所账户状态 (CCXT)
        #bot.reply_to(message, formatted_key)
        balance = exchange.fetch_balance()
        r_status = "\n💰 **真实账户 (Exchange)**\n"
        # 仅显示余额大于 0 的币种
        active_balances = {k: v for k, v in balance['total'].items() if v > 0}
        
        if not active_balances:
            r_status += "空仓中"
        else:
            for coin, amt in active_balances.items():
                r_status += f"🔹 {coin}: {amt:.4f}\n"

        report = f"🤖 **交易机器人账户实时状态**\n{'-'*20}\n{v_status}{r_status}"
        bot.edit_message_text(report, chat_id=loading_msg.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
        
    except Exception as e:
        bot.edit_message_text(f"⚠️ 获取状态失败: {str(e)}", chat_id=loading_msg.chat.id, message_id=loading_msg.message_id)

# --- 2. 止损设置处理器 (已存在，增强健壮性) ---
@bot.message_handler(commands=['set_sl'])
def update_stop_loss(message):
    if not auth(message): return
    try:
        _, symbol, value = message.text.split()
        symbol = symbol.upper()
        sl_val = float(value)

        # 定义一个闭包函数来处理复杂的嵌套 JSON 更新
        def update_logic(state):
            # 如果 state 里没有运行时配置，则初始化
            if 'runtime_config' not in state:
                state['runtime_config'] = {}
            if symbol not in state['runtime_config']:
                state['runtime_config'][symbol] = {}
            
            state['runtime_config'][symbol]['stop_loss_pct'] = sl_val
            return state

        if state_mgr.update_state(func=update_logic):
            bot.reply_to(message, f"✅ **安全同步成功**\n`{symbol}` 止损已更新为: `{sl_val*100}%`")
        else:
            bot.reply_to(message, "❌ 写入状态文件时发生锁死或错误")
    except Exception as e:
        bot.reply_to(message, f"⚠️ 格式错误或执行异常: {e}")

# --- 3. 查看追踪止盈状态处理器 ---
@bot.message_handler(commands=['trailing_status'])
def get_trailing_status(message):
    if not auth(message): return
    
    try:
        # 获取所有持仓的状态
        positions = risk.state['positions']
        if not positions:
            bot.reply_to(message, "📊 当前无持仓")
            return
        
        status_report = "📊 **追踪止盈状态报告**\n\n"
        
        for symbol in positions:
            # 模拟获取当前价格（实际应该从交易所获取）
            current_price = positions[symbol]['highest_price']  # 简化处理
            
            status = risk.get_trailing_stop_status(symbol, current_price)
            if status:
                status_report += f"🔸 **{symbol}**\n"
                status_report += f"入场价: {status['entry_price']:.2f}\n"
                status_report += f"当前价: {status['current_price']:.2f}\n"
                status_report += f"最高价: {status['highest_price']:.2f}\n"
                status_report += f"最高盈利: {status['highest_profit_pct']:+.2%}\n"
                status_report += f"当前回撤: {status['current_drawdown_pct']:+.2%}\n"
                
                if status['active_trigger_drawdown']:
                    status_report += f"触发回撤阈值: {status['active_trigger_drawdown']:.2%}"
                    if status['time_multiplier']:
                        status_report += f" (时间系数: {status['time_multiplier']:.2f})\n"
                        status_report += f"调整后阈值: {status['adjusted_trigger_drawdown']:.2%}\n"
                    else:
                        status_report += "\n"
                else:
                    status_report += "追踪止盈: 未激活\n"
                
                status_report += f"持仓时间: {status['holding_time_hours']:.1f}小时\n\n"
        
        bot.reply_to(message, status_report, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ 获取状态失败: {e}")

# --- 4. 追踪止盈设置处理器 ---
@bot.message_handler(commands=['set_ts'])
def update_trailing_stop(message):
    if not auth(message): return
    try:
        _, symbol, value = message.text.split()
        symbol = symbol.upper()
        ts_val = float(value)

        # 调用 RiskManager 封装的安全锁方法
        risk.update_runtime_config(symbol, 'trailing_stop_pct', ts_val)
        
        bot.reply_to(message, 
            f"✅ **追踪止盈同步成功**\n"
            f"🔹 币种: `{symbol}`\n"
            f"📈 新比例: `{ts_val*100}%`\n"
            f"💾 设置已持久化，重启不丢失。", 
            parse_mode='Markdown'
        )
    except Exception as e:
        bot.reply_to(message, "⚠️ 格式: `/set_ts BTC/USDT 0.01`", parse_mode='Markdown')

# --- 4. 紧急熔断指令 (/fuse) ---
@bot.message_handler(commands=['fuse'])
def handle_emergency_fuse(message):
    if not auth(message): return
    
    # 调用封装好的线程安全方法
    risk.remote_set_fuse(True)
    
    bot.reply_to(message, "🚨 **紧急熔断已启动！**\n状态已写入 `bot_state.json`。主程序将在下一轮循环检测到并停止交易。")

# --- 5. 解除熔断指令 (/unfuse) ---
@bot.message_handler(commands=['unfuse'])
def handle_unfuse(message):
    if not auth(message): return
    
    # 调用方法解除熔断
    risk.remote_set_fuse(False)
    
    bot.reply_to(message, "✅ **熔断已解除**\n机器人将恢复正常扫描信号。")

# 启动监听
def start_remote_listener():
    logger.info("📡 远程调参监听器已启动...")
    bot.infinity_polling(timeout=90, long_polling_timeout=5)
