import os
import sys

# 强制设置 Python 环境为 UTF-8
os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 如果是 Python 3.7+，开启开发模式的 UTF-8 强制支持
if hasattr(sys, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import pandas as pd
import time
import logging
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator
from ta.volatility import BollingerBands

import threading
from remote_control import start_remote_listener

import config
from risk_manager import RiskManager
from telegram_notifier import send_notification

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        # 显式指定编码并增加错误处理模式
        logging.FileHandler("bot_main.log", encoding='utf-8', errors='replace'), 
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TradingBot.Main")

class AdvancedTradingBot:
    def __init__(self):
        # 启动远程控制线程
        self.cmd_thread = threading.Thread(target=start_remote_listener, daemon=True)
        self.cmd_thread.start()
        #load_dotenv()
        # 1. 提取私钥并处理换行符
        raw_key = os.getenv('BINANCE_SECRET_KEY', '')

        # 关键点：将字符串中的 "\n" 替换为真正的换行字符，并去掉可能误加的引号
        formatted_key = raw_key.replace('\\n', '\n').strip('"').strip("'")

        # 2. 检查头尾标志是否完整 (MalformedFraming 经常是因为少了横线)
        if not formatted_key.startswith('-----BEGIN PRIVATE KEY-----'):
            formatted_key = f"-----BEGIN PRIVATE KEY-----\n{formatted_key}"
        if not formatted_key.endswith('-----END PRIVATE KEY-----'):
            formatted_key = f"{formatted_key}\n-----END PRIVATE KEY-----"

        self.exchange = ccxt.binance({
            'apiKey': config.API_KEY,
            'secret': formatted_key,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot', 'secretType': 'ed25519'}
        })
        self.risk = RiskManager(
            max_exposure=config.MAX_TOTAL_EXPOSURE, 
            fuse_limit=config.DRAWDOWN_FUSE
        )
        # self.notifier = Notifier() # 实例化通知模块

    def fetch_data(self, symbol):
        try:
            # 显式清理 symbol 字符串，防止多余空格
            clean_symbol = symbol.strip()
            bars = self.exchange.fetch_ohlcv(clean_symbol, timeframe=config.TIMEFRAME, limit=100)
            df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])

            # --- 压力测试注入：模拟 10% 暴跌 ---
            #if clean_symbol == 'BTC/USDT':
                #df.loc[df.index[-1], 'close'] = df.iloc[-2]['close'] * 0.90
                #logger.warning(f"⚠️ [测试中] 已为 {clean_symbol} 注入模拟暴跌信号...")

            return df
        except Exception as e:
            # 这里打印出具体的错误内容，帮助我们判断是否是 API 报错导致的
            logger.error(f"获取 {symbol} 数据时发生异常: {str(e)}")
            return pd.DataFrame()

    def get_strategy_signal(self, df, symbol):
        # 1. 根据币种名字，从 config.py 的字典中抓取属于它的参数
        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

        # 2. 计算指标
        adx = ADXIndicator(df['high'], df['low'], df['close']).adx().iloc[-1]
        rsi = RSIIndicator(df['close']).rsi().iloc[-1]
        sma_short = SMAIndicator(df['close'], window=20).sma_indicator().iloc[-1]
        sma_long = SMAIndicator(df['close'], window=60).sma_indicator().iloc[-1]
        price = df['close'].iloc[-1]
        
        # 3. 计算ATR和波动率（用于动态参数调整）
        from ta.volatility import AverageTrueRange
        atr = AverageTrueRange(df['high'], df['low'], df['close'], 
                               window=spec.get('atr_period', 14)).average_true_range().iloc[-1]
        atr_pct = atr / price  # ATR百分比（波动率指标）
        
        # 4. 成交量分析
        current_volume = df['volume'].iloc[-1]
        volume_ma = df['volume'].rolling(window=spec.get('volume_ma_period', 20)).mean().iloc[-1]
        volume_ratio = current_volume / volume_ma if volume_ma > 0 else 0
        
        # 5. 动态参数调整（根据波动率）
        adjusted_params = self._adjust_params_by_volatility(spec, atr_pct)
        
        # 辅助日志：每轮巡检的基础状态（可选，如果嫌日志太多可以注释掉）
        logger.debug(f"📊 {symbol} 状态扫描: Price:{price:.2f}, ADX:{adx:.1f}, RSI:{rsi:.1f}, ATR%:{atr_pct:.2%}, VolRatio:{volume_ratio:.2f}")
        
        # --- 逻辑 A：强趋势模式 (ADX > 阈值) ---
        if adx > adjusted_params['adx_threshold']:
            mode_str = "【趋势模式】"
            
            # 买入信号：需要成交量确认
            if price > sma_short and sma_short > sma_long:
                # 成交量确认：当前成交量需大于均值的threshold倍
                volume_confirmed = volume_ratio >= spec.get('volume_threshold', 1.5)
                
                if volume_confirmed:
                    logger.debug(f"📈 {symbol} {mode_str} 触发买入 | 原因: 价格 > SMA20({sma_short:.2f}) 且 均线多头排列 | 成交量确认: {volume_ratio:.2f}x")
                    return "BUY", "TREND_STRENGTH"
                else:
                    logger.debug(f"⚠️ {symbol} {mode_str} 买入信号但成交量不足 | VolRatio:{volume_ratio:.2f} < 阈值{spec.get('volume_threshold', 1.5)}")
                    return "HOLD", "LOW_VOLUME"

            if price < sma_long:
                logger.debug(f"📉 {symbol} {mode_str} 触发卖出 | 原因: 价格跌破 SMA60({sma_long:.2f}) 趋势终结")
                return "SELL", "TREND_EXIT"

        # --- 逻辑 B：弱势/震荡模式 (ADX <= 阈值) ---
        else:
            mode_str = "【震荡模式】"
            
            # 震荡买入：需要成交量确认
            if rsi < adjusted_params['rsi_oversold']:
                volume_confirmed = volume_ratio >= spec.get('volume_threshold', 1.5) * 0.8  # 震荡模式成交量要求稍低
                
                if volume_confirmed:
                    logger.debug(f"底部反弹 {symbol} {mode_str} 触发买入 | 原因: RSI({rsi:.1f}) < 阈值({adjusted_params['rsi_oversold']}) | 成交量确认: {volume_ratio:.2f}x")
                    return "BUY", "MEAN_REVERSION"
                else:
                    logger.debug(f"⚠️ {symbol} {mode_str} 买入信号但成交量不足 | VolRatio:{volume_ratio:.2f}")
                    return "HOLD", "LOW_VOLUME"

            if rsi > adjusted_params['rsi_overbought']:
                logger.debug(f"顶部回落 {symbol} {mode_str} 触发卖出 | 原因: RSI({rsi:.1f}) > 阈值({adjusted_params['rsi_overbought']})")
                return "SELL", "RANGE_EXIT"

        return "HOLD", "NONE"
    
    def _adjust_params_by_volatility(self, spec, atr_pct):
        """根据波动率动态调整参数"""
        vol_adjust = spec.get('volatility_adjust', {})
        
        if not vol_adjust.get('enabled', False):
            return spec
        
        # 获取阈值和倍数
        low_vol_threshold = vol_adjust.get('low_vol_threshold', 0.02)
        high_vol_threshold = vol_adjust.get('high_vol_threshold', 0.05)
        low_vol_multiplier = vol_adjust.get('low_vol_multiplier', 0.8)
        high_vol_multiplier = vol_adjust.get('high_vol_multiplier', 1.2)
        
        # 根据波动率调整参数
        adjusted = spec.copy()
        
        if atr_pct < low_vol_threshold:
            # 低波动：缩小参数（更严格的进场条件）
            multiplier = low_vol_multiplier
            logger.debug(f"📉 低波动市场 (ATR%:{atr_pct:.2%})，参数调整系数: {multiplier}")
        elif atr_pct > high_vol_threshold:
            # 高波动：放大参数（更宽松的止损/止盈）
            multiplier = high_vol_multiplier
            logger.debug(f"📈 高波动市场 (ATR%:{atr_pct:.2%})，参数调整系数: {multiplier}")
        else:
            # 正常波动：不调整
            return spec
        
        # 调整关键参数
        if 'adx_threshold' in adjusted:
            adjusted['adx_threshold'] = spec['adx_threshold'] * multiplier
        if 'rsi_oversold' in adjusted:
            adjusted['rsi_oversold'] = spec['rsi_oversold'] * multiplier
        if 'rsi_overbought' in adjusted:
            adjusted['rsi_overbought'] = spec['rsi_overbought'] / multiplier
        
        return adjusted

    def _execute_order(self, symbol, side, amount, price, mode):
        """
        统一订单拦截器：增加 0.1% 虚拟手续费计算
        """
        FEE_RATE = 0.001  # 0.1% 手续费

        if not config.LIVE_TRADE:
            # 模拟模式：处理虚拟记账
            acc = self.risk.state['virtual_account']
            
            if side == 'buy':
                raw_cost = amount * price
                fee = raw_cost * FEE_RATE
                total_cost = raw_cost + fee  # 买入时：实际支出 = 成交额 + 手续费
                
                if acc['balance'] < total_cost:
                    logger.error(f"❌ 模拟购买失败：虚拟余额不足！(含手续费需: {total_cost:.2f})")
                    return False
                
                acc['balance'] -= total_cost
                acc['total_fees'] += fee  # --- 累加买入手续费 ---
                logger.info(f"🧪 [模拟买入] 成交:{raw_cost:.2f} | 手续费:{fee:.2f} | 剩余余额:{acc['balance']:.2f}")
                
            elif side == 'sell':
                raw_revenue = amount * price
                fee = raw_revenue * FEE_RATE
                net_revenue = raw_revenue - fee  # 卖出时：实际到手 = 成交额 - 手续费
                
                acc['balance'] += net_revenue
                acc['total_fees'] += fee  # --- 累加卖出手续费 ---
                
                # 计算盈亏（基于净到手金额）
                pos = self.risk.state['positions'].get(symbol)
                if pos:
                    # 单笔盈亏 = 现在的净收入 - 当初的净支出
                    trade_pnl = net_revenue - pos.get('cost', 0)
                    acc['total_pnl'] += trade_pnl
                    acc['trade_count'] += 1
                    logger.info(f"🧪 [模拟卖出] 净收入:{net_revenue:.2f} (已扣手续费:{fee:.2f}) | 单笔净盈亏:{trade_pnl:.2f}")

            self.risk.save_state()
            return True

    def run(self):
        logger.info(f"🚀 系统已启动 (当前模式: {'实盘' if config.LIVE_TRADE else '测试/模拟'})")
        while True:
            try:
                # 获取账户余额 (实盘获取真实余额，模拟模式使用虚拟账户余额)
                if config.LIVE_TRADE:
                    balance_info = self.exchange.fetch_balance()
                    total_usdt = balance_info['total'].get('USDT', 0)
                else:
                    # 模拟模式使用虚拟账户的当前余额
                    total_usdt = self.risk.state['virtual_account']['balance']

                for symbol in config.SYMBOLS:
                    df = self.fetch_data(symbol)
                    if df.empty: continue
                    
                    # 风险熔断检查
                    if self.risk.check_circuit_breaker(symbol, df):
                        if self.risk.state['is_fused']:
                            # self.notifier.send_email(f"🚨 紧急熔断: {symbol}", "检测到异常跌幅，系统已自动锁定。")
                            send_notification(f"🚨  紧急熔断: {symbol}", "检测到异常跌幅，系统已自动锁定。")
                        continue
                    
                    price = df['close'].iloc[-1]
                    pos = self.risk.state['positions'].get(symbol)

                    # --- 第一步：风控检查（追踪止盈/硬止损/ATR止损） ---
                    stop_reason = self.risk.update_trailing_stop(symbol, price, df)
                    if stop_reason:
                        # 使用拦截器执行模拟/实盘卖出
                        if self._execute_order(symbol, 'sell', pos.get('amount', 0), price, stop_reason):
                            logger.warning(f"🚨 {symbol} 触发 {stop_reason}")
                            pnl = (price / pos['entry_price'] - 1) * 100
                            # self.notifier.send_email(f"🆘 离场通知: {symbol}", f"原因: {stop_reason}\n收益率: {pnl:.2f}%")
                            send_notification(f"🆘  离场通知: {symbol}", f"*原因*: {stop_reason}\n*收益率*: {pnl:.2f}%")
                            del self.risk.state['positions'][symbol]
                            self.risk.save_state()
                        continue

                    # --- 第二步：策略信号判定 ---
                    signal, mode = self.get_strategy_signal(df, symbol)

                    # --- 第三步：执行逻辑 ---
                    # 执行买入
                    if signal == "BUY" and self.risk.can_open_position(symbol, total_usdt):
                        spec_config = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)
                        trade_amount = spec_config.get('trade_amount', 20)
                        amount = trade_amount / price
                        
                        # 调用统一拦截器
                        if self._execute_order(symbol, 'buy', amount, price, mode):
                            self.risk.execute_buy_update(symbol, price, amount, trade_amount, mode)

                            # 发送通知
                            safe_mode = mode.replace("_", " ")
                            #self.notifier.send_email(f"✅ 买入成交: {symbol}", f"价格: {price}\n模式: {mode}")
                            send_notification(f"✅  买入成交: {symbol}", f"*价格*: {price}\n*模式*: {safe_mode}")

                    # 执行策略卖出（RSI超买等信号）
                    elif signal == "SELL" and pos:
                        if self._execute_order(symbol, 'sell', pos['amount'], price, mode):
                            pnl_pct = self.risk.execute_sell_update(symbol, price, mode)

                            # 发送通知
                            #self.notifier.send_email(f"🔻 卖出成交: {symbol}", f"收益率: {pnl:.2f}%")
                            send_notification(f"🔻 卖出成交: {symbol}", f"*收益率*: {pnl:.2f}%")

                time.sleep(60) 
            except Exception as e:
                logger.error(f"运行异常: {e}")
                time.sleep(10)

if __name__ == "__main__":
    AdvancedTradingBot().run()
