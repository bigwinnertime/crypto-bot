"""
市场情绪集成（长期规划-2）— Crypto Fear & Greed Index。

数据源：https://api.alternative.me/fng/ （免费，每日更新）
情绪分数 0-100：0=极度恐惧，100=极度贪婪

应用场景：
  - 极度恐惧 (<20)：禁止趋势跟踪入场，均值回归仓位减半
  - 极度贪婪 (>80)：趋势仓位收紧止盈，禁止均值回归入场
  - 正常区间 (20-80)：不影响策略
"""
import logging
import time
import requests

logger = logging.getLogger(__name__)

# Fear & Greed Index API
FNG_API_URL = "https://api.alternative.me/fng/"

# 情绪阈值
EXTREME_FEAR_THRESHOLD = 20
EXTREME_GREED_THRESHOLD = 80

# 缓存（每日更新一次即可）
_cached_sentiment = None
_cached_timestamp = 0
_CACHE_TTL = 21600  # 6小时缓存（API数据每日更新，6小时足够及时）


def fetch_fear_greed_index():
    """
    拉取 Crypto Fear & Greed Index。
    返回: (score: int, classification: str) 或 (None, None)
    """
    global _cached_sentiment, _cached_timestamp

    # 缓存检查
    now = time.time()
    if _cached_sentiment is not None and (now - _cached_timestamp) < _CACHE_TTL:
        return _cached_sentiment

    try:
        resp = requests.get(FNG_API_URL, params={"limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and data.get("data"):
            entry = data["data"][0]
            score = int(entry["value"])
            classification = entry.get("value_classification", "")
            _cached_sentiment = (score, classification)
            _cached_timestamp = now
            logger.info(f"📊 恐惧贪婪指数: {score} ({classification})")
            return score, classification
    except Exception as e:
        logger.warning(f"⚠️ 获取恐惧贪婪指数失败: {e}")

    return None, None


def get_sentiment_scale():
    """
    根据当前情绪返回策略仓位缩放系数。

    返回 dict:
      {
        'trend_scale': float,      # 趋势信号仓位缩放
        'meanrev_scale': float,    # 均值回归仓位缩放
        'tighten_profit': bool,   # 是否收紧止盈
        'block_trend': bool,       # 是否禁止趋势入场
        'block_meanrev': bool,    # 是否禁止均值回归入场
        'score': int,              # 情绪分数
        'classification': str,    # 情绪分类
      }
    """
    score, classification = fetch_fear_greed_index()

    result = {
        'trend_scale': 1.0,
        'meanrev_scale': 1.0,
        'tighten_profit': False,
        'block_trend': False,
        'block_meanrev': False,
        'score': score,
        'classification': classification,
    }

    if score is None:
        return result  # API 失败时不影响策略

    if score < EXTREME_FEAR_THRESHOLD:
        # 极度恐惧：禁止趋势跟踪（可能继续下跌），均值回归减半（接飞刀风险）
        result['block_trend'] = True
        result['meanrev_scale'] = 0.5
        logger.info(f"😰 极度恐惧 ({score})：禁止趋势入场，均值回归仓位减半")
    elif score > EXTREME_GREED_THRESHOLD:
        # 极度贪婪：禁止均值回归（可能不回归），趋势收紧止盈
        result['block_meanrev'] = True
        result['tighten_profit'] = True
        result['trend_scale'] = 0.8  # 趋势也减仓（可能见顶）
        logger.info(f"🤑 极度贪婪 ({score})：禁止均值回归入场，趋势仓位收紧至80%")
    # 正常区间(20-80)不调整

    return result
