import os
import sys

# 强制设置 Python 环境为 UTF-8
os.environ['LANG'] = 'en_US.UTF-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 如果是 Python 3.7+，开启开发模式的 UTF-8 强制支持
if hasattr(sys, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 立即加载环境变量
from dotenv import load_dotenv
load_dotenv()

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
        # 主交易日志
        logging.FileHandler("bot_main.log", encoding='utf-8', errors='replace'),
        # 运行状态日志
        logging.FileHandler("bot_run.log", encoding='utf-8', errors='replace'),
        # 控制台输出
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
            df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])

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
        """
        优化版策略信号生成器 v2
        改进点：
          1. ADX 阈值降低（×0.8 系数），释放趋势模式信号
          2. 引入 MACD 金叉 + 量能确认，替代严苛的 SMA 多头排列
          3. 引入 RSI 均线交叉（RSI 上穿 50），不依赖极端值
          4. 引入布林带下轨支撑 + RSI 底背离双重验证
          5. 卖出改用 ATR 移动止损，摆脱 SMA60 假突破
        """
        if len(df) < 60:
            logger.warning(f"{symbol} 数据不足（{len(df)}根），跳过信号判定")
            return "HOLD", "INSUFFICIENT_DATA"

        spec = config.STRATEGY_CONFIG.get(symbol, config.DEFAULT_CONFIG)

        # ── 1. 指标预计算 ──────────────────────────────
        close     = df['close']
        high      = df['high']
        low       = df['low']
        volume    = df['volume']
        price     = close.iloc[-1]

        adx_val   = ADXIndicator(high, low, close).adx().iloc[-1]
        rsi_val   = RSIIndicator(close).rsi().iloc[-1]
        rsi_prev  = RSIIndicator(close).rsi().iloc[-2]

        sma20     = SMAIndicator(close, window=20).sma_indicator().iloc[-1]
        sma60     = SMAIndicator(close, window=60).sma_indicator().iloc[-1]

        bb        = BollingerBands(close,
                                   window=spec.get('bb_period', 20),
                                   window_dev=spec.get('bb_std', 2))
        bb_upper  = bb.bollinger_hband().iloc[-1]
        bb_mid    = bb.bollinger_mavg().iloc[-1]
        bb_lower  = bb.bollinger_lband().iloc[-1]

        from ta.volatility import AverageTrueRange
        atr       = AverageTrueRange(high, low, close,
                                    window=spec.get('atr_period', 14)
                                    ).average_true_range().iloc[-1]
        atr_pct   = atr / price

        from ta.trend import MACD
        macd_ind  = MACD(close)
        macd_line = macd_ind.macd().iloc[-1]
        macd_sig  = macd_ind.macd_signal().iloc[-1]
        macd_prev = macd_ind.macd().iloc[-2]

        vol_ma    = volume.rolling(spec.get('volume_ma_period', 20)).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 0
        rsi_2_ago = RSIIndicator(close).rsi().iloc[-3]

        # ── 2. 波动率自适应参数 ────────────────────────
        adjusted  = self._adjust_params_by_volatility(spec, atr_pct)

        # ── 3. 卖出信号（持仓时优先判断）───────────────
        sell_reason = self._should_sell(
            symbol, price, adx_val, rsi_val, macd_line, macd_sig,
            bb_lower, atr, spec, adjusted
        )
        if sell_reason:
            return "SELL", sell_reason

        # ── 4. 买入信号（四套逻辑 OR 叠加）────────────
        buy_reason = self._should_buy(
            price, adx_val, rsi_val, rsi_prev, sma20, sma60,
            macd_line, macd_sig, macd_prev, bb_lower, bb_mid,
            vol_ratio, spec, adjusted, rsi_2_ago
        )
        if buy_reason:
            return "BUY", buy_reason

        # ── 5. 默认持币/观望 ──────────────────────────
        regime = "【趋势】" if adx_val > adjusted['adx_threshold'] else "【震荡】"
        logger.debug(f"{symbol} {regime} ADX={adx_val:.1f} RSI={rsi_val:.1f} → 持币观望")
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

    def _should_sell(self, symbol, price, adx, rsi, macd, macd_sig,
                     bb_lower, atr, spec, adjusted):
        """
        卖出判断（ATR 移动止损为核心，替代 SMA60 硬性跌破）：
          - 追踪止损：highest - N×ATR
          - 固定止损：entry × (1 - stop_loss_pct)
          - RSI 超买预警（盈利 > 5% 时）
          - MACD 死叉 + ADX 趋弱
        """
        pos = self.risk.state['positions'].get(symbol)
        if not pos:
            return None

        entry_price   = pos['entry_price']
        highest_price = pos.get('highest_price', entry_price)
        atr_multi     = spec.get('atr_multiplier', 2.0)
        profit_pct    = (price - entry_price) / entry_price

        trail_stop = highest_price - atr_multi * atr
        hard_stop  = entry_price * (1 - spec.get('stop_loss_pct', 0.03))

        if price <= trail_stop:
            return f"ATR追踪止损 (价{price:.2f}≤线{trail_stop:.2f})"
        if price <= hard_stop:
            return f"固定止损 (价{price:.2f}≤线{hard_stop:.2f})"
        if profit_pct > 0.05 and rsi > adjusted['rsi_overbought']:
            return f"RSI超买预警 (RSI={rsi:.1f}>阈值{adjusted['rsi_overbought']:.0f})"

        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        if macd_dead and adx < adjusted['adx_threshold'] * 0.75:
            return "MACD死叉+ADX趋弱"

        return None

    def _should_buy(self, price, adx, rsi, rsi_prev, sma20, sma60,
                    macd, macd_sig, macd_prev,
                    bb_lower, bb_mid, vol_ratio, spec, adjusted,
                    rsi_2_ago=None):
        """
        四套买入逻辑（任意一套命中 + 量能确认 → 买入）：
          A. 趋势跟随：MACD 金叉 + ADX 确认 + 量能
          B. RSI 反弹：RSI 上穿 50 + 价格站稳布林中轨 + 量能
          C. 布林支撑：价格触下轨 + RSI 底背离 + 量能
          D. SMA20 突破：价格站上 SMA20 + RSI 健康 + 量能
        """
        vol_thr = spec.get('volume_threshold', 1.5)

        # ── A. 趋势跟随（MACD 金叉 + 量能放大）
        macd_golden = (macd_prev < macd_sig and macd > macd_sig)
        if adx > adjusted['adx_threshold'] * 0.8:
            if macd_golden and vol_ratio >= vol_thr:
                return "TREND_MACD_GOLDEN_CROSS"
            if (adx > adjusted['adx_threshold'] and
                    vol_ratio >= vol_thr * 1.2 and
                    price > sma20 and rsi > 50):
                return "TREND_ADX_VOL_CONFIRM"

        # ── B. RSI 均线交叉反弹（RSI 上穿 50）
        rsi_cross_50 = (rsi_prev < 50 <= rsi)
        if rsi_cross_50 and price > bb_mid and vol_ratio >= vol_thr * 0.8:
            return "RSI_50_CROSS_BOUNCE"

        # ── C. 布林下轨支撑 + RSI 底背离
        touch_lower    = price <= bb_lower * 1.005
        rsi_divergence = (rsi_2_ago is not None
                          and rsi > rsi_2_ago
                          and rsi > 30)
        if touch_lower and rsi_divergence and vol_ratio >= vol_thr * 0.6:
            return "BB_LOWER_RSI_DIVERGENCE"

        # ── D. SMA20 突破（放宽版，不过度依赖均线排列）
        macd_dead = (macd_prev > macd_sig and macd < macd_sig)
        if (price > sma20 and rsi > 55 and
                vol_ratio >= vol_thr and not macd_dead):
            return "SMA20_BREAKOUT"

        return None


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

        # 实盘模式：执行真实订单
        try:
            if side == 'buy':
                order = self.exchange.create_market_buy_order(symbol, amount)
                logger.info(f"✅ [实盘买入] {symbol} 订单已执行: {order['id']}")
                return True
            elif side == 'sell':
                order = self.exchange.create_market_sell_order(symbol, amount)
                logger.info(f"✅ [实盘卖出] {symbol} 订单已执行: {order['id']}")
                return True
        except Exception as e:
            logger.error(f"❌ [实盘{side}] 订单执行失败: {e}")
            return False

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
                            # 执行卖出后状态更新（记录交易历史、删除持仓），内部会保存状态
                            self.risk.execute_sell_update(symbol, price, stop_reason)
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
                        logger.info(f"📤 准备执行买入订单: {symbol}, 金额: {trade_amount}, 价格: {price:.2f}")
                        if self._execute_order(symbol, 'buy', amount, price, mode):
                            logger.info(f"✅ _execute_order 返回 True，开始更新持仓状态")
                            self.risk.execute_buy_update(symbol, price, amount, trade_amount, mode)
                            logger.info(f"✅ 持仓状态更新完成，准备发送通知")
                            
                            # 发送通知
                            safe_mode = mode.replace("_", " ")
                            logger.info(f"📤 正在调用 send_notification: 标题='✅  买入成交: {symbol}'")
                            result = send_notification(f"✅  买入成交: {symbol}", f"*价格*: {price}\n*模式*: {safe_mode}")
                            logger.info(f"📤 send_notification 调用完成，返回值: {result}")

                    # 执行策略卖出（RSI超买等信号）
                    elif signal == "SELL" and pos:
                        if self._execute_order(symbol, 'sell', pos['amount'], price, mode):
                            pnl_pct = self.risk.execute_sell_update(symbol, price, mode)

                            # 发送通知
                            #self.notifier.send_email(f"🔻 卖出成交: {symbol}", f"收益率: {pnl:.2f}%")
                            send_notification(f"🔻 卖出成交: {symbol}", f"*收益率*: {pnl_pct:.2f}%")

                time.sleep(60) 
            except Exception as e:
                logger.error(f"运行异常: {e}")
                time.sleep(10)

if __name__ == "__main__":
    AdvancedTradingBot().run()
