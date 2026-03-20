from telegram_notifier import send_notification
import os
import logging

logger = logging.getLogger("TradingBot.DailyReport")

def send_daily_msg():
    report_path = 'daily_report.txt'
    
    if not os.path.exists(report_path):
        logger.error("❌ 报告文件不存在，停止发送。")
        return

    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()

    # 实例化你之前的通知模块
    #n = TelegramNotifier()
    subject = f"📅 交易机器人每日统计报告 - {os.popen('date +%Y-%m-%d').read().strip()}"
    
    # 发送邮件
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
