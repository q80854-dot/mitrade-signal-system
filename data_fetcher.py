"""
data_fetcher.py v4.0 — 多源數據抓取
來源：yfinance + Alpha Vantage + Finnhub + FRED + CoinGecko + CNN
"""
import time, logging, requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

from config import (SYMBOLS, TIMEFRAMES, SECTOR_ETFS, US_FUTURES,
                    FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, FRED_API_KEY)

logger  = logging.getLogger(__name__)
HEADERS = {"User-Agent":"Mozilla/5.0"}
_cache: Dict = {}

def _cache_get(key, ttl=300):
    if key in _cache:
        if (datetime.now(timezone.utc)-_cache[key]["ts"]).total_seconds() < ttl:
            return _cache[key]["data"]
    return None

def _cache_set(key, data):
    _cache[key] = {"data":data,"ts":datetime.now(timezone.utc)}
    return data

def _req(url, params=None, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"Request failed {url}: {e}")
    return None

# ── K線（yfinance主力，Alpha Vantage備援）──

def _yf_bars(yahoo_sym, period, interval, bars):
    if not YFINANCE_OK: return None
    try:
        df = yf.Ticker(yahoo_sym).history(period=period, interval=interval)
        if df is None or len(df) < 10: return None
        df = df.tail(bars)
        out = []
        for ts, row in df.iterrows():
            try:
                out.append({
                    "ts":int(ts.timestamp()) if hasattr(ts,'timestamp') else 0,
                    "open":round(float(row["Open"]),6),
                    "high":round(float(row["High"]),6),
                    "low":round(float(row["Low"]),6),
                    "close":round(float(row["Close"]),6),
                    "volume":int(row.get("Volume",0)) if row.get("Volume") else 0,
                })
            except: continue
        return out if len(out) >= 10 else None
    except Exception as e:
        logger.warning(f"yfinance {yahoo_sym}: {e}")
        return None

def _av_bars(av_sym, interval):
    if not ALPHA_VANTAGE_KEY: return None
    av_map = {"1h":"60min","4h":"60min","1d":"Daily"}
    av_int = av_map.get(interval,"60min")
    func   = "TIME_SERIES_DAILY" if "Daily" in av_int else "TIME_SERIES_INTRADAY"
    ts_key = "Time Series (Daily)" if "Daily" in av_int else f"Time Series ({av_int})"
    params = {"function":func,"symbol":av_sym,"outputsize":"compact","apikey":ALPHA_VANTAGE_KEY}
    if func != "TIME_SERIES_DAILY": params["interval"] = av_int
    data = _req("https://www.alphavantage.co/query", params)
    if not data or ts_key not in data: return None
    out = []
    for _, ohlcv in list(data[ts_key].items())[:120]:
        try:
            out.append({
                "ts":0,
                "open":round(float(ohlcv["1. open"]),6),
                "high":round(float(ohlcv["2. high"]),6),
                "low":round(float(ohlcv["3. low"]),6),
                "close":round(float(ohlcv["4. close"]),6),
                "volume":int(float(ohlcv.get("5. volume",0))),
            })
        except: continue
    return list(reversed(out)) if len(out) >= 10 else None

def fetch_ohlcv(symbol, timeframe_key="entry"):
    if symbol not in SYMBOLS: return None
    tf  = TIMEFRAMES.get(timeframe_key)
    si  = SYMBOLS[symbol]
    if not tf: return None
    ck  = f"{symbol}_{timeframe_key}"
    ttl = 300 if tf["interval"] in ["1h","4h"] else 1800
    if c := _cache_get(ck, ttl): return c
    bars = _yf_bars(si["yahoo"], tf["period"], tf["interval"], tf["bars"])
    if not bars and si.get("av") and ALPHA_VANTAGE_KEY:
        logger.info(f"Alpha Vantage fallback for {symbol}")
        bars = _av_bars(si["av"], tf["interval"])
        time.sleep(0.5)
    if not bars: return None
    return _cache_set(ck, {
        "symbol":symbol,"name":si["name"],"category":si.get("cat","其他"),
        "interval":tf["interval"],"label":tf["label"],
        "current_price":bars[-1]["close"],"bars":bars,
        "closes":[b["close"] for b in bars],"opens":[b["open"] for b in bars],
        "highs":[b["high"] for b in bars],"lows":[b["low"] for b in bars],
        "volumes":[b["volume"] for b in bars],"timestamps":[b["ts"] for b in bars],
        "bar_count":len(bars),"fetched_at":datetime.now(timezone.utc).isoformat(),
        "pip":si.get("pip",0.0001),"emoji":si.get("emoji","📊"),"data_valid":True,
    })

def fetch_all_timeframes(symbol):
    result = {}
    for tf in ["trend","mid","entry"]:
        data = fetch_ohlcv(symbol, tf)
        if data is None: return None
        result[tf] = data
        time.sleep(0.3)
    return result

# ── 宏觀數據 ──

