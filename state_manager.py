import threading
import json
import os
import logging

logger = logging.getLogger(__name__)

class SafeStateManager:
    """
    带线程锁的安全状态管理器
    确保 Telegram 线程（写入）与 主策略线程（读取/更新）互不干扰
    """
    def __init__(self, state_file='bot_state.json'):
        self.state_file = state_file
        self._lock = threading.Lock()  # 核心线程锁

    def read_state(self):
        """主程序安全读取"""
        with self._lock:
            try:
                if not os.path.exists(self.state_file):
                    return {}
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"❌ 线程安全读取失败: {e}")
                return {}

    def update_state(self, update_dict=None, func=None):
        """
        Telegram 远程控制专用：安全更新状态
        :param update_dict: 直接更新的键值对
        :param func: 复杂逻辑的回调函数
        """
        with self._lock:
            try:
                # 1. 读取当前最新状态
                state = {}
                if os.path.exists(self.state_file):
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)

                # 2. 执行更新逻辑
                if update_dict:
                    state.update(update_dict)
                if func:
                    state = func(state)

                # 3. 原子化写入
                with open(self.state_file, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=4, ensure_ascii=False)
                return True
            except Exception as e:
                logger.error(f"❌ 线程安全写入失败: {e}")
                return False

# 全局单例对象
state_mgr = SafeStateManager(state_file='bot_state.json')
