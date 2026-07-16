#!/bin/bash

# 清理 Telegram Bot 锁文件
echo "🧹 清理 Telegram Bot 锁文件..."

# 删除锁文件
if [ -f "telegram_bot.lock" ]; then
    echo "🗑️  删除 telegram_bot.lock"
    rm -f telegram_bot.lock
fi

# 查找并终止可能存在的僵尸进程
# #25: 原模式 "crypto-bot.*remote_control" 不匹配实际进程（主进程是 python bot_engine.py，
#      remote_control 只是它 import 的模块）。改为匹配主入口脚本。
echo "🔍 检查僵尸进程..."
pkill -f "python.*bot_engine\.py" 2>/dev/null || echo "✅ 无僵尸进程"

echo "✅ 清理完成"
