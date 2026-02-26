import telebot
import json
import config
import os
from dotenv import load_dotenv

load_dotenv()
bot = telebot.TeleBot(os.getenv('TELEGRAM_TOKEN'))
ADMIN_ID = int(os.getenv('TELEGRAM_CHAT_ID'))

def auth(message):
    return message.from_user.id == ADMIN_ID

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not auth(message): return
    help_text = (
        "🤖 交易机器人控制台\n\n"
        "/status - 查看当前持仓与状态\n"
        "/set_sl [币种] [比例] - 修改止损(例: /set_sl SOL 0.03)\n"
        "/set_ts [币种] [比例] - 修改追踪止盈\n"
        "/fuse - 手动紧急熔断"
    )
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['set_sl'])
def update_stop_loss(message):
    if not auth(message): return
    try:
        # 指令解析: /set_sl SOL/USDT 0.05
        _, symbol, value = message.text.split()
        if symbol in config.STRATEGY_CONFIG:
            config.STRATEGY_CONFIG[symbol]['stop_loss_pct'] = float(value)
            bot.reply_to(message, f"✅ 已更新 {symbol} 止损比例为: {value}")
        else:
            bot.reply_to(message, "❌ 找不到该币种配置")
    except Exception as e:
        bot.reply_to(message, f"⚠️ 格式错误: /set_sl BTC/USDT 0.02")

# 启动监听
def start_remote_listener():
    print("📡 远程调参监听器已启动...")
    bot.infinity_polling()
