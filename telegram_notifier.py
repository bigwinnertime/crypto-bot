import requests
import config
import logging
import time
import html

logger = logging.getLogger(__name__)

# Telegram HTML 模式支持的格式标签白名单。
# send_notification 会对动态内容（reason/价格/数值）做 HTML 转义，
# 但保留这些格式标签，避免调用者手写的 <b></b> 被破坏。
# 修复：reason 含未转义的 <（如 "ADX=15.3<30.0)"）曾被 Telegram 当成非法标签导致 HTTP 400。
_ALLOWED_HTML_TAGS = ('b', '/b', 'i', '/i', 'u', '/u', 's', '/s',
                      'code', '/code', 'pre', '/pre')


def _escape_html_keep_tags(text):
    """转义 HTML 特殊字符（< > &），但保留白名单内的格式标签。

    动态数据（如 exit_reason、数值）里的 < 会被转义为 &lt;，Telegram 正常显示为 <；
    调用者写的 <b> 等格式标签会被恢复，保持加粗/斜体等样式。
    """
    if not isinstance(text, str):
        text = str(text)
    escaped = html.escape(text, quote=False)
    for tag in _ALLOWED_HTML_TAGS:
        escaped = escaped.replace(f'&lt;{tag}&gt;', f'<{tag}>')
    return escaped

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_msg(self, text, max_retries=3, backoff_base=2):
        """发送纯文本消息，带自动重试机制"""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(self.base_url, data=payload, timeout=10)
                
                # 成功
                if response.status_code == 200:
                    logger.info(f"✅ TG通知发送成功: {text[:50]}...")
                    return True
                
                # 服务器端错误 (5xx) 可重试
                if 500 <= response.status_code < 600:
                    if attempt < max_retries:
                        wait_time = backoff_base ** attempt
                        logger.warning(f"TG API返回{response.status_code}，第{attempt}次重试，等待{wait_time}秒...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"TG通知发送失败，已重试{max_retries}次: HTTP {response.status_code} - {response.text}")
                        return False
                
                # 客户端错误 (4xx) 不重试
                else:
                    logger.error(f"TG通知发送失败: HTTP {response.status_code} - {response.text}")
                    return False
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    wait_time = backoff_base ** attempt
                    logger.warning(f"TG通知超时，第{attempt}次重试，等待{wait_time}秒...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"TG通知超时，已重试{max_retries}次")
                    return False
                    
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    wait_time = backoff_base ** attempt
                    logger.warning(f"TG通知网络异常，第{attempt}次重试，等待{wait_time}秒: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"TG通知网络异常，已重试{max_retries}次: {e}")
                    return False
                    
            except Exception as e:
                logger.error(f"TG通知未知异常: {e}")
                return False
        
        return False


# 为了方便，你可以保持函数名一致，减少主程序改动
def send_notification(title, content, **kwargs):
    """发送 Telegram 通知，**kwargs 用于兼容未来扩展参数"""
    notifier = TelegramNotifier()
    logger = logging.getLogger(__name__)
    
    logger.info(f"📥 send_notification 被调用: title={title}")
    
    # 检查配置是否完整
    if not notifier.token or not notifier.chat_id:
        logger.error(f"❌ Telegram配置缺失: TOKEN={'有' if notifier.token else '无'}, CHAT_ID={'有' if notifier.chat_id else '无'}")
        return False
    
    # 将标题和内容组合，使用 HTML 格式美化（Markdown 对 Emoji 兼容性差）
    # title/content 都是动态数据，先转义 < > & 防止 Telegram HTML 解析失败，
    # 再由 _escape_html_keep_tags 保留 <b></b> 等格式标签
    safe_title = _escape_html_keep_tags(title)
    safe_content = _escape_html_keep_tags(content)
    formatted_msg = f"🔔 <b>{safe_title}</b>\n\n{safe_content}"
    
    # 添加调试信息
    logger.info(f"📤 准备发送TG通知: {title}")
    logger.info(f"🔑 Token前8位: {notifier.token[:8]}... Chat ID: {notifier.chat_id}")
    
    # 同步发送
    try:
        result = notifier.send_msg(formatted_msg)
        logger.info(f"✅ 发送完成，结果: {result}")
        return result
    except Exception as e:
        logger.error(f"❌ 发送失败: {e}")
        return False

if __name__ == "__main__":
    # 配置独立运行的日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler("telegram_notifier.log", encoding='utf-8', errors='replace'),
            logging.StreamHandler()
        ]
    )
    
    # 测试代码
    logger.info("🧪 开始测试 Telegram 通知功能...")
    
    # 测试发送
    test_title = "🧪 测试消息"
    test_content = "这是一条测试消息，用于验证 Telegram 通知功能是否正常工作。\n\n时间: " + __import__('time').strftime('%Y-%m-%d %H:%M:%S')
    
    logger.info("📤 测试发送...")
    result = send_notification(test_title, test_content)
    logger.info(f"✅ 发送结果: {result}")
    
    logger.info("🏁 测试完成!")
