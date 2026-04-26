""" config.py v5.2 — 修正版（移除美股、更新備用數據） """
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

MAX_RISK_PER_TRADE      = 0.03
MAX_DAILY_RISK          = 0.06
MAX_SIMULTANEOUS_TRADES = 3
MIN_ACCOUNT_FOR_INDEX   = 1500
MIN_ACCOUNT_FOR_STOCK   = 500

# ★ 移除美股品種，只保留外匯/商品/加密/指數
OVERNIGHT_SWAP = {
    "EURUSD": {"buy":-2.50,"sell": 1.80},
    "GBPUSD": {"buy":-3.20,"sell": 2.10},
    "USDJPY": {"buy": 1.50,"sell":-2.80},
    "AUDUSD": {"buy":-2.00,"sell": 1.20},
    "USDCAD": {"buy": 1.30,"sell":-2.50},
    "XAUUSD": {"buy":-3.50,"sell": 1.00},
    "WTI":    {"buy":-4.00,"sell": 2.00},
    "BTCUSD": {"buy":-15.0,"sell":10.00},
    "ETHUSD": {"buy":-8.00,"sell": 5.00},
    "US500":  {"buy":-2.00,"sell": 0.80},
    "NAS100": {"buy":-3.00,"sell": 1.00},
    "US30":   {"buy":-2.50,"sell": 0.90},
    "HK50":   {"buy":-5.00,"sell": 2.00},
    "GER40":  {"buy":-2.00,"sell": 0.70},
}

TYPICAL_SPREAD = {
    "EURUSD":0.0002,"GBPUSD":0.0003,"USDJPY":0.03,"AUDUSD":0.0003,"USDCAD":0.0003,
    "XAUUSD":0.30,"WTI":0.05,"BTCUSD":30.0,"ETHUSD":2.0,
    "US500":0.5,"NAS100":1.5,"US30":5.0,"HK50":5.0,"GER40":1.5,
}

