#!/bin/bash

# 清理 Telegram Bot 锁文件
echo "🧹 清理 Telegram Bot 锁文件..."

# 删除锁文件
if [ -f "telegram_bot.lock" ]; then
    echo "🗑️  删除 telegram_bot.lock"
    rm -f telegram_bot.lock
fi

# 查找并终止可能存在的僵尸进程
echo "🔍 检查僵尸进程..."
pkill -f "crypto-bot.*remote_control" 2>/dev/null || echo "✅ 无僵尸进程"

echo "✅ 清理完成"
