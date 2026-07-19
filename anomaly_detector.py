"""
异常检测与预警（长期规划-3）— 基于价格行为检测异常。

检测维度：
  1. 波动率异常：ATR 突然放大到历史均值的 N 倍
  2. 成交量异常：成交量放大到均值的 N 倍但价格不动（量价背离）
  3. 跨币种相关性异常：BTC/ETH/SOL 同时异动（系统性风险）

异常时发送 Telegram 预警，严重异常时建议触发全局熔断。
"""
import logging
import time
from collections import defaultdict

import pandas as pd
from ta.volatility import AverageTrueRange

import config

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """异常检测器，在主循环中每轮调用。"""

    def __init__(self, notifier=None):
        self.notifier = notifier  # Telegram 通知函数
        self._price_cache = {}    # symbol -> {'prev_price': float, 'prev_time': float}
        self._atr_cache = {}      # symbol -> [atr_history]
        self._alert_cooldown = {} # alert_type -> last_alert_time（防刷屏）
        self._cooldown_seconds = 3600  # 同类预警冷却1小时

    def check_volatility_anomaly(self, symbol, df, spec):
        """
        波动率异常检测：当前ATR > 30日平均ATR的2.5倍。

        返回: (is_anomaly: bool, message: str)
        """
        if len(df) < 45:
            return False, ""

        atr_period = spec.get('atr_period', 14)
        atr_series = AverageTrueRange(df['high'], df['low'], df['close'], window=atr_period).average_true_range()

        current_atr = atr_series.iloc[-1]
        avg_atr_30d = atr_series.iloc[-30:].mean()

        if pd.isna(current_atr) or pd.isna(avg_atr_30d) or avg_atr_30d <= 0:
            return False, ""

        ratio = current_atr / avg_atr_30d
        threshold = spec.get('volatility_anomaly_ratio', 2.5)

        if ratio >= threshold:
            msg = f"⚠️ {symbol} 波动率异常飙升：当前ATR是30日均值的 {ratio:.1f}倍"
            return True, msg

        return False, ""

    def check_volume_price_divergence(self, symbol, df, spec):
        """
        量价背离检测：成交量放大到均值的3倍但价格变动<1%。

        返回: (is_anomaly: bool, message: str)
        """
        if len(df) < 25:
            return False, ""

        vol_ma_period = spec.get('volume_ma_period', 20)
        vol_ma = df['volume'].rolling(vol_ma_period).mean().iloc[-1]
        current_vol = df['volume'].iloc[-1]

        if pd.isna(vol_ma) or vol_ma <= 0:
            return False, ""

        vol_ratio = current_vol / vol_ma
        price_change = abs(df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]

        threshold = spec.get('volume_anomaly_ratio', 3.0)
        price_threshold = spec.get('divergence_price_threshold', 0.01)

        if vol_ratio >= threshold and price_change < price_threshold:
            msg = f"⚠️ {symbol} 量价背离异常：量比{vol_ratio:.1f}倍 但价格仅变动{price_change:.2%}"
            return True, msg

        return False, ""

    def check_cross_symbol_anomaly(self, symbol_data):
        """
        跨币种相关性异常检测：所有币种同时大跌（系统性风险）。

        参数: symbol_data = {'BTC/USDT': price_change, 'ETH/USDT': price_change, ...}
        返回: (is_anomaly: bool, message: str, should_fuse: bool)
        """
        if len(symbol_data) < 2:
            return False, "", False

        # 所有币种同时跌幅 > 8%（4h K线级别，5%太常见）
        all_drop = all(change < -0.08 for change in symbol_data.values())
        # 至少2个币种跌幅 > 5%（4h K线单根3%很常见，提高到5%）
        significant_drops = sum(1 for change in symbol_data.values() if change < -0.05)

        if all_drop or significant_drops >= 2:
            changes_str = ", ".join(f"{s}: {c:.2%}" for s, c in symbol_data.items())
            msg = f"🚨 系统性风险预警：多币种同时异动 ({changes_str})"
            # 所有币种跌>5% 触发全局熔断建议
            should_fuse = all_drop
            return True, msg, should_fuse

        return False, "", False

    def run_all_checks(self, symbol, df, spec, symbol_changes=None):
        """
        运行所有异常检测，返回预警列表和熔断建议。

        参数:
          symbol: 币种
          df: K线数据
          spec: 币种配置
          symbol_changes: {symbol: price_change} 用于跨币种检测（可选）

        返回: {
          'alerts': [str],        # 预警消息列表
          'should_fuse': bool,    # 是否建议全局熔断
        }
        """
        alerts = []
        should_fuse = False

        # 1. 波动率异常
        is_vol, vol_msg = self.check_volatility_anomaly(symbol, df, spec)
        if is_vol and self._can_alert('volatility', symbol):
            alerts.append(vol_msg)

        # 2. 量价背离
        is_div, div_msg = self.check_volume_price_divergence(symbol, df, spec)
        if is_div and self._can_alert('divergence', symbol):
            alerts.append(div_msg)

        # 3. 跨币种异常
        if symbol_changes:
            is_cross, cross_msg, cross_fuse = self.check_cross_symbol_anomaly(symbol_changes)
            if is_cross and self._can_alert('cross_symbol', 'global'):
                alerts.append(cross_msg)
                should_fuse = cross_fuse

        return {'alerts': alerts, 'should_fuse': should_fuse}

    def _can_alert(self, alert_type, symbol):
        """防刷屏：同类预警1小时内只发一次。"""
        key = f"{alert_type}_{symbol}"
        now = time.time()
        last = self._alert_cooldown.get(key, 0)
        if now - last < self._cooldown_seconds:
            return False
        self._alert_cooldown[key] = now
        return True

    def send_alerts(self, alerts):
        """发送预警到 Telegram。"""
        if not alerts:
            return
        for msg in alerts:
            logger.warning(msg)
            if self.notifier:
                try:
                    self.notifier("🚨 异常预警", msg)
                except Exception as e:
                    logger.error(f"发送预警失败: {e}")