# ★ SYMBOLS：移除全部美股，只保留外匯/商品/加密/指數
SYMBOLS = {
    # 外匯
    "EURUSD": {"yahoo":"EURUSD=X","av":"EUR","name":"EUR/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","ECB","CPI","非農"]},
    "GBPUSD": {"yahoo":"GBPUSD=X","av":"GBP","name":"GBP/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["DXY","BOE","英國CPI"]},
    "USDJPY": {"yahoo":"JPY=X",   "av":"JPY","name":"USD/JPY","cat":"外匯","pip":0.01,  "atr_mult":1.5,"priority":1,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["Fed","BOJ","日本干預"]},
    "AUDUSD": {"yahoo":"AUDUSD=X","av":"AUD","name":"AUD/USD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":2,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["中國PMI","鐵礦石"]},
    "USDCAD": {"yahoo":"CAD=X",   "av":"CAD","name":"USD/CAD","cat":"外匯","pip":0.0001,"atr_mult":1.5,"priority":2,"emoji":"💱","min_lot":0.01,"min_account":0,"drivers":["原油","BOC"]},
    # 商品
    "XAUUSD": {"yahoo":"GC=F","av":"XAU","name":"黃金 XAU/USD","cat":"商品","pip":0.1,"atr_mult":2.0,"priority":1,"emoji":"🥇","min_lot":0.01,"min_account":0,"drivers":["DXY","Fed","VIX","川普"]},
    "WTI":    {"yahoo":"CL=F","av":"WTI","name":"WTI 原油",    "cat":"商品","pip":0.01,"atr_mult":2.0,"priority":2,"emoji":"🛢️","min_lot":0.01,"min_account":0,"special_conditions":True,"drivers":["EIA","OPEC","中東"]},
    # 加密
    "BTCUSD": {"yahoo":"BTC-USD","av":"BTC","name":"Bitcoin BTC/USD",    "cat":"加密","pip":1.0,"atr_mult":2.5,"priority":1,"emoji":"₿", "min_lot":0.01,"min_account":0,"drivers":["恐懼貪婪","川普"]},
    "ETHUSD": {"yahoo":"ETH-USD","av":"ETH","name":"Ethereum ETH/USD","cat":"加密","pip":0.1,"atr_mult":2.5,"priority":2,"emoji":"⟠","min_lot":0.01,"min_account":0,"drivers":["BTC走勢"]},
    # 指數
    "US500":  {"yahoo":"^GSPC","av":"SPY","name":"S&P 500",      "cat":"指數","pip":0.1,"atr_mult":2.0,"priority":2,"emoji":"📈","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["Fed","財報季","VIX"]},
    "NAS100": {"yahoo":"^NDX","av":"QQQ","name":"納斯達克 100","cat":"指數","pip":0.1,"atr_mult":2.5,"priority":2,"emoji":"💻","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["科技股財報","Fed"]},
    "US30":   {"yahoo":"^DJI","av":"DIA","name":"道瓊工業",      "cat":"指數","pip":1.0,"atr_mult":2.0,"priority":2,"emoji":"🏭","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["工業股財報","Fed"]},
    "HK50":   {"yahoo":"^HSI","av":"EWH","name":"恒生指數",      "cat":"指數","pip":1.0,"atr_mult":2.0,"priority":3,"emoji":"🇭🇰","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["中國政策","人民幣"]},
    "GER40":  {"yahoo":"^GDAXI","av":"EWG","name":"德國 DAX 40","cat":"指數","pip":0.1,"atr_mult":2.0,"priority":3,"emoji":"🇩🇪","min_lot":0.1,"min_account":1500,"account_warning":True,"drivers":["ECB","歐元區PMI"]},
}

SECTOR_ETFS = {
    "XLK":"科技","XLF":"金融","XLE":"能源","XLV":"醫療","XLY":"消費",
    "XLI":"工業","XLB":"材料","XLRE":"房地產","XLU":"公用事業","XLC":"通訊","XLP":"必需消費",
}

US_FUTURES = {
    "ES=F":"S&P500期貨","NQ=F":"納斯達克期貨","YM=F":"道瓊期貨","GC=F":"黃金期貨","CL=F":"原油期貨",
}

INDICATOR_PARAMS = {
    "ema_fast":9,"ema_mid":21,"ema_slow":50,"ema_trend":200,
    "rsi_period":14,"rsi_overbought":70,"rsi_oversold":30,
    "rsi_bull_zone":45,"rsi_bear_zone":55,
    "macd_fast":12,"macd_slow":26,"macd_signal":9,
    "bb_period":20,"bb_std":2,"atr_period":14,"vol_period":20,
    "adx_period":14,
    "support_lookback":50,
    "fib_levels":[0.236,0.382,0.5,0.618,0.786],
}
IND = INDICATOR_PARAMS

TIMEFRAMES = {
    "trend":{"interval":"1d","period":"6mo","bars":120,"label":"日線"},
    "mid":  {"interval":"4h","period":"60d","bars":120,"label":"4小時"},
    "entry":{"interval":"1h","period":"30d","bars":120,"label":"1小時"},
}

CIRCUIT_BREAKER = {
    "vix_extreme":40,"vix_high":30,"vix_threshold":30,
    "price_spike":3.0,"price_spike_pct":3.0,
    "signal_expire_h":4,"signal_expire_hours":4,
    "eia_pause_min":120,"eia_pause_minutes":120,
    "news_pause_minutes":30,"earnings_pause_days":2,
    "weekend_gap_warning":True,"max_daily_signals":10,
    "risk_per_trade_pct":2.0,
    "max_lot":2.0,
}
CB = CIRCUIT_BREAKER

SIGNAL_THRESHOLDS = {
    "min_score":65,"high_conf":80,"high_confidence":80,
    "min_rr":1.3,"min_rr_ratio":1.3,
}
THRESH = SIGNAL_THRESHOLDS

# ★ 移除美股相關品種群組
CORRELATION_GROUPS = [
    ["EURUSD","GBPUSD","AUDUSD"],
    ["XAUUSD","EURUSD"],
    ["WTI","USDCAD"],
    ["BTCUSD","ETHUSD"],
    ["US500","NAS100","US30"],
]

SYSTEM = {
    "scan_interval_min":15,"scan_interval_minutes":15,
    "trump_check_min":30,"web_port":5000,
    "version":"5.2.0","name":"Mitrade AI Signal System","timezone":"Asia/Taipei",
}

DISCLAIMER = (
    "⚠️ 本訊號由AI技術分析生成，僅供參考，不構成投資建議。"
    "CFD槓桿交易涉及高風險，請先於模擬帳戶驗證後再使用真實資金。盈虧自負。"
)
