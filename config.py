"""
config.py — 系統設定
"""
import os
from dotenv import load_dotenv
load_dotenv()

# API 金鑰
GROK_API_KEY        = os.getenv("GROK_API_KEY", "")
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "8091656090")

# 帳戶設定
ACCOUNT_BALANCE_USD = float(os.getenv("ACCOUNT_BALANCE_USD", "750"))
MAX_RISK_PER_TRADE  = 0.03
MAX_DAILY_RISK      = 0.06

# 品種清單
SYMBOLS = {
    "EURUSD": {"yahoo":"EURUSD=X", "name":"EUR/USD",  "cat":"外匯", "pip":0.0001, "atr_mult":1.5, "priority":1, "emoji":"💱", "min_lot":0.01},
    "GBPUSD": {"yahoo":"GBPUSD=X", "name":"GBP/USD",  "cat":"外匯", "pip":0.0001, "atr_mult":1.5, "priority":1, "emoji":"💱", "min_lot":0.01},
    "USDJPY": {"yahoo":"JPY=X",    "name":"USD/JPY",  "cat":"外匯", "pip":0.01,   "atr_mult":1.5, "priority":1, "emoji":"💱", "min_lot":0.01},
    "AUDUSD": {"yahoo":"AUDUSD=X", "name":"AUD/USD",  "cat":"外匯", "pip":0.0001, "atr_mult":1.5, "priority":2, "emoji":"💱", "min_lot":0.01},
    "USDCAD": {"yahoo":"CAD=X",    "name":"USD/CAD",  "cat":"外匯", "pip":0.0001, "atr_mult":1.5, "priority":2, "emoji":"💱", "min_lot":0.01},
    "XAUUSD": {"yahoo":"GC=F",     "name":"黃金",     "cat":"商品", "pip":0.1,    "atr_mult":2.0, "priority":1, "emoji":"🥇", "min_lot":0.01},
    "WTI":    {"yahoo":"CL=F",     "name":"WTI原油",  "cat":"商品", "pip":0.01,   "atr_mult":2.0, "priority":2, "emoji":"🛢️", "min_lot":0.01, "special_conditions":True},
    "BTCUSD": {"yahoo":"BTC-USD",  "name":"Bitcoin",  "cat":"加密", "pip":1.0,    "atr_mult":2.5, "priority":2, "emoji":"₿",  "min_lot":0.01},
    "ETHUSD": {"yahoo":"ETH-USD",  "name":"Ethereum", "cat":"加密", "pip":0.1,    "atr_mult":2.5, "priority":3, "emoji":"⟠",  "min_lot":0.01},
}

# 技術指標參數
INDICATOR_PARAMS = {
    "ema_fast":9, "ema_mid":21, "ema_slow":50, "ema_trend":200,
    "rsi_period":14, "rsi_overbought":70, "rsi_oversold":30,
    "rsi_bull_zone":45, "rsi_bear_zone":55,
    "macd_fast":12, "macd_slow":26, "macd_signal":9,
    "bb_period":20, "bb_std":2,
    "atr_period":14,
    "vol_period":20,
}
IND = INDICATOR_PARAMS  # 別名

# 時間框架
TIMEFRAMES = {
    "trend": {"interval":"1d", "period":"6mo", "bars":120, "label":"日線"},
    "mid":   {"interval":"4h", "period":"60d", "bars":120, "label":"4小時"},
    "entry": {"interval":"1h", "period":"30d", "bars":120, "label":"1小時"},
}

# 熔斷條件（同時提供兩個名稱相容舊版）
CIRCUIT_BREAKER = {
    "vix_extreme":    40,
    "vix_high":       30,
    "vix_threshold":  30,
    "price_spike":     3.0,
    "price_spike_pct": 3.0,
    "signal_expire_h": 4,
    "signal_expire_hours": 4,
    "eia_pause_min":  120,
    "eia_pause_minutes": 120,
    "news_pause_minutes": 30,
}
CB = CIRCUIT_BREAKER  # 別名

# 訊號閾值（同時提供兩個名稱）
SIGNAL_THRESHOLDS = {
    "min_score":    65,
    "high_conf":    80,
    "high_confidence": 80,
    "min_rr":       1.3,
    "min_rr_ratio": 1.3,
}
THRESH = SIGNAL_THRESHOLDS  # 別名

# 系統設定
SYSTEM = {
    "scan_interval_min": 15,
    "scan_interval_minutes": 15,
    "trump_check_min":   30,
    "web_port":          5000,
    "version":           "2.0.0",
    "name":              "Mitrade AI Signal System",
    "timezone":          "Asia/Taipei",
}

DISCLAIMER = "⚠️ 本訊號由AI技術分析生成，僅供參考，不構成投資建議。交易有風險，盈虧自負。"
