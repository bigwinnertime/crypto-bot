import json
import os
import time
import logging
import threading

logger = logging.getLogger("TradingBot.Risk")

class RiskManager:
    # 创建一个类级别的锁（单例模式），确保所有实例共享同一把锁
    _file_lock = threading.Lock()

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
        with self._file_lock:
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
                logger.warning(f"⚠️ 读取状态文件异常，已加载默认设置: {e}")
                return defaults

    def save_state(self):
        """持久化保存当前持仓和熔断状态"""
        with self._file_lock:
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
            # 使用配置文件中的熔断持续时间
            import config
            fuse_duration = getattr(config, 'FUSE_DURATION', 14400)  # 默认4小时
            elapsed = time.time() - self.state['fuse_time']
            if elapsed > fuse_duration:
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
        
        # --- 1. 动态优先级参数获取 ---
        import config
        # 获取 Telegram 运行时的动态配置 (第一优先级)
        runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
        # 获取币种特定配置 (第二优先级)
        spec_cfg = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

        # 最终采用的比例：runtime -> spec -> default
        ts_pct = runtime_cfg.get('trailing_stop_pct', spec_cfg.get('trailing_stop_pct', 0.01))
        sl_pct = runtime_cfg.get('stop_loss_pct', spec_cfg.get('stop_loss_pct', 0.02))

        # --- 2. 更新最高价 ---
        if 'highest_price' not in pos:
            pos['highest_price'] = pos['entry_price']

        # 只要当前价破了新高，就更新最高价
        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price
            # 注意：这里调用的是带锁的 save_state
            self.save_state()
            return None

        # --- 3. 计算比例 ---
        # 计算从最高点的回撤比例
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        # 计算从入场价的亏损比例 (用于硬止损)
        loss_from_entry = (pos['entry_price'] - current_price) / pos['entry_price']
        # 计算最高点曾达到的涨幅
        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']

        # --- 4. 判定逻辑 ---

        # 规则 A：追踪止盈 (使用 ts_pct)
        # 逻辑：只有当最高点涨幅超过了追踪比例，才开启回撤监控
        if highest_profit_reached > ts_pct:
            if drawdown >= ts_pct:
                return f"追踪止盈 (回撤 {drawdown:.2%}, 设定阈值 {ts_pct:.2%})"

        # 规则 B：固定止损 (使用 sl_pct)
        if loss_from_entry >= sl_pct:
            return f"固定止损 (亏损 {loss_from_entry:.2%}, 设定阈值 {sl_pct:.2%})"

        return None

    def update_runtime_config(self, symbol, key, value):
        """
        安全地更新运行时配置（如 SL, TS 比例）
        symbol: 币种名，如 'BTC/USDT'
        key: 配置项名称，如 'trailing_stop_pct'
        value: 新的数值，如 0.01
        """
        with self._file_lock:
            # 确保 runtime_config 结构存在
            if 'runtime_config' not in self.state:
                self.state['runtime_config'] = {}
            if symbol not in self.state['runtime_config']:
                self.state['runtime_config'][symbol] = {}
                
            # 写入新参数
            self.state['runtime_config'][symbol][key] = value
            
            # 立即持久化到 bot_state.json
            self.save_state()
            logger.info(f"⚙️ 远程配置更新: {symbol} {key} = {value}")

    def remote_set_fuse(self, status: bool):
        """供 Telegram 线程安全设置熔断"""
        with self._file_lock:
            self.state['is_fused'] = status
            self.state['fuse_time'] = time.time() if status else 0
            self.save_state()

    def execute_buy_update(self, symbol, price, amount, cost, mode):
        """统一封装：买入后的状态更新逻辑"""
        with self._file_lock:
            # 更新持仓字典
            self.state['positions'][symbol] = {
                "entry_price": price,
                "amount": amount,
                "cost": cost,
                "highest_price": price,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            # 扣除虚拟账户余额 (已经在 _execute_order 里处理过的可以不重复扣，
            # 但为了逻辑严密，建议状态更新统一由此处负责)
            self.save_state()

    def execute_sell_update(self, symbol, price, mode):
        """统一封装：卖出后的状态更新逻辑（计算PnL、存入历史、清空持仓）"""
        with self._file_lock:
            if symbol not in self.state['positions']:
                return None
                
            pos = self.state['positions'][symbol]
            pnl_val = (price - pos['entry_price']) * pos['amount']
            pnl_pct = (price / pos['entry_price'] - 1) * 100
            
            # 1. 构建交易记录
            trade_record = {
                "symbol": symbol,
                "entry_price": pos['entry_price'],
                "sell_price": price,
                "amount": pos['amount'],
                "pnl_amount": pnl_val,
                "pnl_pct": pnl_pct,
                "exit_reason": mode,
                "sell_time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # 2. 存入历史记录
            if 'trade_history' not in self.state:
                self.state['trade_history'] = []
            self.state['trade_history'].append(trade_record)
            
            # 3. 更新虚拟账户总统计
            self.state['virtual_account']['total_pnl'] += pnl_val
            self.state['virtual_account']['trade_count'] += 1
            
            # 4. 移除持仓并保存
            del self.state['positions'][symbol]
            self.save_state()
            
            return pnl_pct  # 返回收益率供通知使用
