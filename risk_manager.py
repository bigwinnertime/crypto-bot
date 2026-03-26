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
    def update_trailing_stop(self, symbol, current_price, df=None):
        """核心逻辑：分阶段追踪止盈 + 时间衰减 + ATR动态止损"""
        if symbol not in self.state['positions']:
            return None

        pos = self.state['positions'][symbol]
        
        # --- 1. 动态优先级参数获取 ---
        try:
            import config
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        except ImportError:
            # 如果导入失败，使用运行时配置或默认配置
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = {
                'stop_loss_pct': 0.03,
                'trailing_stops': [
                    {
                        'profit_threshold': 0.02,      # 盈利2%时开启追踪
                        'trigger_drawdown': 0.015,    # 回撤1.5%时触发卖出
                        'trailing_pct': 0.015         # 向后兼容
                    },
                    {
                        'profit_threshold': 0.05,      # 盈利5%时开启追踪
                        'trigger_drawdown': 0.02,     # 回撤2%时触发卖出
                        'trailing_pct': 0.02
                    },
                    {
                        'profit_threshold': 0.10,      # 盈利10%时开启追踪
                        'trigger_drawdown': 0.025,    # 回撤2.5%时触发卖出
                        'trailing_pct': 0.025
                    },
                ],
                'time_decay': {
                    'enabled': True,
                    'intervals': [
                        {'hours': 1, 'multiplier': 1.0},
                        {'hours': 4, 'multiplier': 0.8},
                        {'hours': 12, 'multiplier': 0.6},
                        {'hours': 24, 'multiplier': 0.5},
                        {'hours': float('inf'), 'multiplier': 0.4}
                    ]
                },
                'use_atr_stop': True,
                'atr_multiplier': 2.0
            }

        # 最终采用的比例：runtime -> spec -> default
        sl_pct = runtime_cfg.get('stop_loss_pct', spec_cfg.get('stop_loss_pct', 0.02))
        trailing_stops = runtime_cfg.get('trailing_stops', spec_cfg.get('trailing_stops', []))
        time_decay_cfg = runtime_cfg.get('time_decay', spec_cfg.get('time_decay', {}))
        use_atr_stop = runtime_cfg.get('use_atr_stop', spec_cfg.get('use_atr_stop', False))
        atr_multiplier = runtime_cfg.get('atr_multiplier', spec_cfg.get('atr_multiplier', 2.0))

        # --- 2. 更新最高价 ---
        if 'highest_price' not in pos:
            pos['highest_price'] = pos['entry_price']

        # 只要当前价破了新高，就更新最高价
        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price
            # 注意：这里调用的是带锁的 save_state
            self.save_state()
            return None

        # --- 3. 计算基础比例 ---
        # 计算从最高点的回撤比例
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        # 计算从入场价的亏损比例 (用于硬止损)
        loss_from_entry = (pos['entry_price'] - current_price) / pos['entry_price']
        # 计算最高点曾达到的涨幅
        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']
        
        # --- 3.5 ATR动态止损计算 ---
        atr_stop_price = None
        if use_atr_stop and df is not None:
            try:
                from ta.volatility import AverageTrueRange
                atr = AverageTrueRange(df['high'], df['low'], df['close'], 
                                       window=14).average_true_range().iloc[-1]
                # ATR止损价 = 入场价 - ATR * 倍数
                atr_stop_price = pos['entry_price'] - atr * atr_multiplier
                logger.debug(f"{symbol} ATR止损: 入场价{pos['entry_price']:.2f} - ATR{atr:.2f} × {atr_multiplier} = {atr_stop_price:.2f}")
            except Exception as e:
                logger.warning(f"ATR计算失败: {e}")

        # --- 4. 分阶段追踪止盈逻辑 ---
        active_trigger_drawdown = None
        
        # 按盈利阈值从高到低排序，找到最合适的触发回撤比例
        sorted_stops = sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True)
        
        for stop_config in sorted_stops:
            if highest_profit_reached >= stop_config['profit_threshold']:
                # 新的分离式逻辑：使用独立的trigger_drawdown
                active_trigger_drawdown = stop_config.get('trigger_drawdown', stop_config.get('trailing_pct', 0.02))
                break
        
        # 如果没有达到任何盈利门槛，不启用追踪止盈
        if active_trigger_drawdown is None:
            logger.debug(f"{symbol} 盈利 {highest_profit_reached:.2%}，未达到追踪止盈门槛")
        else:
            # 应用时间衰减
            if time_decay_cfg.get('enabled', False):
                time_multiplier = self._calculate_time_multiplier(pos, time_decay_cfg)
                adjusted_trigger_drawdown = active_trigger_drawdown * time_multiplier
                
                logger.debug(f"{symbol} 追踪止盈: 原始{active_trigger_drawdown:.2%} -> 调整后{adjusted_trigger_drawdown:.2%} (时间系数{time_multiplier:.2f})")
                active_trigger_drawdown = adjusted_trigger_drawdown
            
            # 检查是否触发追踪止盈
            if drawdown >= active_trigger_drawdown:
                return f"追踪止盈 (回撤 {drawdown:.2%}, 设定阈值 {active_trigger_drawdown:.2%})"

        # --- 5. 固定止损检查（支持ATR动态止损） ---
        # 优先使用ATR止损
        if atr_stop_price and current_price <= atr_stop_price:
            atr_loss_pct = (pos['entry_price'] - current_price) / pos['entry_price']
            return f"ATR动态止损 (价格{current_price:.2f} <= ATR止损线{atr_stop_price:.2f}, 亏损{atr_loss_pct:.2%})"
        
        # 否则使用固定百分比止损
        if loss_from_entry >= sl_pct:
            return f"固定止损 (亏损 {loss_from_entry:.2%}, 设定阈值 {sl_pct:.2%})"

        return None

    def _calculate_time_multiplier(self, position, time_decay_cfg):
        """计算时间衰减系数"""
        try:
            # 获取持仓时间
            entry_time_str = position.get('time', '')
            if not entry_time_str:
                return 1.0
            
            from datetime import datetime
            entry_time = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            holding_hours = (current_time - entry_time).total_seconds() / 3600
            
            # 找到对应的时间区间
            intervals = time_decay_cfg.get('intervals', [])
            for interval in intervals:
                if holding_hours <= interval['hours']:
                    return interval['multiplier']
            
            return 1.0
        except Exception as e:
            logger.warning(f"时间衰减计算失败: {e}")
            return 1.0

    def get_trailing_stop_status(self, symbol, current_price):
        """获取追踪止盈状态信息（用于调试和监控）"""
        if symbol not in self.state['positions']:
            return None

        pos = self.state['positions'][symbol]
        
        # 获取配置 - 避免直接导入config以防dotenv问题
        try:
            import config
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        except ImportError:
            # 如果导入失败，使用运行时配置或默认配置
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = {
                'trailing_stops': [
                    {'profit_threshold': 0.02, 'trailing_pct': 0.015},
                    {'profit_threshold': 0.05, 'trailing_pct': 0.02},
                    {'profit_threshold': 0.10, 'trailing_pct': 0.025},
                ],
                'time_decay': {
                    'enabled': True,
                    'intervals': [
                        {'hours': 1, 'multiplier': 1.0},
                        {'hours': 4, 'multiplier': 0.8},
                        {'hours': 12, 'multiplier': 0.6},
                        {'hours': 24, 'multiplier': 0.5},
                        {'hours': float('inf'), 'multiplier': 0.4}
                    ]
                }
            }
        
        trailing_stops = runtime_cfg.get('trailing_stops', spec_cfg.get('trailing_stops', []))
        time_decay_cfg = runtime_cfg.get('time_decay', spec_cfg.get('time_decay', {}))
        
        # 计算当前状态
        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        
        # 找到当前活跃的触发回撤比例
        active_trigger_drawdown = None
        sorted_stops = sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True)
        
        for stop_config in sorted_stops:
            if highest_profit_reached >= stop_config['profit_threshold']:
                # 新的分离式逻辑：使用独立的trigger_drawdown
                active_trigger_drawdown = stop_config.get('trigger_drawdown', stop_config.get('trailing_pct', 0.02))
                break
        
        # 应用时间衰减
        if active_trigger_drawdown and time_decay_cfg.get('enabled', False):
            time_multiplier = self._calculate_time_multiplier(pos, time_decay_cfg)
            adjusted_trigger_drawdown = active_trigger_drawdown * time_multiplier
        else:
            adjusted_trigger_drawdown = active_trigger_drawdown
        
        return {
            'symbol': symbol,
            'entry_price': pos['entry_price'],
            'current_price': current_price,
            'highest_price': pos['highest_price'],
            'highest_profit_pct': highest_profit_reached,
            'current_drawdown_pct': drawdown,
            'active_trigger_drawdown': active_trigger_drawdown,
            'adjusted_trigger_drawdown': adjusted_trigger_drawdown,
            'time_multiplier': time_multiplier if time_decay_cfg.get('enabled', False) else None,
            'holding_time_hours': self._get_holding_hours(pos)
        }
    
    def _get_holding_hours(self, position):
        """获取持仓小时数"""
        try:
            entry_time_str = position.get('time', '')
            if not entry_time_str:
                return 0
            
            from datetime import datetime
            entry_time = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            return (current_time - entry_time).total_seconds() / 3600
        except:
            return 0

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
            
            # 3. 移除持仓并保存
            # 注：total_pnl 和 trade_count 已在 _execute_order 的模拟卖出一并更新，避免重复累加
            del self.state['positions'][symbol]
            self.save_state()
            
            return pnl_pct  # 返回收益率供通知使用
