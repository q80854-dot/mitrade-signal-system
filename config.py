"""
config.py v4.0 — 完整設定檔
包含所有 API Key、品種、指標、風控參數
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ══════════════════════════════════════════
# API 金鑰
# ══════════════════════════════════════════
GROK_API_KEY        = os.getenv("GROK_API_KEY", "")
FINNHUB_API_KEY     = os.getenv("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_KEY   = os.getenv("ALPHA_VANTAGE_KEY", "")
FRED_API_KEY        = os.getenv("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "8091656090")

# ══════════════════════════════════════════
# 帳戶設定
# ══════════════════════════════════════════
ACCOUNT_BALANCE_USD     = float(os.getenv("ACCOUNT_BALANCE_USD", "3000"))
MAX_RISK_PER_TRADE      = 0.03   # 每筆最大風險 3%
MAX_DAILY_RISK          = 0.06   # 每日最大風險 6%
MAX_SIMULTANEOUS_TRADES = 3      # 同時最多持倉數量
MIN_ACCOUNT_FOR_INDEX   = 1500   # 指數最低帳戶門檻
MIN_ACCOUNT_FOR_STOCK   = 500    # 個股最低帳戶門檻

# ══════════════════════════════════════════
# Overnight Swap 費率（大約值，以帳戶%計）
# 每個品種持倉超過一天的費用
# ══════════════════════════════════════════
OVERNIGHT_SWAP = {
    "外匯":  0.0002,   # 約 0.02%/天
    "商品":  0.0003,   # 約 0.03%/天
    "指數":  0.0002,   # 約 0.02%/天
    "美股":  0.0003,   # 約 0.03%/天
    "加密":  0.0005,   # 約 0.05%/天（最高）
}

# Mitrade 大概點差（用於計算實際成本）
TYPICAL_SPREAD = {
    "EURUSD": 0.0002, "GBPUSD": 0.0003, "USDJPY": 0.03,
    "AUDUSD": 0.0003, "USDCAD": 0.0003,
    "XAUUSD": 0.30,   "WTI":    0.05,
    "BTCUSD": 30.0,   "ETHUSD": 2.0,
    "US500":  0.5,    "NAS100": 1.5,   "US30":   5.0,
    "HK50":   5.0,    "GER40":  1.5,
    "AAPL":   0.05,   "NVDA":   0.10,  "TSLA":   0.15,
    "MSFT":   0.10,   "AMZN":   0.10,  "GOOGL":  0.10,
}

# ══════════════════════════════════════════
# 品種清單
# ══════════════════════════════════════════
SYMBOLS = {
    # ── 外匯 ─────────────────────────────
    "EURUSD": {"yahoo":"EURUSD=X","av":"EUR","name":"EUR/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","ECB","CPI","非農"]},
    "GBPUSD": {"yahoo":"GBPUSD=X","av":"GBP","name":"GBP/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","BOE","英國CPI"]},
    "USDJPY": {"yahoo":"JPY=X",   "av":"JPY","name":"USD/JPY","cat":"外匯","pip":0.01,  "atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["Fed","BOJ","日本干預"]},
    "AUDUSD": {"yahoo":"AUDUSD=X","av":"AUD","name":"AUD/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":2,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["中國PMI","鐵礦石"]},
    "USDCAD": {"yahoo":"CAD=X",   "av":"CAD","name":"USD/CAD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":2,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["原油","BOC"]},

    # ── 商品 ─────────────────────────────
    "XAUUSD": {"yahoo":"GC=F",   "av":"XAU","name":"黃金 XAU/USD","cat":"商品","pip":0.1, "atr_mult":2.0,"priority":1,"emoji":"🥇","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","VIX","川普"]},
    "WTI":    {"yahoo":"CL=F",   "av":"WTI","name":"WTI 原油",    "cat":"商品","pip":0.01,"atr_mult":2.0,"priority":2,"emoji":"🛢️","min_lot":0.01,"min_account":0,"special_conditions":True,"drivers":["EIA","OPEC","中東"]},

    # ── 加密 ─────────────────────────────
    "BTCUSD": {"yahoo":"BTC-USD","av":"BTC","name":"Bitcoin BTC/USD",    "cat":"加密","pip":1.0,"atr_mult":2.5,"priority":2,"emoji":"₿", "min_lot":0.01,"min_account":0,"drivers":["恐懼貪婪","川普"]},
    "ETHUSD": {"yahoo":"ETH-USD","av":"ETH","name":"Ethereum ETH/USD",   "cat":"加密","pip":0.1,"atr_mult":2.5,"priority":3,"emoji":"⟠","min_lot":0.01,"min_account":0,"drivers":["BTC走勢"]},

    # ── 指數 ─────────────────────────────
    "US500":  {"yahoo":"^GSPC", "av":"SPY", "name":"S&P 500",      "cat":"指數","pip":0.1,"atr_mult":2.0,"priority":2,"emoji":"📈","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_INDEX,"account_warning":True,"drivers":["Fed","財報季","VIX"]},
    "NAS100": {"yahoo":"^NDX",  "av":"QQQ", "name":"納斯達克 100", "cat":"指數","pip":0.1,"atr_mult":2.5,"priority":2,"emoji":"💻","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_INDEX,"account_warning":True,"drivers":["科技股財報","Fed"]},
    "US30":   {"yahoo":"^DJI",  "av":"DIA", "name":"道瓊工業",     "cat":"指數","pip":1.0,"atr_mult":2.0,"priority":2,"emoji":"🏭","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_INDEX,"account_warning":True,"drivers":["工業股財報","Fed"]},
    "HK50":   {"yahoo":"^HSI",  "av":"EWH", "name":"恒生指數",     "cat":"指數","pip":1.0,"atr_mult":2.0,"priority":3,"emoji":"🇭🇰","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_INDEX,"account_warning":True,"drivers":["中國政策","人民幣"]},
    "GER40":  {"yahoo":"^GDAXI","av":"EWG", "name":"德國 DAX 40",  "cat":"指數","pip":0.1,"atr_mult":2.0,"priority":3,"emoji":"🇩🇪","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_INDEX,"account_warning":True,"drivers":["ECB","歐元區PMI"]},

    # ── 美股個股 ─────────────────────────
    "AAPL":  {"yahoo":"AAPL","av":"AAPL","name":"蘋果 Apple",   "cat":"美股","pip":0.01,"atr_mult":2.0,"priority":2,"emoji":"🍎","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["財報","iPhone","中國"]},
    "NVDA":  {"yahoo":"NVDA","av":"NVDA","name":"輝達 NVIDIA",  "cat":"美股","pip":0.01,"atr_mult":3.0,"priority":2,"emoji":"🖥️","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["AI需求","財報","出口限制"]},
    "TSLA":  {"yahoo":"TSLA","av":"TSLA","name":"特斯拉 Tesla", "cat":"美股","pip":0.01,"atr_mult":3.5,"priority":2,"emoji":"⚡","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["馬斯克","財報","電動車"]},
    "MSFT":  {"yahoo":"MSFT","av":"MSFT","name":"微軟 Microsoft","cat":"美股","pip":0.01,"atr_mult":2.0,"priority":3,"emoji":"🪟","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["Azure","AI","財報"]},
    "AMZN":  {"yahoo":"AMZN","av":"AMZN","name":"亞馬遜 Amazon","cat":"美股","pip":0.01,"atr_mult":2.5,"priority":3,"emoji":"📦","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["AWS","零售","財報"]},
    "GOOGL": {"yahoo":"GOOGL","av":"GOOGL","name":"Alphabet Google","cat":"美股","pip":0.01,"atr_mult":2.0,"priority":3,"emoji":"🔍","min_lot":0.1,"min_account":MIN_ACCOUNT_FOR_STOCK,"account_warning":True,"earnings_sensitive":True,"drivers":["廣告","AI","財報"]},
}

# ══════════════════════════════════════════
# 板塊 ETF（用於板塊強弱分析）
# ══════════════════════════════════════════
SECTOR_ETFS = {
    "XLK": "科技",  "XLF": "金融",  "XLE": "能源",
    "XLV": "醫療",  "XLY": "消費",  "XLI": "工業",
    "XLB": "材料",  "XLRE":"房地產","XLU": "公用事業",
    "XLC": "通訊",  "XLP": "必需消費",
}

# 美股期貨（開盤前方向指標）
US_FUTURES = {
    "ES=F":  "S&P500期貨",
    "NQ=F":  "納斯達克期貨",
    "YM=F":  "道瓊期貨",
    "GC=F":  "黃金期貨",
    "CL=F":  "原油期貨",
}

# ══════════════════════════════════════════
# 技術指標參數
# ══════════════════════════════════════════
INDICATOR_PARAMS = {
    "ema_fast":9,"ema_mid":21,"ema_slow":50,"ema_trend":200,
    "rsi_period":14,"rsi_overbought":70,"rsi_oversold":30,
    "rsi_bull_zone":45,"rsi_bear_zone":55,
    "macd_fast":12,"macd_slow":26,"macd_signal":9,
    "bb_period":20,"bb_std":2,
    "atr_period":14,"vol_period":20,
    "support_lookback":50,   # 支撐壓力位回看K線數
    "fib_levels":[0.236,0.382,0.5,0.618,0.786],  # Fibonacci 回調位
}
IND = INDICATOR_PARAMS

# ══════════════════════════════════════════
# 時間框架
# ══════════════════════════════════════════
TIMEFRAMES = {
    "trend": {"interval":"1d","period":"6mo","bars":120,"label":"日線"},
    "mid":   {"interval":"4h","period":"60d","bars":120,"label":"4小時"},
    "entry": {"interval":"1h","period":"30d","bars":120,"label":"1小時"},
}

# ══════════════════════════════════════════
# 熔斷與風控條件
# ══════════════════════════════════════════
CIRCUIT_BREAKER = {
    "vix_extreme":40,"vix_high":30,"vix_threshold":30,
    "price_spike":3.0,"price_spike_pct":3.0,
    "signal_expire_h":4,"signal_expire_hours":4,
    "eia_pause_min":120,"eia_pause_minutes":120,
    "news_pause_minutes":30,
    "earnings_pause_days":2,
    "weekend_gap_warning":True,   # 週五提醒縮倉
    "max_daily_signals":10,       # 每日最多發出訊號數
}
CB = CIRCUIT_BREAKER

# ══════════════════════════════════════════
# 訊號閾值
# ══════════════════════════════════════════
SIGNAL_THRESHOLDS = {
    "min_score":65,"high_conf":80,"high_confidence":80,
    "min_rr":1.3,"min_rr_ratio":1.3,
}
THRESH = SIGNAL_THRESHOLDS

# ══════════════════════════════════════════
# 品種相關性矩陣（用於風控）
# 相關性 > 0.7 → 同向，不能同時開
# ══════════════════════════════════════════
CORRELATION_GROUPS = [
    ["EURUSD","GBPUSD","AUDUSD"],  # 同受美元影響
    ["XAUUSD","EURUSD"],           # 黃金和歐元通常同向
    ["WTI","USDCAD"],              # 原油和加元高度相關
    ["BTCUSD","ETHUSD"],           # 加密同向
    ["US500","NAS100","US30"],     # 美股指數同向
    ["AAPL","MSFT","NVDA","GOOGL","AMZN"],  # 科技股同向
]

# ══════════════════════════════════════════
# 系統設定
# ══════════════════════════════════════════
SYSTEM = {
    "scan_interval_min":15,"scan_interval_minutes":15,
    "trump_check_min":30,
    "web_port":5000,
    "version":"4.0.0",
    "name":"Mitrade AI Signal System",
    "timezone":"Asia/Taipei",
}

DISCLAIMER = (
    "⚠️ 本訊號由AI技術分析生成，僅供參考，不構成投資建議。"
    "CFD槓桿交易涉及高風險，請先於模擬帳戶驗證後再使用真實資金。盈虧自負。"
)
