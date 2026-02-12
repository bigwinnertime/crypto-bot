import json
import os
import time
import logging

logger = logging.getLogger("TradingBot.Risk")

class RiskManager:
    def __init__(self, state_file="bot_state.json", max_exposure=0.7, fuse_limit=0.05):
        self.state_file = state_file
        self.max_exposure = max_exposure
        self.fuse_limit = fuse_limit
        self.state = self.load_state()

        # --- 新增：初始化虚拟账户 ---
        if 'virtual_account' not in self.state:
            self.state['virtual_account'] = {
                'balance': 10000.0,      # 初始虚拟资金 1万 USDT
                'initial_balance': 10000.0,
                'total_pnl': 0.0,        # 累计盈亏金额
                'total_fees': 0.0,
                'trade_count': 0         # 总交易次数
            }

    def load_state(self):
        """从 JSON 加载机器人记忆"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"读取状态文件失败，创建新状态: {e}")
        return {"positions": {}, "is_fused": False, "fuse_time": 0}

    def save_state(self):
        """持久化保存当前持仓和熔断状态"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                # ensure_ascii=False 是关键，防止中文保存为 \uXXXX
                json.dump(self.state, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")

    def check_circuit_breaker(self, symbol, df):
        """熔断机制：防止在暴跌中持续接飞刀"""
        if len(df) < 2: return False
        
        # 计算当前 K 线相对于上一根的涨跌幅
        change = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
        
        # 触发熔断条件
        if change < -self.fuse_limit:
            logger.error(f" 发生异常暴跌 ({change:.2%})！启动熔断保护。")
            self.state['is_fused'] = True
            self.state['fuse_time'] = time.time()
            self.save_state()
            return True
        
        # 检查熔断是否过期
        if self.state['is_fused']:
            elapsed = time.time() - self.state['fuse_time']
            if elapsed > 14400: # 4小时
                logger.info("🛡️ 熔断冷却期结束，系统尝试恢复监控。")
                self.state['is_fused'] = False
                self.save_state()
            else:
                return True
                
        return self.state['is_fused']

    def can_open_position(self, symbol, total_balance):
        """下单前的最后一道防线"""
        # 1. 检查是否已有持仓
        if symbol in self.state['positions']:
            return False
            
        # 2. 检查总仓位暴露
        current_cost = sum(p['cost'] for p in self.state['positions'].values())
        if total_balance > 0 and (current_cost / total_balance) > self.max_exposure:
            logger.warning(f"⚠️ 仓位占用过高 ({(current_cost/total_balance):.2%})，拒绝买入 {symbol}")
            return False
            
        return True
    def update_trailing_stop(self, symbol, current_price):
        """核心逻辑：更新最高价并检查动态止损"""
        if symbol not in self.state['positions']:
            return None

        pos = self.state['positions'][symbol]
        # 获取该币种特有配置
        import config
        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

        # 1. 初始化/更新该仓位见过的最高价
        if 'highest_price' not in pos:
            pos['highest_price'] = pos['entry_price']

        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price
            self.save_state()
            return None

        # 2. 计算回撤比例
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        # 3. 计算亏损比例 (相对于入场价)
        loss_from_entry = (pos['entry_price'] - current_price) / pos['entry_price']

        # 检查是否触发 追踪止盈 或 固定止损
        if drawdown >= spec['trailing_stop_pct']:
            return f"追踪止盈 (回撤 {drawdown:.2%})"

        if loss_from_entry >= spec['stop_loss_pct']:
            return f"固定止损 (亏损 {loss_from_entry:.2%})"

        return None
