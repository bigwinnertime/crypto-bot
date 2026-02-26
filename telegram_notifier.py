import requests
import config
import logging

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_msg(self, text):
        """发送纯文本消息"""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"  # 支持加粗、代码块等格式
        }
        try:
            response = requests.post(self.base_url, data=payload)
            if response.status_code != 200:
                logger.error(f"TG通知发送失败: {response.text}")
        except Exception as e:
            logger.error(f"TG通知异常: {e}")

# 为了方便，你可以保持函数名一致，减少主程序改动
def send_notification(title, content):
    notifier = TelegramNotifier()
    # 将标题和内容组合，使用 Markdown 格式美化
    formatted_msg = f"🔔 *{title}*\n\n{content}"
    notifier.send_msg(formatted_msg)
