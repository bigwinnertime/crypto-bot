#!/bin/bash
# 1. 设置项目根目录的绝对路径（请根据实际情况修改）
PROJECT_DIR="/root/project/crypto-bot"
# 2. 设置 Conda 环境中 Python 的绝对路径
PYTHON_EXEC="/root/miniconda3/envs/crypto-bot/bin/python"

# 1. 进入程序所在目录（请修改为你的实际绝对路径）
cd ${PROJECT_DIR}

# 2. 激活虚拟环境 (如果你使用了虚拟环境，请取消下面这行的注释)
#source venv/bin/activate

# 3. 生成报告
# 运行 report_generator.py 并将输出重定向到文件
${PYTHON_EXEC} report_generator.py > daily_report.txt

# 4. 调用 Python 发送telegram
${PYTHON_EXEC} send_telegram_report_daily.py

# 5. (可选) 清理日志或备份报告
# cp daily_report.txt ./history/report_$(date +%Y%m%d).txt
# 检查执行结果
if [ $? -eq 0 ]; then
    echo "$(date): 报告发送成功" >> cron_debug.log
else
    echo "$(date): 报告发送失败" >> cron_debug.log
fi
