from telegram_notifier import send_notification
import os
import logging
from datetime import datetime

logger = logging.getLogger("TradingBot.DailyReport")

def send_daily_msg():
    # 使用脚本所在目录构建绝对路径，不依赖工作目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(script_dir, 'daily_report.txt')

    if not os.path.exists(report_path):
        logger.error(f"❌ 报告文件不存在: {report_path}")
        return

    if not os.path.getsize(report_path) > 0:
        logger.error("❌ 报告文件为空，跳过发送")
        return

    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()

    # #26: 用 datetime 替代废弃的 os.popen('date ...')，跨平台且无子进程开销
    subject = f"📅 交易机器人每日统计报告 - {datetime.now().strftime('%Y-%m-%d')}"

    send_notification(subject, report_content)
    logger.info("📧 每日报告已发出。")

if __name__ == "__main__":
    # 配置独立运行的日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler("daily_report.log", encoding='utf-8', errors='replace'),
            logging.StreamHandler()
        ]
    )
    send_daily_msg()
