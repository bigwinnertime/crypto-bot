import json
import copy
import os
import time
import logging
import threading

logger = logging.getLogger("TradingBot.Risk")

class RiskManager:
    # 创建一个类级别的可重入锁（单例模式），确保所有实例共享同一把锁
    _file_lock = threading.RLock()

    def __init__(self, state_file="bot_state.json", max_exposure=0.7, fuse_limit=0.08):
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
        defaults = {
            "positions": {},
            "is_fused": False,
            "fuse_time": 0,
            "fused_symbols": {},
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
                if not os.path.exists(self.state_file):
                    return copy.deepcopy(defaults)

                with open(self.state_file, 'r') as f:
                    state = json.load(f)

                for key, value in defaults.items():
                    if key not in state:
                        state[key] = copy.deepcopy(value)
                    elif isinstance(value, dict):
                        for sub_key, sub_value in value.items():
                            if sub_key not in state[key]:
                                state[key][sub_key] = sub_value

                return state

            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"⚠️ 读取状态文件异常，已加载默认设置: {e}")
                return copy.deepcopy(defaults)

    def save_state(self):
        """持久化保存当前持仓和熔断状态"""
        with self._file_lock:
            try:
                with open(self.state_file, 'w', encoding='utf-8') as f:
                    json.dump(self.state, f, indent=4, ensure_ascii=False)
            except Exception as e:
                logger.error(f"保存状态文件失败: {e}")

    # ═══════════════════════════════════════════════════
    #  熔断机制（按币种独立）
    # ═══════════════════════════════════════════════════

    def check_circuit_breaker(self, symbol, df):
        """熔断机制：按币种独立触发，防止在暴跌中持续接飞刀"""
        if len(df) < 2:
            return False

        import config

        # 全局熔断（Telegram /fuse 命令）
        if self.state.get('is_fused', False):
            fuse_duration = getattr(config, 'FUSE_DURATION', 7200)
            elapsed = time.time() - self.state.get('fuse_time', 0)
            if elapsed > fuse_duration:
                logger.info("🛡️ 全局熔断冷却期结束，系统恢复监控。")
                self.state['is_fused'] = False
                self.state['fuse_time'] = 0
                self.save_state()
            else:
                return True

        # 按币种独立熔断
        change = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]

        if change < -self.fuse_limit:
            logger.error(f"🚨 {symbol} 发生异常暴跌 ({change:.2%})！启动该币种熔断保护。")
            if 'fused_symbols' not in self.state:
                self.state['fused_symbols'] = {}
            self.state['fused_symbols'][symbol] = time.time()
            self.save_state()
            return True

        # 检查该币种的熔断是否过期
        fused_symbols = self.state.get('fused_symbols', {})
        if symbol in fused_symbols:
            fuse_duration = getattr(config, 'FUSE_DURATION', 7200)
            elapsed = time.time() - fused_symbols[symbol]
            if elapsed > fuse_duration:
                logger.info(f"🛡️ {symbol} 熔断冷却期结束，恢复监控。")
                del self.state['fused_symbols'][symbol]
                self.save_state()
            else:
                remaining = int((fuse_duration - elapsed) / 60)
                logger.info(f"⏸️ {symbol} 仍在熔断冷却中（剩余约 {remaining} 分钟）")
                return True

        return False

    def is_symbol_fused(self, symbol):
        """检查某个币种是否处于熔断状态（供 bot_engine 快速查询）"""
        # 全局熔断
        if self.state.get('is_fused', False):
            return True
        # 按币种熔断
        fused_symbols = self.state.get('fused_symbols', {})
        if symbol in fused_symbols:
            import config
            fuse_duration = getattr(config, 'FUSE_DURATION', 7200)
            elapsed = time.time() - fused_symbols[symbol]
            if elapsed > fuse_duration:
                return False  # 已过期
            return True
        return False

    def remote_set_fuse(self, status: bool):
        """供 Telegram 线程安全设置全局熔断"""
        with self._file_lock:
            self.state['is_fused'] = status
            self.state['fuse_time'] = time.time() if status else 0
            if status:
                # 全局熔断时，同时熔断所有已配置的币种
                import config
                if 'fused_symbols' not in self.state:
                    self.state['fused_symbols'] = {}
                for symbol in config.SYMBOLS:
                    if symbol not in self.state['fused_symbols']:
                        self.state['fused_symbols'][symbol] = time.time()
            self.save_state()

    # ═══════════════════════════════════════════════════
    #  仓位管理
    # ═══════════════════════════════════════════════════

    def can_open_position(self, symbol, total_balance):
        """下单前的最后一道防线"""
        import config

        # 1. 检查是否已有持仓
        if symbol in self.state['positions']:
            return False

        # 2. 检查总仓位暴露
        current_cost = sum(p['cost'] for p in self.state['positions'].values())
        if total_balance > 0 and (current_cost / total_balance) > self.max_exposure:
            logger.warning(f"⚠️ 仓位占用过高 ({(current_cost/total_balance):.2%})，拒绝买入 {symbol}")
            return False

        # 3. 账户级最大回撤保护
        if self._is_account_drawdown_limited(total_balance):
            logger.warning(f"⚠️ 账户回撤超限，拒绝买入 {symbol}")
            return False

        # 4. 相关性检查
        if not self._check_correlation(symbol):
            logger.warning(f"⚠️ 相关币种持仓已达上限，拒绝买入 {symbol}")
            return False

        return True

    def _is_account_drawdown_limited(self, current_balance):
        """检查账户是否处于最大回撤保护期"""
        import config

        # 更新净值高点
        equity_high = self.state.get('equity_high', current_balance)
        if current_balance > equity_high:
            self.state['equity_high'] = current_balance
            return False

        # 计算回撤
        drawdown = (equity_high - current_balance) / equity_high if equity_high > 0 else 0
        max_dd = getattr(config, 'MAX_DRAWDOWN_PCT', 0.15)

        if drawdown >= max_dd:
            # 检查冷却期
            dd_time = self.state.get('drawdown_trigger_time', 0)
            if dd_time == 0:
                self.state['drawdown_trigger_time'] = time.time()
                self.save_state()
                logger.error(f"🚨 账户回撤 {drawdown:.2%} 超过阈值 {max_dd:.2%}，暂停交易！")
                return True

            cooldown = getattr(config, 'DRAWDOWN_COOLDOWN', 14400)
            if time.time() - dd_time < cooldown:
                return True
            else:
                # 冷却期结束，重置
                self.state['drawdown_trigger_time'] = 0
                self.state['equity_high'] = current_balance
                self.save_state()
                logger.info("🛡️ 回撤冷却期结束，恢复交易。")
                return False

        return False

    def _check_correlation(self, symbol):
        """检查相关性分组的持仓限制"""
        import config

        groups = getattr(config, 'CORRELATION_GROUPS', {})
        max_corr = getattr(config, 'MAX_CORRELATED_POSITIONS', 1)

        for group_name, symbols in groups.items():
            if symbol in symbols:
                # 计算该组已持有的数量
                held_count = sum(1 for s in symbols if s in self.state['positions'])
                if held_count >= max_corr:
                    return False

        return True

    # ═══════════════════════════════════════════════════
    #  追踪止盈（仅负责分阶段追踪止盈，不含止损）
    # ═══════════════════════════════════════════════════

    def update_trailing_stop(self, symbol, current_price, df=None):
        """分阶段追踪止盈 + 时间衰减。止损逻辑由 bot_engine._should_sell 统一处理。"""
        if symbol not in self.state['positions']:
            return None

        pos = self.state['positions'][symbol]

        # --- 1. 参数获取 ---
        try:
            import config
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        except ImportError:
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = {
                'trailing_stops': [
                    {'profit_threshold': 0.02, 'trigger_drawdown': 0.015, 'trailing_pct': 0.015},
                    {'profit_threshold': 0.05, 'trigger_drawdown': 0.02, 'trailing_pct': 0.02},
                    {'profit_threshold': 0.10, 'trigger_drawdown': 0.025, 'trailing_pct': 0.025},
                ],
                'time_decay': {
                    'enabled': True,
                    'intervals': [
                        {'hours': 1, 'multiplier': 1.0},
                        {'hours': 4, 'multiplier': 0.9},
                        {'hours': 12, 'multiplier': 0.7},
                        {'hours': 24, 'multiplier': 0.5},
                        {'hours': float('inf'), 'multiplier': 0.3}
                    ]
                }
            }

        trailing_stops = runtime_cfg.get('trailing_stops', spec_cfg.get('trailing_stops', []))
        time_decay_cfg = runtime_cfg.get('time_decay', spec_cfg.get('time_decay', {}))

        # --- 2. 更新最高价 ---
        if 'highest_price' not in pos:
            pos['highest_price'] = pos['entry_price']

        if current_price > pos['highest_price']:
            pos['highest_price'] = current_price
            self.save_state()
            return None

        # --- 3. 计算基础比例 ---
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']
        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']

        # --- 4. 分阶段追踪止盈逻辑 ---
        active_trigger_drawdown = None

        sorted_stops = sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True)
        for stop_config in sorted_stops:
            if highest_profit_reached >= stop_config['profit_threshold']:
                active_trigger_drawdown = stop_config.get('trigger_drawdown', stop_config.get('trailing_pct', 0.02))
                break

        if active_trigger_drawdown is None:
            logger.debug(f"{symbol} 盈利 {highest_profit_reached:.2%}，未达到追踪止盈门槛")
            return None

        # 应用时间衰减（持仓越久，止损越紧）
        if time_decay_cfg.get('enabled', False):
            time_multiplier = self._calculate_time_multiplier(pos, time_decay_cfg)
            adjusted_trigger_drawdown = active_trigger_drawdown / time_multiplier

            logger.debug(f"{symbol} 追踪止盈: 原始{active_trigger_drawdown:.2%} -> 调整后{adjusted_trigger_drawdown:.2%} (时间系数{time_multiplier:.2f})")
            active_trigger_drawdown = adjusted_trigger_drawdown

        if drawdown >= active_trigger_drawdown:
            return f"追踪止盈 (回撤 {drawdown:.2%}, 设定阈值 {active_trigger_drawdown:.2%})"

        return None

    def _calculate_time_multiplier(self, position, time_decay_cfg):
        """计算时间衰减系数（持仓越久，返回值越小，止损越紧）"""
        try:
            entry_time_str = position.get('time', '')
            if not entry_time_str:
                return 1.0

            from datetime import datetime
            entry_time = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            holding_hours = (current_time - entry_time).total_seconds() / 3600

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

        try:
            import config
            runtime_cfg = self.state.get('runtime_config', {}).get(symbol, {})
            spec_cfg = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
        except ImportError:
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
                        {'hours': 4, 'multiplier': 0.9},
                        {'hours': 12, 'multiplier': 0.7},
                        {'hours': 24, 'multiplier': 0.5},
                        {'hours': float('inf'), 'multiplier': 0.3}
                    ]
                }
            }

        trailing_stops = runtime_cfg.get('trailing_stops', spec_cfg.get('trailing_stops', []))
        time_decay_cfg = runtime_cfg.get('time_decay', spec_cfg.get('time_decay', {}))

        highest_profit_reached = (pos['highest_price'] - pos['entry_price']) / pos['entry_price']
        drawdown = (pos['highest_price'] - current_price) / pos['highest_price']

        active_trigger_drawdown = None
        sorted_stops = sorted(trailing_stops, key=lambda x: x['profit_threshold'], reverse=True)
        for stop_config in sorted_stops:
            if highest_profit_reached >= stop_config['profit_threshold']:
                active_trigger_drawdown = stop_config.get('trigger_drawdown', stop_config.get('trailing_pct', 0.02))
                break

        time_multiplier = None
        adjusted_trigger_drawdown = active_trigger_drawdown
        if active_trigger_drawdown and time_decay_cfg.get('enabled', False):
            time_multiplier = self._calculate_time_multiplier(pos, time_decay_cfg)
            adjusted_trigger_drawdown = active_trigger_drawdown / time_multiplier

        return {
            'symbol': symbol,
            'entry_price': pos['entry_price'],
            'current_price': current_price,
            'highest_price': pos['highest_price'],
            'highest_profit_pct': highest_profit_reached,
            'current_drawdown_pct': drawdown,
            'active_trigger_drawdown': active_trigger_drawdown,
            'adjusted_trigger_drawdown': adjusted_trigger_drawdown,
            'time_multiplier': time_multiplier,
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

    # ═══════════════════════════════════════════════════
    #  运行时配置 & 交易更新
    # ═══════════════════════════════════════════════════

    def update_runtime_config(self, symbol, key, value):
        """安全地更新运行时配置"""
        with self._file_lock:
            if 'runtime_config' not in self.state:
                self.state['runtime_config'] = {}
            if symbol not in self.state['runtime_config']:
                self.state['runtime_config'][symbol] = {}

            self.state['runtime_config'][symbol][key] = value
            self.save_state()
            logger.info(f"⚙️ 远程配置更新: {symbol} {key} = {value}")

    def execute_buy_update(self, symbol, price, amount, cost, mode):
        """统一封装：买入后的状态更新逻辑"""
        with self._file_lock:
            self.state['positions'][symbol] = {
                "entry_price": price,
                "amount": amount,
                "cost": cost,
                "highest_price": price,
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_state()

    def execute_sell_update(self, symbol, price, mode):
        """统一封装：卖出后的状态更新逻辑（计算PnL、存入历史、清空持仓）"""
        with self._file_lock:
            if symbol not in self.state['positions']:
                return None

            pos = self.state['positions'][symbol]
            pnl_val = (price - pos['entry_price']) * pos['amount']
            pnl_pct = (price / pos['entry_price'] - 1) * 100

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

            if 'trade_history' not in self.state:
                self.state['trade_history'] = []
            self.state['trade_history'].append(trade_record)

            del self.state['positions'][symbol]
            self.save_state()

            return pnl_pct

    def execute_virtual_trade(self, symbol, side, amount, price, fee_rate=0.001):
        """统一封装：线程安全的虚拟账户余额与盈亏计算"""
        with self._file_lock:
            acc = self.state['virtual_account']
            if side == 'buy':
                raw_cost = amount * price
                fee = raw_cost * fee_rate
                total_cost = raw_cost + fee
                
                if acc['balance'] < total_cost:
                    return False, total_cost, fee, 0
                
                acc['balance'] -= total_cost
                acc['total_fees'] += fee
                self.save_state()
                return True, raw_cost, fee, 0
                
            elif side == 'sell':
                raw_revenue = amount * price
                fee = raw_revenue * fee_rate
                net_revenue = raw_revenue - fee
                
                acc['balance'] += net_revenue
                acc['total_fees'] += fee
                
                pos = self.state['positions'].get(symbol)
                trade_pnl = 0
                if pos:
                    trade_pnl = net_revenue - pos.get('cost', 0)
                    acc['total_pnl'] += trade_pnl
                    acc['trade_count'] += 1
                    
                self.save_state()
                return True, net_revenue, fee, trade_pnl
            return False, 0, 0, 0