def fetch_macro_data():
    if c := _cache_get("macro_data", 600): return c
    macro = {"fetched_at":datetime.now(timezone.utc).isoformat()}
    if YFINANCE_OK:
        for key, sym in {"vix":"^VIX","dxy":"DX-Y.NYB","us10y":"^TNX",
                          "sp500":"^GSPC","gold":"GC=F","btc":"BTC-USD"}.items():
            try:
                h = yf.Ticker(sym).history(period="5d",interval="1d")
                if h is not None and len(h) >= 2:
                    p,pv = float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2])
                    macro[key] = {"price":round(p,4),"chg":round((p-pv)/pv*100,2) if pv else 0,"prev":round(pv,4)}
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"Macro {key}: {e}")
    # CNN 恐懼貪婪指數（多個備用來源）
    fg_score = None
    # 方法1：Alternative Fear & Greed API
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1",
            headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8)
        if r.status_code == 200:
            d = r.json()
            if d.get("data"):
                fg_score = float(d["data"][0]["value"])
    except: pass
    # 方法2：CNN 直接抓
    if fg_score is None:
        try:
            r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                         "Referer":"https://edition.cnn.com/markets/fear-and-greed"},
                timeout=8)
            if r.status_code == 200:
                d = r.json()
                fg_score = float(d.get("fear_and_greed",{}).get("score",50))
        except: pass
    s = fg_score if fg_score is not None else 50
    macro["fear_greed"] = {
        "score":round(s,1),"label":"neutral",
        "label_zh":"極度恐慌" if s<20 else "恐慌" if s<40 else "中性" if s<60 else "貪婪" if s<80 else "極度貪婪",
    }
    return _cache_set("macro_data", macro)

# ── FRED 美聯儲數據 ──

def fetch_fred_data():
    """抓取 FRED 數據（失敗時靜默跳過）"""
    if cc := _cache_get("fred_data", 3600): return cc
    if not FRED_API_KEY: return _cache_set("fred_data", {})
    fred   = {}
    series = {
        "cpi":"CPIAUCSL","core_cpi":"CPILFESL","gdp":"GDP",
        "unemployment":"UNRATE","fed_rate":"FEDFUNDS",
        "us10y_yield":"GS10","us2y_yield":"GS2",
    }
    for key, sid in series.items():
        try:
            d = _req("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id":sid,"api_key":FRED_API_KEY,
                             "file_type":"json","limit":2,"sort_order":"desc"},
                     timeout=5)
            if d and "observations" in d and d["observations"]:
                obs = d["observations"]
                try:
                    v  = float(obs[0]["value"])
                    pv = float(obs[1]["value"]) if len(obs)>1 else v
                    fred[key] = {"value":round(v,3),"prev":round(pv,3),
                                 "chg":round(v-pv,3),"date":obs[0]["date"]}
                except (ValueError, KeyError): pass
            time.sleep(0.2)
        except Exception as e:
            logger.debug(f"FRED {key} skipped: {e}")
            continue
    if "us10y_yield" in fred and "us2y_yield" in fred:
        sp = round(fred["us10y_yield"]["value"]-fred["us2y_yield"]["value"],3)
        fred["yield_curve"] = {"spread":sp,"inverted":sp<0,
            "label_zh":"倒掛（衰退警號）" if sp<0 else "正常"}
    return _cache_set("fred_data", fred)

# ── Finnhub：財報日曆 + 個股新聞 + 經濟事件 ──

