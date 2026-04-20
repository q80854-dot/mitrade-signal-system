"""
data_fetcher.py — 數據抓取模組
使用 yfinance 抓取 K 線與宏觀數據
所有數據在回傳前都會經過驗證
"""

import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, List

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logging.warning("yfinance not installed. Run: pip install yfinance")

from config import SYMBOLS, TIMEFRAMES

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# 快取系統（避免重複抓取）
_cache: Dict = {}

def _cache_key(symbol: str, interval: str) -> str:
    return f"{symbol}_{interval}"

def _cache_get(key: str, ttl_seconds: int = 300) -> Optional[dict]:
    """從快取取得數據，超過 TTL 則視為過期"""
    if key in _cache:
        age = (datetime.now(timezone.utc) - _cache[key]["ts"]).total_seconds()
        if age < ttl_seconds:
            return _cache[key]["data"]
    return None

def _cache_set(key: str, data: dict) -> dict:
    """儲存數據到快取"""
    _cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}
    return data

def _safe_get(url: str, params: dict = None, timeout: int = 10) -> Optional[requests.Response]:
    """安全的 HTTP GET，有錯誤處理"""
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if r.status_code == 200:
            return r
        logger.warning(f"HTTP {r.status_code} for {url}")
        return None
    except requests.Timeout:
        logger.error(f"Timeout fetching {url}")
        return None
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

# ═══════════════════════════════════════════
# K 線數據抓取
# ═══════════════════════════════════════════

def fetch_ohlcv(symbol: str, timeframe_key: str = "entry") -> Optional[dict]:
    """
    使用 yfinance 抓取指定品種的 K 線數據
    symbol: 品種代碼（例如 EURUSD）
    timeframe_key: trend / mid / entry
    """
    if not YFINANCE_AVAILABLE:
        logger.error("yfinance not available")
        return None

    if symbol not in SYMBOLS:
        logger.error(f"Unknown symbol: {symbol}")
        return None

    tf = TIMEFRAMES.get(timeframe_key)
    if not tf:
        logger.error(f"Unknown timeframe: {timeframe_key}")
        return None

    sym_info  = SYMBOLS[symbol]
    yahoo_sym = sym_info["yahoo"]
    cache_key = _cache_key(symbol, timeframe_key)

    ttl    = 300 if tf["interval"] in ["1h", "4h"] else 1800
    cached = _cache_get(cache_key, ttl)
    if cached:
        return cached

    try:
        ticker = yf.Ticker(yahoo_sym)
        df     = ticker.history(period=tf["range"], interval=tf["interval"])

        if df is None or len(df) < 30:
            logger.warning(f"Insufficient data for {symbol} {timeframe_key}: {len(df) if df is not None else 0}")
            return None

        # 只取最近 N 根
        df = df.tail(tf["bars"])

        bars = []
        for ts, row in df.iterrows():
            bars.append({
                "ts":     int(ts.timestamp()) if hasattr(ts, 'timestamp') else 0,
                "open":   round(float(row["Open"]),   6),
                "high":   round(float(row["High"]),   6),
                "low":    round(float(row["Low"]),    6),
                "close":  round(float(row["Close"]),  6),
                "volume": int(row["Volume"]) if "Volume" in row else 0,
            })

        if not bars:
            return None

        current_price = bars[-1]["close"]

        data = {
            "symbol":        symbol,
            "name":          sym_info["name"],
            "category":      sym_info["category"],
            "interval":      tf["interval"],
            "label":         tf["label"],
            "current_price": current_price,
            "bars":          bars,
            "closes":        [b["close"]  for b in bars],
            "opens":         [b["open"]   for b in bars],
            "highs":         [b["high"]   for b in bars],
            "lows":          [b["low"]    for b in bars],
            "volumes":       [b["volume"] for b in bars],
            "timestamps":    [b["ts"]     for b in bars],
            "bar_count":     len(bars),
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "pip":           sym_info["pip"],
            "emoji":         sym_info.get("emoji", "📊"),
            "data_valid":    True,
        }

        return _cache_set(cache_key, data)

    except Exception as e:
        logger.error(f"Error fetching {symbol} {timeframe_key}: {e}")
        return None

