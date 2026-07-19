#!/bin/bash
# cleanup_bot.sh — 安全清理锁文件和僵尸进程
# 使用 PID 文件精确管理进程，避免 pkill 误杀正在运行的 bot

set -euo pipefail

# 获取脚本所在目录（构建绝对路径）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="${SCRIPT_DIR}/telegram_bot.lock"
PID_FILE="${SCRIPT_DIR}/bot_engine.pid"

echo "🧹 清理 Telegram Bot 锁文件和僵尸进程..."

# 1. 删除锁文件
if [ -f "${LOCK_FILE}" ]; then
    echo "🗑️  删除 ${LOCK_FILE}"
    rm -f "${LOCK_FILE}"
fi

# 2. 检查 PID 文件，精确清理僵尸进程
if [ -f "${PID_FILE}" ]; then
    PID=$(cat "${PID_FILE}")
    if kill -0 "${PID}" 2>/dev/null; then
        # 检查进程是否是 python bot_engine.py（防止 PID 复用）
        CMDLINE=$(cat "/proc/${PID}/cmdline" 2>/dev/null || ps -p "${PID}" -o command= 2>/dev/null || echo "")
        if echo "${CMDLINE}" | grep -q "bot_engine"; then
            echo "⚠️ 发现 bot_engine 进程 (PID: ${PID})，正在终止..."
            kill "${PID}" 2>/dev/null || true
            # 等待2秒让进程优雅退出
            sleep 2
            # 如果还活着，强制杀死
            if kill -0 "${PID}" 2>/dev/null; then
                echo "⚠️ 进程未退出，强制终止..."
                kill -9 "${PID}" 2>/dev/null || true
            fi
            echo "✅ 僵尸进程已清理 (PID: ${PID})"
        else
            echo "ℹ️  PID ${PID} 不是 bot_engine 进程（PID 可能已被复用），跳过"
        fi
    else
        echo "✅ PID ${PID} 已不存在，仅清理 PID 文件"
    fi
    rm -f "${PID_FILE}"
else
    # PID 文件不存在时，用 pkill 作为后备（但加确认提示）
    echo "ℹ️  未找到 PID 文件，检查是否有残留进程..."
    # 仅查找不终止，确认后再手动操作
    PIDS=$(pgrep -f "python.*bot_engine\.py" 2>/dev/null || true)
    if [ -n "${PIDS}" ]; then
        echo "⚠️ 发现 bot_engine 进程（未通过 PID 文件管理）:"
        echo "${PIDS}" | while read pid; do
            echo "  PID: ${pid}  $(ps -p ${pid} -o command= 2>/dev/null || echo 'unknown')"
        done
        echo "如需终止，请手动执行: kill <PID>"
    else
        echo "✅ 无残留进程"
    fi
fi

echo "✅ 清理完成"
