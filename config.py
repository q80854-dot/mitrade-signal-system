"""
config.py v5.5 — 全面修正版

修正清單：
★ BUG #1：TIMEFRAMES bars=100 不足以提供 EMA50 所需數據 → 改為 150
★ BUG #2：period 設定太短（"1d","3d"）在非交易時段容易取到 0 根 → 加長
★ BUG #3：SIGNAL_THRESHOLDS min_score=65 過高，EMA 修復前幾乎全被過濾 → 調整為 62
★ BUG #4：CIRCUIT_BREAKER 缺少 max_simultaneous_trades 欄位（和 MAX_SIMULTANEOUS_TRADES 重複定義）→ 統一
★ BUG #5：INDICATOR_PARAMS adx_period=10 對 5M 超短線偏小 → 調整為 14
★ 新增 ACCOUNT_BALANCE_USD 直接從 env 讀取（已有，確認正確）
★ 新增每個 SYMBOLS 條目的 pip_value 欄位，供 signal_engine 直接使用
"""
import os
from dotenv import load_dotenv
load_dotenv()

GROK_API_KEY        = os.getenv("GROK_API_KEY", "")
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_KEY   = os.getenv("ALPHA_VANTAGE_KEY", "")
FRED_API_KEY        = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "8091656090")
ACCOUNT_BALANCE_USD = float(os.getenv("ACCOUNT_BALANCE_USD", "3000"))

MAX_RISK_PER_TRADE      = 0.02
MAX_DAILY_RISK          = 0.04   # ★ 超短線嚴格限制 4%
MAX_SIMULTANEOUS_TRADES = 3
MIN_ACCOUNT_FOR_INDEX   = 1500
MIN_ACCOUNT_FOR_STOCK   = 500

OVERNIGHT_SWAP = {
    "EURUSD": {"buy": -2.50, "sell":  1.80},
    "GBPUSD": {"buy": -3.20, "sell":  2.10},
    "USDJPY": {"buy":  1.50, "sell": -2.80},
    "AUDUSD": {"buy": -2.00, "sell":  1.20},
    "USDCAD": {"buy":  1.30, "sell": -2.50},
    "XAUUSD": {"buy": -3.50, "sell":  1.00},
    "WTI":    {"buy": -4.00, "sell":  2.00},
    "BTCUSD": {"buy":-15.00, "sell": 10.00},
    "ETHUSD": {"buy": -8.00, "sell":  5.00},
    "US500":  {"buy": -2.00, "sell":  0.80},
    "NAS100": {"buy": -3.00, "sell":  1.00},
    "US30":   {"buy": -2.50, "sell":  0.90},
    "HK50":   {"buy": -5.00, "sell":  2.00},
    "GER40":  {"buy": -2.00, "sell":  0.70},
}

TYPICAL_SPREAD = {
    "EURUSD": 0.0002, "GBPUSD": 0.0003, "USDJPY": 0.03,
    "AUDUSD": 0.0003, "USDCAD": 0.0003,
    "XAUUSD": 0.30,   "WTI":    0.05,
    "BTCUSD": 30.0,   "ETHUSD": 2.0,
    "US500":  0.5,    "NAS100": 1.5, "US30": 5.0, "HK50": 5.0, "GER40": 1.5,
}

# pip_value = 每手每 pip 的 USD 損益（Mitrade 官方規格）
SYMBOLS = {
    # ── 外匯 ──
    "EURUSD": {
        "yahoo":"EURUSD=X","av":"EUR","name":"EUR/USD","cat":"外匯",
        "pip":0.0001,"pip_value":10.0,"atr_mult":1.5,"priority":1,
        "emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","ECB"]
    },
    "GBPUSD": {
        "yahoo":"GBPUSD=X","av":"GBP","name":"GBP/USD","cat":"外匯",
        "pip":0.0001,"pip_value":10.0,"atr_mult":1.5,"priority":1,
        "emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","BOE"]
    },
    "USDJPY": {
        "yahoo":"JPY=X","av":"JPY","name":"USD/JPY","cat":"外匯",
        "pip":0.01,"pip_value":9.1,"atr_mult":1.5,"priority":1,
        "emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["Fed","BOJ"]
    },
    "AUDUSD": {
        "yahoo":"AUDUSD=X","av":"AUD","name":"AUD/USD","cat":"外匯",
        "pip":0.0001,"pip_value":10.0,"atr_mult":1.5,"priority":2,
        "emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["中國PMI"]
    },
    "USDCAD": {
        "yahoo":"CAD=X","av":"CAD","name":"USD/CAD","cat":"外匯",
        "pip":0.0001,"pip_value":7.7,"atr_mult":1.5,"priority":2,
        "emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["原油","BOC"]
    },
    # ── 商品 ──
    "XAUUSD": {
        "yahoo":"GC=F","av":"XAU","name":"黃金 XAU/USD","cat":"商品",
        "pip":0.1,"pip_value":10.0,"atr_mult":2.0,"priority":1,
        "emoji":"🥇","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","VIX"]
    },
    "WTI": {
        "yahoo":"CL=F","av":"WTI","name":"WTI 原油","cat":"商品",
        "pip":0.01,"pip_value":10.0,"atr_mult":2.0,"priority":2,
        "emoji":"🛢️","min_lot":0.01,"min_account":0,"special_conditions":True,"drivers":["EIA","OPEC"]
    },
    # ── 加密 ──
    "BTCUSD": {
        "yahoo":"BTC-USD","av":"BTC","name":"Bitcoin BTC/USD","cat":"加密",
        "pip":1.0,"pip_value":1.0,"atr_mult":2.5,"priority":1,
        "emoji":"₿","min_lot":0.01,"min_account":0,"drivers":["F&G","川普"]
    },
    "ETHUSD": {
        "yahoo":"ETH-USD","av":"ETH","name":"Ethereum ETH/USD","cat":"加密",
        "pip":0.1,"pip_value":0.1,"atr_mult":2.5,"priority":2,
        "emoji":"⟠","min_lot":0.01,"min_account":0,"drivers":["BTC走勢"]
    },
    # ── 指數 ──
    "US500": {
        "yahoo":"^GSPC","av":"SPY","name":"S&P 500","cat":"指數",
        "pip":0.1,"pip_value":0.1,"atr_mult":2.0,"priority":2,
        "emoji":"📈","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["Fed","VIX"]
    },
    "NAS100": {
        "yahoo":"^NDX","av":"QQQ","name":"納斯達克 100","cat":"指數",
        "pip":0.1,"pip_value":0.1,"atr_mult":2.5,"priority":2,
        "emoji":"💻","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["科技財報","Fed"]
    },
    "US30": {
        "yahoo":"^DJI","av":"DIA","name":"道瓊工業","cat":"指數",
        "pip":1.0,"pip_value":1.0,"atr_mult":2.0,"priority":3,
        "emoji":"🏭","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["工業財報"]
    },
    "HK50": {
        "yahoo":"^HSI","av":"EWH","name":"恒生指數","cat":"指數",
        "pip":1.0,"pip_value":0.13,"atr_mult":2.0,"priority":3,
        "emoji":"🇭🇰","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["中國政策"]
    },
    "GER40": {
        "yahoo":"^GDAXI","av":"EWG","name":"德國 DAX 40","cat":"指數",
        "pip":0.1,"pip_value":0.11,"atr_mult":2.0,"priority":3,
        "emoji":"🇩🇪","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["ECB"]
    },
}

