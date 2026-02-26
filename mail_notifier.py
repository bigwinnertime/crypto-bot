import smtplib
from email.mime.text import MIMEText
from email.header import Header
import logging
import config

logger = logging.getLogger("TradingBot.Notifier")

class Notifier:
    def __init__(self):
        # 从 config 模块统一获取配置
        self.smtp_server = config.EMAIL_SMTP_SERVER
        self.smtp_port = config.EMAIL_SMTP_PORT
        self.sender = config.EMAIL_SENDER
        self.password = config.EMAIL_PASSWORD
        self.receiver = config.EMAIL_RECEIVER

    def send_email(self, subject, content):
        # 安全检查：如果配置不全则不执行
        if not all([self.sender, self.password, self.receiver]):
            logger.warning("📩 邮件通知参数配置不完整，跳过发送。")
            return

        message = MIMEText(content, 'plain', 'utf-8')
        message['From'] = self.sender
        message['To'] = self.receiver
        message['Subject'] = Header(subject, 'utf-8')

        try:
            # 增加调试日志
            logger.debug(f"正在尝试连接 Gmail SMTP...")
            smtp_obj = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=10)
            
            # 打印与服务器交互的详细过程
            # smtp_obj.set_debuglevel(1) 
            
            smtp_obj.login(self.sender, self.password)
            smtp_obj.sendmail(self.sender, [self.receiver], message.as_string())
            smtp_obj.quit()
            logger.debug(f"📧 Gmail 通知成功: {subject}")
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ Gmail 认证失败：请检查是否使用了'应用专用密码'而非登录密码。")
        except Exception as e:
            logger.error(f"❌ Gmail 发送失败，具体原因: {e}")
