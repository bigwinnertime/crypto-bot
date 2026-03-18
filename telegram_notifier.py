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
            response = requests.post(self.base_url, data=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"TG通知发送失败: HTTP {response.status_code} - {response.text}")
            else:
                logger.info(f"✅ TG通知发送成功: {text[:50]}...")
        except requests.exceptions.Timeout:
            logger.error("TG通知超时")
        except requests.exceptions.RequestException as e:
            logger.error(f"TG通知网络异常: {e}")
        except Exception as e:
            logger.error(f"TG通知未知异常: {e}")

# 为了方便，你可以保持函数名一致，减少主程序改动
def send_notification(title, content):
    notifier = TelegramNotifier()
    
    # 检查配置是否完整
    if not notifier.token or not notifier.chat_id:
        logger.error("❌ Telegram配置缺失: TOKEN或CHAT_ID未设置")
        return False
    
    # 将标题和内容组合，使用 Markdown 格式美化
    formatted_msg = f"🔔 *{title}*\n\n{content}"
    
    # 添加调试信息
    logger.info(f"📤 准备发送TG通知: {title}")
    logger.debug(f"Token前8位: {notifier.token[:8]}... Chat ID: {notifier.chat_id}")
    
    notifier.send_msg(formatted_msg)
    return True
