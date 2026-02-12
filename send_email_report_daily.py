from notifier import Notifier
import os

def send_daily_email():
    report_path = 'daily_report.txt'
    
    if not os.path.exists(report_path):
        print("❌ 报告文件不存在，停止发送。")
        return

    with open(report_path, 'r', encoding='utf-8') as f:
        report_content = f.read()

    # 实例化你之前的通知模块
    n = Notifier()
    subject = f"📅 交易机器人每日统计报告 - {os.popen('date +%Y-%m-%d').read().strip()}"
    
    # 发送邮件
    n.send_email(subject, report_content)
    print("📧 每日报告邮件已发出。")

if __name__ == "__main__":
    send_daily_email()