def fetch_earnings_calendar(days_ahead=7):
    if c := _cache_get("earnings_calendar", 3600): return c
    if not FINNHUB_API_KEY: return []
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    d = _req("https://finnhub.io/api/v1/calendar/earnings",
             params={"from":now.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d or "earningsCalendar" not in d: return _cache_set("earnings_calendar",[])
    watched = set(SYMBOLS.keys())
    results = []
    for item in d["earningsCalendar"]:
        sym = item.get("symbol","").upper().replace(".US","")
        if sym in watched:
            results.append({
                "symbol":sym,"name":SYMBOLS.get(sym,{}).get("name",sym),
                "date":item.get("date",""),"eps_est":item.get("epsEstimate"),
                "eps_actual":item.get("epsActual"),"time":item.get("hour",""),
            })
    return _cache_set("earnings_calendar", results)

def fetch_company_news(symbol, days_back=2):
    ck = f"news_{symbol}"
    if c := _cache_get(ck, 900): return c
    if not FINNHUB_API_KEY: return []
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    d = _req("https://finnhub.io/api/v1/company-news",
             params={"symbol":symbol,"from":start.strftime("%Y-%m-%d"),
                     "to":now.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d: return _cache_set(ck,[])
    results = [{"headline":i.get("headline",""),"summary":i.get("summary","")[:200],
                "source":i.get("source",""),"url":i.get("url","")} for i in d[:5]]
    return _cache_set(ck, results)

# 別名
fetch_finnhub_news = fetch_company_news

def fetch_economic_calendar():
    if c := _cache_get("eco_calendar", 3600): return c
    if not FINNHUB_API_KEY: return []
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=7)
    d = _req("https://finnhub.io/api/v1/calendar/economic",
             params={"from":now.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d or "economicCalendar" not in d: return _cache_set("eco_calendar",[])
    keywords = ["Non-Farm","CPI","GDP","Federal Reserve","FOMC","Interest Rate","Unemployment","PMI","EIA","OPEC"]
    results  = []
    for e in d["economicCalendar"]:
        name   = e.get("event","")
        impact = e.get("impact","")
        if impact in ["high","medium"] or any(k.lower() in name.lower() for k in keywords):
            results.append({"event":name,"date":e.get("time",""),"country":e.get("country",""),
                            "impact":impact,"actual":e.get("actual"),"estimate":e.get("estimate")})
    return _cache_set("eco_calendar", results[:20])

# ── 美股期貨 ──

def fetch_us_futures():
    if c := _cache_get("us_futures", 300): return c
    if not YFINANCE_OK: return {}
    futures = {}
    for sym, name in US_FUTURES.items():
        try:
            h = yf.Ticker(sym).history(period="5d",interval="1d")
            if h is not None and len(h) >= 2:
                p,pv = float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2])
                chg  = round((p-pv)/pv*100,2) if pv else 0
                futures[sym] = {"name":name,"price":round(p,2),"chg":chg,
                    "bias":"bullish" if chg>0.1 else "bearish" if chg<-0.1 else "neutral"}
            time.sleep(0.2)
        except: pass
    if futures:
        avg = ((futures.get("ES=F",{}).get("chg",0))+(futures.get("NQ=F",{}).get("chg",0)))/2
        futures["overall"] = {
            "bias":"bullish" if avg>0.1 else "bearish" if avg<-0.1 else "neutral",
            "avg_chg":round(avg,2),
            "label":"美股預計高開" if avg>0.1 else "美股預計低開" if avg<-0.1 else "美股預計平開",
        }
    return _cache_set("us_futures", futures)

# ── 板塊 ETF ──

def fetch_sector_etf():
    if c := _cache_get("sector_etf", 3600): return c
    if not YFINANCE_OK: return {}
    data = {}
    for etf, name in SECTOR_ETFS.items():
        try:
            h = yf.Ticker(etf).history(period="5d",interval="1d")
            if h is not None and len(h) >= 2:
                p,pv = float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2])
                data[etf] = {"sector":name,"price":round(p,2),"chg":round((p-pv)/pv*100,2) if pv else 0}
            time.sleep(0.2)
        except: pass
    if data:
        s = sorted(data.items(), key=lambda x:x[1]["chg"], reverse=True)
        data["strongest"] = s[0][1]["sector"] if s else "—"
        data["weakest"]   = s[-1][1]["sector"] if s else "—"
        data["rotation"]  = f"資金流入{data['strongest']}，流出{data['weakest']}"
    return _cache_set("sector_etf", data)

# ── CoinGecko 加密情緒 ──

def fetch_crypto_sentiment():
    if c := _cache_get("crypto_sentiment", 1800): return c
    try:
        d = _req("https://api.coingecko.com/api/v3/global",timeout=10)
        if d and "data" in d:
            chg = round(d["data"].get("market_cap_change_percentage_24h_usd",0),2)
            btc = round(d["data"].get("market_cap_percentage",{}).get("btc",0),1)
            result = {
                "market_cap_change_24h":chg,"btc_dominance":btc,
                "sentiment":"bullish" if chg>1 else "bearish" if chg<-1 else "neutral",
                "sentiment_zh":"加密偏多" if chg>1 else "加密偏空" if chg<-1 else "加密中性",
            }
            return _cache_set("crypto_sentiment", result)
    except Exception as e:
        logger.debug(f"CoinGecko: {e}")
    return _cache_set("crypto_sentiment", {})

# ── 交易時段 ──

def fetch_market_status():
    now  = datetime.now(timezone.utc)
    h,wd = now.hour, now.weekday()
    if wd >= 5:        s,sz,t = "weekend","週末休市",False
    elif 22<=h or h<8: s,sz,t = "asia","亞洲盤",True
    elif 8<=h<13:      s,sz,t = "london","倫敦盤",True
    elif 13<=h<17:     s,sz,t = "overlap","倫紐重疊（最佳）",True
    else:              s,sz,t = "newyork","紐約盤",True
    best = {
        "asia":   ["USDJPY","AUDUSD","BTCUSD","HK50"],
        "london": ["EURUSD","GBPUSD","XAUUSD","GER40"],
        "overlap":["EURUSD","GBPUSD","USDJPY","XAUUSD","US500"],
        "newyork":["EURUSD","USDJPY","XAUUSD","WTI","US500","NAS100"],
        "weekend":["BTCUSD","ETHUSD"],
    }
    return {
        "session":s,"session_zh":sz,"tradeable":t,"hour_utc":h,"weekday":wd,
        "best_symbols":best.get(s,[]),"utc_time":now.strftime("%H:%M UTC"),
        "taiwan_time":(now+timedelta(hours=8)).strftime("%H:%M 台灣時間"),
        "friday_warning":wd==4 and h>=20,
    }
