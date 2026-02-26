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
        # --- 第一步：先定义默认值，确保作用域覆盖整个函数 ---
        defaults = {
            "positions": {},
            "is_fused": False,
            "fuse_time": 0,
            "trade_history": [],
            "virtual_account": {
                "balance": 10000.0,
                "initial_balance": 10000.0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
                "trade_count": 0
            }
        }

        try:
            # 如果文件不存在，直接返回默认值
            if not os.path.exists(self.state_file):
                return defaults
                
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                
            # --- 第二步：使用默认值补全读取到的 state ---
            # 这种写法可以防止以后增加新功能时，旧的 JSON 文件缺少字段导致报错
            for key, value in defaults.items():
                if key not in state:
                    state[key] = value
                # 针对嵌套的 virtual_account 也要检查
                if key == "virtual_account":
                    for sub_key, sub_value in defaults["virtual_account"].items():
                        if sub_key not in state["virtual_account"]:
                            state["virtual_account"][sub_key] = sub_value
            
            return state

        except (json.JSONDecodeError, Exception) as e:
            # 如果文件损坏或其他异常，安全返回默认值
            print(f"⚠️ 读取状态文件异常，已加载默认设置: {e}")
            return defaults

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

        # 1. 计算当前相对于入场价的盈亏比例
        # (当前价 - 入场价) / 入场价
        current_profit = (current_price - pos['entry_price']) / pos['entry_price']

        # 2. 初始化/更新最高价
        if 'highest_price' not in pos:
            pos['highest_price'] = pos['entry_price']

        # 只要当前价破了新高，就更新最高价
        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price
            self.save_state()
            # 破新高时肯定没触发回撤，直接返回
            return None

        # 3. 计算从最高点的回撤比例
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        # 4. 计算从入场价的亏损比例 (用于硬止损)
        loss_from_entry = (pos['entry_price'] - current_price) / pos['entry_price']

        # --- 核心调整逻辑 ---

        # 规则 A：只有当盈利曾达到过激活门槛（例如超过追踪比例），才允许触发“追踪止盈”
        # 这样可以确保：如果没赚够钱，就不会因为微小波动触发“止盈”导致亏损
        # 逻辑：最高价涨幅必须 > 追踪比例 (spec['trailing_stop_pct'])
        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']

        if highest_profit_reached > spec['trailing_stop_pct']:
            if drawdown >= spec['trailing_stop_pct']:
                return f"追踪止盈 (回撤 {drawdown:.2%})"

        # 规则 B：无论盈利与否，只要触及底线，立即执行“固定止损”
        if loss_from_entry >= spec['stop_loss_pct']:
            return f"固定止损 (亏损 {loss_from_entry:.2%})"

        return None