SECTOR_ETFS = {
    "XLK":"科技","XLF":"金融","XLE":"能源","XLV":"醫療","XLY":"消費",
    "XLI":"工業","XLB":"材料","XLRE":"房地產","XLU":"公用事業","XLC":"通訊","XLP":"必需消費",
}
US_FUTURES = {
    "ES=F":"S&P500期貨","NQ=F":"納斯達克期貨","YM=F":"道瓊期貨",
    "GC=F":"黃金期貨","CL=F":"原油期貨",
}

# ── 指標參數（超短線版本）──
INDICATOR_PARAMS = {
    # EMA：超短線週期
    "ema_fast":  5,
    "ema_mid":  13,
    "ema_slow": 21,
    "ema_trend":50,   # ★ 最大需求 → TIMEFRAMES bars 必須 > 55
    # RSI
    "rsi_period":      9,
    "rsi_overbought": 70,
    "rsi_oversold":   30,
    "rsi_bull_zone":  45,
    "rsi_bear_zone":  55,
    # MACD
    "macd_fast":   5,
    "macd_slow":  13,
    "macd_signal": 4,
    # 布林帶
    "bb_period": 15,
    "bb_std":     2,
    # ATR / ADX
    "atr_period": 10,
    "adx_period": 14,  # ★ BUG #5 修正：從 10 改為 14，5M 超短線更穩定
    # 成交量
    "vol_period": 10,
    # 支撐壓力
    "support_lookback": 30,
    "fib_levels": [0.236, 0.382, 0.5, 0.618, 0.786],
}
IND = INDICATOR_PARAMS

# ── 時框設定 ──
# ★ BUG #1 修正：bars 從 100 → 150，確保 ema_trend(50)+5 = 55 根在任何情況下都滿足
# ★ BUG #2 修正：period 加長，避免非交易時段取到空資料
TIMEFRAMES = {
    "trend": {"interval": "1h",  "period": "15d", "bars": 150, "label": "1小時"},
    "mid":   {"interval": "15m", "period": "7d",  "bars": 150, "label": "15分鐘"},
    "entry": {"interval": "5m",  "period": "3d",  "bars": 150, "label": "5分鐘"},
}

# ── 熔斷器 ──
CIRCUIT_BREAKER = {
    "vix_extreme":         40,
    "vix_high":            30,
    "vix_threshold":       30,
    "price_spike":          3.0,
    "price_spike_pct":      3.0,
    "signal_expire_h":      1,
    "signal_expire_hours":  1,
    "eia_pause_min":       60,
    "eia_pause_minutes":   60,
    "news_pause_minutes":  15,
    "earnings_pause_days":  2,
    "weekend_gap_warning": True,
    "max_daily_signals":   10,
    "risk_per_trade_pct":   2.0,
    "max_lot":              2.0,
    "max_simultaneous":     3,   # ★ BUG #4 修正：統一定義
}
CB = CIRCUIT_BREAKER

# ★ BUG #3 修正：min_score 調整為 62（EMA 修復後仍留有緩衝）
SIGNAL_THRESHOLDS = {
    "min_score":       62,
    "high_conf":       80,
    "high_confidence": 80,
    "min_rr":          1.3,
    "min_rr_ratio":    1.3,
}
THRESH = SIGNAL_THRESHOLDS

CORRELATION_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD"],
    ["XAUUSD", "EURUSD"],
    ["WTI",    "USDCAD"],
    ["BTCUSD", "ETHUSD"],
    ["US500",  "NAS100", "US30"],
]

SYSTEM = {
    "scan_interval_min":      5,
    "scan_interval_minutes":  5,
    "trump_check_min":       30,
    "web_port":            5000,
    "version":         "5.5.0",
    "name":   "Mitrade AI Signal System",
    "timezone": "Asia/Taipei",
}

DISCLAIMER = (
    "⚠️ 本訊號由AI技術分析生成，僅供參考，不構成投資建議。"
    "CFD槓桿交易涉及高風險，請先於模擬帳戶驗證後再使用真實資金。盈虧自負。"
)
