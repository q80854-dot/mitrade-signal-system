"""
data_fetcher.py — 數據抓取模組（雲端穩定版）
使用 yfinance 抓取 K 線與宏觀數據
"""
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

from config import SYMBOLS, TIMEFRAMES

logger = logging.getLogger(__name__)

# 快取
_cache: Dict = {}

def _cache_get(key: str, ttl: int = 300) -> Optional[dict]:
    if key in _cache:
        age = (datetime.now(timezone.utc) - _cache[key]["ts"]).total_seconds()
        if age < ttl:
            return _cache[key]["data"]
    return None

def _cache_set(key: str, data: dict) -> dict:
    _cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}
    return data


def fetch_ohlcv(symbol: str, timeframe_key: str = "entry") -> Optional[dict]:
    """抓取 K 線數據"""
    if not YFINANCE_OK:
        return None
    if symbol not in SYMBOLS:
        return None

    tf = TIMEFRAMES.get(timeframe_key)
    if not tf:
        return None

    sym_info  = SYMBOLS[symbol]
    yahoo_sym = sym_info["yahoo"]
    cache_key = f"{symbol}_{timeframe_key}"

    ttl    = 300 if tf["interval"] in ["1h", "4h"] else 1800
    cached = _cache_get(cache_key, ttl)
    if cached:
        return cached

    try:
        ticker = yf.Ticker(yahoo_sym)

        # yfinance 新版用 period 參數
        period   = tf.get("period", tf.get("range", "30d"))
        interval = tf["interval"]

        df = ticker.history(period=period, interval=interval)

        if df is None or len(df) < 10:
            logger.warning(f"Insufficient data for {symbol} {timeframe_key}: {len(df) if df is not None else 0}")
            return None

        df = df.tail(tf["bars"])

        bars = []
        for ts, row in df.iterrows():
            try:
                bars.append({
                    "ts":     int(ts.timestamp()) if hasattr(ts, 'timestamp') else 0,
                    "open":   round(float(row["Open"]),   6),
                    "high":   round(float(row["High"]),   6),
                    "low":    round(float(row["Low"]),    6),
                    "close":  round(float(row["Close"]),  6),
                    "volume": int(row.get("Volume", 0)) if row.get("Volume") else 0,
                })
            except Exception:
                continue

        if len(bars) < 10:
            return None

        current_price = bars[-1]["close"]

        data = {
            "symbol":        symbol,
            "name":          sym_info["name"],
            "category":      sym_info.get("cat", sym_info.get("category", "其他")),
            "interval":      interval,
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
            "pip":           sym_info.get("pip", 0.0001),
            "emoji":         sym_info.get("emoji", "📊"),
            "data_valid":    True,
        }

        return _cache_set(cache_key, data)

    except Exception as e:
        logger.error(f"Error fetching {symbol} {timeframe_key}: {e}")
        return None


def fetch_all_timeframes(symbol: str) -> Optional[dict]:
    """抓取一個品種的全部三個時框"""
    result = {}
    for tf_key in ["trend", "mid", "entry"]:
        data = fetch_ohlcv(symbol, tf_key)
        if data is None:
            logger.warning(f"Failed {symbol} {tf_key}")
            return None
        result[tf_key] = data
        time.sleep(0.3)  # 避免限速
    return result


def fetch_macro_data() -> dict:
    """抓取宏觀數據"""
    cached = _cache_get("macro_data", 600)  # 快取10分鐘
    if cached:
        return cached

    macro = {"fetched_at": datetime.now(timezone.utc).isoformat()}

    if not YFINANCE_OK:
        return macro

    targets = {
        "vix":   "^VIX",
        "dxy":   "DX-Y.NYB",
        "us10y": "^TNX",
        "sp500": "^GSPC",
        "gold":  "GC=F",
        "btc":   "BTC-USD",
    }

    for key, sym in targets.items():
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
            time.sleep(0.5)  # 避免限速
        except Exception as e:
            logger.warning(f"Failed macro {key}: {e}")

    # CNN 恐懼貪婪指數
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if r.status_code == 200:
            fg    = r.json().get("fear_and_greed", {})
            score = float(fg.get("score", 50))
            macro["fear_greed"] = {
                "score":    round(score, 1),
                "label":    fg.get("rating", "neutral"),
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
        logger.warning(f"Fear greed failed: {e}")
        macro["fear_greed"] = {"score": 50, "label": "neutral", "label_zh": "中性"}

    return _cache_set("macro_data", macro)


def fetch_market_status() -> dict:
    """判斷目前交易時段"""
    now     = datetime.now(timezone.utc)
    hour    = now.hour
    weekday = now.weekday()

    if weekday >= 5:
        session, session_zh, tradeable = "weekend", "週末休市", False
    elif 22 <= hour or hour < 8:
        session, session_zh, tradeable = "asia", "亞洲盤", True
    elif 8 <= hour < 13:
        session, session_zh, tradeable = "london", "倫敦盤", True
    elif 13 <= hour < 17:
        session, session_zh, tradeable = "overlap", "倫紐重疊（最佳）", True
    else:
        session, session_zh, tradeable = "newyork", "紐約盤", True

    best = {
        "asia":    ["USDJPY", "AUDUSD", "BTCUSD"],
        "london":  ["EURUSD", "GBPUSD", "XAUUSD"],
        "overlap": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        "newyork": ["EURUSD", "USDJPY", "XAUUSD", "WTI"],
        "weekend": ["BTCUSD", "ETHUSD"],
    }

    return {
        "session":      session,
        "session_zh":   session_zh,
        "tradeable":    tradeable,
        "hour_utc":     hour,
        "weekday":      weekday,
        "best_symbols": best.get(session, []),
        "utc_time":     now.strftime("%H:%M UTC"),
    }
