import requests
import config
import logging
import threading
import time

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_msg_async(self, text):
        """异步发送消息，避免阻塞主线程"""
        def send_in_background():
            try:
                self.send_msg(text)
            except Exception as e:
                logger.error(f"异步发送TG通知异常: {e}")
        
        thread = threading.Thread(target=send_in_background, daemon=True)
        thread.start()
        # 等待一小段时间确保线程启动
        time.sleep(0.1)
        return True

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
                return False
            else:
                logger.info(f"✅ TG通知发送成功: {text[:50]}...")
                return True
        except requests.exceptions.Timeout:
            logger.error("TG通知超时")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"TG通知网络异常: {e}")
            return False
        except Exception as e:
            logger.error(f"TG通知未知异常: {e}")
            return False

    def send_msg_sync(self, text):
        """同步发送消息（用于测试）"""
        return self.send_msg(text)

# 为了方便，你可以保持函数名一致，减少主程序改动
def send_notification(title, content, sync=False):
    notifier = TelegramNotifier()
    logger = logging.getLogger(__name__)
    
    logger.info(f"📥 send_notification 被调用: title={title}")
    
    # 检查配置是否完整
    if not notifier.token or not notifier.chat_id:
        logger.error(f"❌ Telegram配置缺失: TOKEN={'有' if notifier.token else '无'}, CHAT_ID={'有' if notifier.chat_id else '无'}")
        return False
    
    # 将标题和内容组合，使用 Markdown 格式美化
    formatted_msg = f"🔔 *{title}*\n\n{content}"
    
    # 添加调试信息
    logger.info(f"📤 准备发送TG通知: {title}")
    logger.info(f"🔑 Token前8位: {notifier.token[:8]}... Chat ID: {notifier.chat_id}")
    
    # 根据参数选择同步或异步发送
    try:
        if sync:
            # 同步发送（用于测试）
            result = notifier.send_msg_sync(formatted_msg)
            logger.info(f"✅ 同步发送完成，结果: {result}")
            return result
        else:
            # 异步发送，避免阻塞主线程
            notifier.send_msg_async(formatted_msg)
            logger.info(f"✅ 异步发送线程已启动")
            return True
    except Exception as e:
        logger.error(f"❌ 启动发送线程失败: {e}")
        return False
