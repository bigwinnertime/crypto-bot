#!/bin/bash
# daily_report.sh — 生成并发送每日报告
# 使用 set -euo pipefail 确保任一步骤失败立即停止，不会发送错误报告

set -euo pipefail

# 项目目录和 Python 路径（请根据实际部署修改）
PROJECT_DIR="/root/project/crypto-bot"
PYTHON_EXEC="/root/miniconda3/envs/crypto-bot/bin/python"

# 进入项目目录
cd "${PROJECT_DIR}"

# 1. 生成报告（失败则立即退出，不发送空报告）
${PYTHON_EXEC} report_generator.py > daily_report.txt

# 2. 检查报告文件是否非空
if [ ! -s daily_report.txt ]; then
    echo "$(date): 报告文件为空，跳过发送" >> cron_debug.log
    exit 1
fi

# 3. 发送 Telegram 报告
${PYTHON_EXEC} send_telegram_report_daily.py

# 4. 记录成功日志
echo "$(date): 报告发送成功" >> cron_debug.log