def fetch_all_timeframes(symbol: str) -> Optional[dict]:
    """
    抓取一個品種的全部三個時框數據
    回傳 {trend: ..., mid: ..., entry: ...}
    三個時框都要成功才回傳，任何一個失敗就回傳 None
    """
    result = {}
    for tf_key in ["trend", "mid", "entry"]:
        data = fetch_ohlcv(symbol, tf_key)
        if data is None:
            logger.warning(f"Failed to fetch {symbol} {tf_key} — skipping this symbol")
            return None
        result[tf_key] = data

    return result

# ═══════════════════════════════════════════
# 宏觀市場數據
# ═══════════════════════════════════════════

def fetch_macro_data() -> dict:
    """
    使用 yfinance 抓取全球宏觀數據
    """
    cached = _cache_get("macro_data", 300)
    if cached:
        return cached

    macro = {
        "vix":    None,
        "dxy":    None,
        "us10y":  None,
        "sp500":  None,
        "gold":   None,
        "btc":    None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    if not YFINANCE_AVAILABLE:
        return macro

    yahoo_targets = {
        "vix":   "^VIX",
        "dxy":   "DX-Y.NYB",
        "us10y": "^TNX",
        "sp500": "^GSPC",
        "gold":  "GC=F",
        "btc":   "BTC-USD",
    }

    for key, sym in yahoo_targets.items():
        try:
            ticker = yf.Ticker(sym)
            hist   = ticker.history(period="5d", interval="1d")
            if hist is not None and len(hist) >= 2:
                price = float(hist["Close"].iloc[-1])
                prev  = float(hist["Close"].iloc[-2])
                chg   = round((price - prev) / prev * 100, 2) if prev else 0
                macro[key] = {
                    "price": round(price, 4),
                    "chg":   chg,
                    "prev":  round(prev, 4),
                }
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"Failed to fetch macro {key}: {e}")

    # CNN 恐懼貪婪指數（用 requests）
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=HEADERS, timeout=8
        )
        if r.status_code == 200:
            d = r.json()
            fg = d.get("fear_and_greed", {})
            score = float(fg.get("score", 50))
            macro["fear_greed"] = {
                "score": round(score, 1),
                "label": fg.get("rating", "neutral"),
                "label_zh": (
                    "極度恐慌" if score < 20 else
                    "恐慌"     if score < 40 else
                    "中性"     if score < 60 else
                    "貪婪"     if score < 80 else
                    "極度貪婪"
                ),
            }
        else:
            macro["fear_greed"] = {"score": 50, "label": "neutral", "label_zh": "中性"}
    except Exception as e:
        logger.warning(f"Failed to fetch fear greed: {e}")
        macro["fear_greed"] = {"score": 50, "label": "neutral", "label_zh": "中性"}

    return _cache_set("macro_data", macro)

def fetch_market_status() -> dict:
    """
    判斷目前是哪個交易時段
    亞洲盤 / 倫敦盤 / 紐約盤 / 休市
    """
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour
    weekday  = now_utc.weekday()  # 0=Monday, 6=Sunday

    if weekday >= 5:
        session = "weekend"
        session_zh = "週末休市"
        tradeable = False
    elif 22 <= hour_utc or hour_utc < 8:
        session = "asia"
        session_zh = "亞洲盤"
        tradeable = True
    elif 8 <= hour_utc < 13:
        session = "london"
        session_zh = "倫敦盤"
        tradeable = True
    elif 13 <= hour_utc < 22:
        session = "newyork"
        session_zh = "紐約盤"
        tradeable = True
    else:
        session = "overlap"
        session_zh = "倫敦/紐約重疊"
        tradeable = True

    # 最佳交易時段（流動性最高）
    best_sessions = {
        "london":  ["EURUSD", "GBPUSD", "EURGBP", "XAUUSD"],
        "newyork": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "WTI", "US500"],
        "asia":    ["USDJPY", "AUDUSD", "HK50", "BTCUSD"],
        "overlap": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],  # 最佳
        "weekend": ["BTCUSD", "ETHUSD"],  # 只有加密在跑
    }

    return {
        "session":       session,
        "session_zh":    session_zh,
        "tradeable":     tradeable,
        "hour_utc":      hour_utc,
        "weekday":       weekday,
        "best_symbols":  best_sessions.get(session, []),
        "utc_time":      now_utc.strftime("%H:%M UTC"),
    }
