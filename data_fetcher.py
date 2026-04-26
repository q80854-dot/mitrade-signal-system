"""
data_fetcher.py v5.2 — 多源交叉驗證
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
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_cache: Dict = {}

def _cache_get(key, ttl=300):
    if key in _cache:
        if (datetime.now(timezone.utc)-_cache[key]["ts"]).total_seconds()<ttl:
            return _cache[key]["data"]
    return None

def _cache_set(key, data):
    _cache[key]={"data":data,"ts":datetime.now(timezone.utc)}
    return data

def _req(url, params=None, timeout=10):
    try:
        r=requests.get(url,headers=HEADERS,params=params,timeout=timeout)
        if r.status_code==200: return r.json()
    except Exception as e: logger.debug(f"Request failed {url}: {e}")
    return None

def _cross_validate_price(prices: dict, symbol: str) -> dict:
    valid={k:v for k,v in prices.items() if v and v>0}
    if not valid: return {"price":0,"sources":[],"confidence":0,"stale":True}
    if len(valid)==1:
        src,p=next(iter(valid.items()))
        return {"price":p,"sources":[src],"confidence":0.6,"stale":False}
    vals=list(valid.values()); median=sorted(vals)[len(vals)//2]
    threshold=0.02
    trusted={k:v for k,v in valid.items() if abs(v-median)/median<=threshold}
    if not trusted: trusted=valid
    weights={"yfinance":0.5,"finnhub":0.3,"coingecko":0.15,"alphavantage":0.05}
    total_w=sum(weights.get(k,0.1) for k in trusted)
    weighted=sum(v*weights.get(k,0.1)/total_w for k,v in trusted.items())
    consistency=1-(max(trusted.values())-min(trusted.values()))/median if median>0 and len(trusted)>1 else 1
    confidence=min(1.0,(len(trusted)/3)*0.7+consistency*0.3)
    rejected=[k for k in valid if k not in trusted]
    if rejected: logger.warning(f"{symbol} 剔除偏差來源：{rejected}")
    return {"price":round(weighted,6),"sources":list(trusted.keys()),"rejected":rejected,
            "confidence":round(confidence,2),"stale":False,
            "spread_pct":round((max(trusted.values())-min(trusted.values()))/median*100,3) if len(trusted)>1 else 0}

def _yf_bars(yahoo_sym, period, interval, bars):
    if not YFINANCE_OK: return None
    try:
        df=yf.Ticker(yahoo_sym).history(period=period,interval=interval)
        if df is None or len(df)<10: return None
        df=df.tail(bars); out=[]
        for ts,row in df.iterrows():
            try:
                o=float(row["Open"]); h=float(row["High"]); l=float(row["Low"]); c=float(row["Close"])
                if h<l or c<=0 or o<=0: continue
                if h<max(o,c)*0.995: h=max(o,c)
                if l>min(o,c)*1.005: l=min(o,c)
                out.append({"ts":int(ts.timestamp()) if hasattr(ts,"timestamp") else 0,
                            "open":round(o,6),"high":round(h,6),"low":round(l,6),"close":round(c,6),
                            "volume":int(row.get("Volume",0)) if row.get("Volume") else 0})
            except: continue
        return out if len(out)>=10 else None
    except Exception as e: logger.warning(f"yfinance {yahoo_sym}: {e}"); return None

def _av_bars(av_sym, interval):
    if not ALPHA_VANTAGE_KEY: return None
    av_map={"1h":"60min","4h":"60min","1d":"Daily"}
    av_int=av_map.get(interval,"60min")
    func="TIME_SERIES_DAILY" if "Daily" in av_int else "TIME_SERIES_INTRADAY"
    ts_key="Time Series (Daily)" if "Daily" in av_int else f"Time Series ({av_int})"
    params={"function":func,"symbol":av_sym,"outputsize":"compact","apikey":ALPHA_VANTAGE_KEY}
    if func!="TIME_SERIES_DAILY": params["interval"]=av_int
    data=_req("https://www.alphavantage.co/query",params)
    if not data or ts_key not in data: return None
    out=[]
    for _,ohlcv in list(data[ts_key].items())[:120]:
        try:
            o=float(ohlcv["1. open"]); h=float(ohlcv["2. high"]); l=float(ohlcv["3. low"]); c=float(ohlcv["4. close"])
            if h<l or c<=0: continue
            out.append({"ts":0,"open":round(o,6),"high":round(h,6),"low":round(l,6),"close":round(c,6),
                        "volume":int(float(ohlcv.get("5. volume",0)))})
        except: continue
    return list(reversed(out)) if len(out)>=10 else None

def _clean_bars(bars, symbol):
    if not bars: return bars
    cleaned=[bars[0]]
    for i in range(1,len(bars)):
        prev_c=cleaned[-1]["close"]; curr_c=bars[i]["close"]
        if prev_c>0 and abs(curr_c-prev_c)/prev_c>0.15:
            logger.warning(f"{symbol} bar {i} 異常漲跌 {(curr_c-prev_c)/prev_c*100:.1f}%，跳過")
            continue
        cleaned.append(bars[i])
    return cleaned

def fetch_ohlcv(symbol, timeframe_key="entry"):
    if symbol not in SYMBOLS: return None
    tf=TIMEFRAMES.get(timeframe_key); si=SYMBOLS[symbol]
    if not tf: return None
    ck=f"{symbol}_{timeframe_key}"; ttl=300 if tf["interval"] in ["1h","4h"] else 1800
    if c:=_cache_get(ck,ttl): return c
    bars=_yf_bars(si["yahoo"],tf["period"],tf["interval"],tf["bars"])
    if not bars and si.get("av") and ALPHA_VANTAGE_KEY:
        bars=_av_bars(si["av"],tf["interval"]); time.sleep(0.5)
    if not bars: return None
    bars=_clean_bars(bars,symbol)
    return _cache_set(ck,{
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
    result={}
    for tf in ["trend","mid","entry"]:
        data=fetch_ohlcv(symbol,tf)
        if data is None: return None
        result[tf]=data; time.sleep(0.2)
    return result

def fetch_macro_data():
    if c:=_cache_get("macro_data",300): return c
    macro={"fetched_at":datetime.now(timezone.utc).isoformat()}

    if YFINANCE_OK:
        for key,sym in {"vix":"^VIX","dxy":"DX-Y.NYB","sp500":"^GSPC"}.items():
            try:
                h=yf.Ticker(sym).history(period="5d",interval="1d")
                if h is not None and len(h)>=2:
                    p,pv=float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2])
                    valid=(5<=p<=90) if key=="vix" else (70<=p<=130) if key=="dxy" else p>1000
                    if valid: macro[key]={"price":round(p,4),"chg":round((p-pv)/pv*100,3),"prev":round(pv,4),"source":"yfinance"}
                time.sleep(0.2)
            except Exception as e: logger.warning(f"Macro yf {key}: {e}")

    gold_prices={}
    if YFINANCE_OK:
        try:
            h=yf.Ticker("GC=F").history(period="5d",interval="1d")
            if h is not None and len(h)>=1: gold_prices["yfinance"]=float(h["Close"].iloc[-1])
        except: pass
    if FINNHUB_API_KEY:
        try:
            r=requests.get("https://finnhub.io/api/v1/quote",params={"symbol":"OANDA:XAU_USD","token":FINNHUB_API_KEY},headers=HEADERS,timeout=5)
            if r.status_code==200 and r.json().get("c"): gold_prices["finnhub"]=float(r.json()["c"])
        except: pass
    if gold_prices:
        cv=_cross_validate_price(gold_prices,"XAUUSD")
        if cv["price"]>0:
            prev=macro.get("gold",{}).get("price",cv["price"]); chg=round((cv["price"]-prev)/prev*100,3) if prev else 0
            macro["gold"]={"price":cv["price"],"chg":chg,"prev":round(prev,2),"sources":cv["sources"],"confidence":cv["confidence"],"source":"+".join(cv["sources"])}

    btc_prices={}; btc_chg_val=0.0
    if YFINANCE_OK:
        try:
            h=yf.Ticker("BTC-USD").history(period="5d",interval="1d")
            if h is not None and len(h)>=1: btc_prices["yfinance"]=float(h["Close"].iloc[-1])
        except: pass
    if FINNHUB_API_KEY:
        try:
            r=requests.get("https://finnhub.io/api/v1/quote",params={"symbol":"BINANCE:BTCUSDT","token":FINNHUB_API_KEY},headers=HEADERS,timeout=5)
            if r.status_code==200 and r.json().get("c"): btc_prices["finnhub"]=float(r.json()["c"])
        except: pass
    try:
        r=requests.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":"bitcoin","vs_currencies":"usd","include_24hr_change":"true"},headers=HEADERS,timeout=8)
        if r.status_code==200:
            d=r.json().get("bitcoin",{})
            if d.get("usd"): btc_prices["coingecko"]=float(d["usd"]); btc_chg_val=float(d.get("usd_24h_change",0) or 0)
    except: pass
    if btc_prices:
        cv=_cross_validate_price(btc_prices,"BTCUSD")
        if cv["price"]>0:
            prev=macro.get("btc",{}).get("price",cv["price"])
            chg=btc_chg_val if btc_chg_val else round((cv["price"]-prev)/prev*100,2) if prev else 0
            macro["btc"]={"price":round(cv["price"],0),"chg":round(chg,2),"prev":round(prev,0),"sources":cv["sources"],"confidence":cv["confidence"],"spread_pct":cv.get("spread_pct",0),"source":"+".join(cv["sources"])}

    fg_scores={}
    try:
        r=requests.get("https://api.alternative.me/fng/?limit=1",headers=HEADERS,timeout=8)
        if r.status_code==200 and r.json().get("data"): fg_scores["alternative"]=float(r.json()["data"][0]["value"])
    except: pass
    try:
        r=requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",headers={"User-Agent":"Mozilla/5.0","Referer":"https://edition.cnn.com/markets/fear-and-greed"},timeout=8)
        if r.status_code==200:
            sc=r.json().get("fear_and_greed",{}).get("score")
            if sc: fg_scores["cnn"]=float(sc)
    except: pass
    if fg_scores:
        vals=list(fg_scores.values()); s=round(sum(vals)/len(vals),1)
        diff=round(max(vals)-min(vals),1) if len(vals)>1 else 0
        if diff>10: logger.warning(f"F&G 來源差異 {diff}，取平均 {s}")
        macro["fear_greed"]={"score":s,"label":"neutral",
            "label_zh":"極度恐慌" if s<20 else "恐慌" if s<40 else "中性" if s<60 else "貪婪" if s<80 else "極度貪婪",
            "sources":list(fg_scores.keys()),"source_diff":diff}
    else:
        macro["fear_greed"]={"score":50,"label_zh":"中性（無數據）","sources":[]}

    filled=sum(1 for k in ["vix","dxy","sp500","gold","btc","fear_greed"] if k in macro)
    macro["data_quality"]={"filled_sources":filled,"total_sources":6,"quality_pct":round(filled/6*100),"fetched_at":datetime.now(timezone.utc).isoformat()}
    return _cache_set("macro_data",macro)

def _fred_fallback():
    return {"fed_rate":{"value":5.25,"prev":5.50,"chg":-0.25,"date":"2024-09","note":"備用"},
            "cpi":{"value":3.2,"prev":3.4,"chg":-0.20,"date":"2024-09","note":"備用"},
            "core_cpi":{"value":3.3,"prev":3.5,"chg":-0.20,"date":"2024-09","note":"備用"},
            "unemployment":{"value":4.1,"prev":4.0,"chg":+0.10,"date":"2024-09","note":"備用"},
            "yield_curve":{"spread":0.15,"inverted":False,"label_zh":"正常","note":"備用"}}

def fetch_fred_data():
    if cc:=_cache_get("fred_data",3600): return cc
    if not FRED_API_KEY: return _cache_set("fred_data",_fred_fallback())
    fred={}
    for key,sid in {"cpi":"CPIAUCSL","core_cpi":"CPILFESL","gdp":"GDP","unemployment":"UNRATE","fed_rate":"FEDFUNDS","us10y_yield":"GS10","us2y_yield":"GS2"}.items():
        try:
            d=_req("https://api.stlouisfed.org/fred/series/observations",params={"series_id":sid,"api_key":FRED_API_KEY,"file_type":"json","limit":2,"sort_order":"desc"},timeout=5)
            if d and "observations" in d and d["observations"]:
                obs=d["observations"]
                try:
                    v=float(obs[0]["value"]); pv=float(obs[1]["value"]) if len(obs)>1 else v
                    fred[key]={"value":round(v,3),"prev":round(pv,3),"chg":round(v-pv,3),"date":obs[0]["date"],"source":"fred"}
                except (ValueError,KeyError): pass
            time.sleep(0.2)
        except Exception as e: logger.debug(f"FRED {key}: {e}"); continue
    if "us10y_yield" in fred and "us2y_yield" in fred:
        sp=round(fred["us10y_yield"]["value"]-fred["us2y_yield"]["value"],3)
        fred["yield_curve"]={"spread":sp,"inverted":sp<0,"label_zh":"倒掛（衰退警號）" if sp<0 else "正常"}
    fallback=_fred_fallback()
    for key in ["fed_rate","cpi","core_cpi","unemployment","yield_curve"]:
        if key not in fred: fred[key]={**fallback.get(key,{}),"note":"備用數據"}
    return _cache_set("fred_data",fred)

def fetch_earnings_calendar(days_ahead=7):
    if c:=_cache_get("earnings_calendar",3600): return c
    if not FINNHUB_API_KEY: return []
    now=datetime.now(timezone.utc); end=now+timedelta(days=days_ahead)
    d=_req("https://finnhub.io/api/v1/calendar/earnings",params={"from":now.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d or "earningsCalendar" not in d: return _cache_set("earnings_calendar",[])
    watched=set(SYMBOLS.keys()); results=[]
    for item in d["earningsCalendar"]:
        sym=item.get("symbol","").upper().replace(".US","")
        if sym in watched:
            results.append({"symbol":sym,"name":SYMBOLS.get(sym,{}).get("name",sym),"date":item.get("date",""),
                            "eps_est":item.get("epsEstimate"),"eps_actual":item.get("epsActual"),"time":item.get("hour","")})
    return _cache_set("earnings_calendar",results)

def fetch_company_news(symbol, days_back=2):
    ck=f"news_{symbol}"
    if c:=_cache_get(ck,900): return c
    if not FINNHUB_API_KEY: return []
    now=datetime.now(timezone.utc); start=now-timedelta(days=days_back)
    d=_req("https://finnhub.io/api/v1/company-news",params={"symbol":symbol,"from":start.strftime("%Y-%m-%d"),"to":now.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d: return _cache_set(ck,[])
    return _cache_set(ck,[{"headline":i.get("headline",""),"summary":i.get("summary","")[:200],"source":i.get("source",""),"url":i.get("url","")} for i in d[:5]])

fetch_finnhub_news=fetch_company_news

def fetch_economic_calendar():
    if c:=_cache_get("eco_calendar",3600): return c
    if not FINNHUB_API_KEY: return []
    now=datetime.now(timezone.utc); end=now+timedelta(days=7)
    d=_req("https://finnhub.io/api/v1/calendar/economic",params={"from":now.strftime("%Y-%m-%d"),"to":end.strftime("%Y-%m-%d"),"token":FINNHUB_API_KEY})
    if not d or "economicCalendar" not in d: return _cache_set("eco_calendar",[])
    keywords=["Non-Farm","CPI","GDP","Federal Reserve","FOMC","Interest Rate","Unemployment","PMI","EIA","OPEC"]
    results=[]
    for e in d["economicCalendar"]:
        name=e.get("event",""); impact=e.get("impact","")
        if impact in ["high","medium"] or any(k.lower() in name.lower() for k in keywords):
            results.append({"event":name,"date":e.get("time",""),"country":e.get("country",""),"impact":impact,"actual":e.get("actual"),"estimate":e.get("estimate")})
    return _cache_set("eco_calendar",results[:20])

def fetch_us_futures():
    if c:=_cache_get("us_futures",300): return c
    if not YFINANCE_OK: return {}
    futures={}
    for sym,name in US_FUTURES.items():
        try:
            h=yf.Ticker(sym).history(period="5d",interval="1d")
            if h is not None and len(h)>=2:
                p,pv=float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2]); chg=round((p-pv)/pv*100,2) if pv else 0
                futures[sym]={"name":name,"price":round(p,2),"chg":chg,"bias":"bullish" if chg>0.1 else "bearish" if chg<-0.1 else "neutral"}
            time.sleep(0.2)
        except: pass
    if futures:
        avg=((futures.get("ES=F",{}).get("chg",0))+(futures.get("NQ=F",{}).get("chg",0)))/2
        futures["overall"]={"bias":"bullish" if avg>0.1 else "bearish" if avg<-0.1 else "neutral","avg_chg":round(avg,2),
                            "label":"美股預計高開" if avg>0.1 else "美股預計低開" if avg<-0.1 else "美股預計平開"}
    return _cache_set("us_futures",futures)

def fetch_sector_etf():
    if c:=_cache_get("sector_etf",3600): return c
    if not YFINANCE_OK: return {}
    data={}
    for etf,name in SECTOR_ETFS.items():
        try:
            h=yf.Ticker(etf).history(period="5d",interval="1d")
            if h is not None and len(h)>=2:
                p,pv=float(h["Close"].iloc[-1]),float(h["Close"].iloc[-2])
                data[etf]={"sector":name,"price":round(p,2),"chg":round((p-pv)/pv*100,2) if pv else 0}
            time.sleep(0.2)
        except: pass
    if data:
        s=sorted(data.items(),key=lambda x:x[1]["chg"],reverse=True)
        data["strongest"]=s[0][1]["sector"] if s else "—"; data["weakest"]=s[-1][1]["sector"] if s else "—"
        data["rotation"]=f"資金流入{data['strongest']}，流出{data['weakest']}"
    return _cache_set("sector_etf",data)

def fetch_crypto_sentiment():
    if c:=_cache_get("crypto_sentiment",1800): return c
    try:
        d=_req("https://api.coingecko.com/api/v3/global",timeout=10)
        if d and "data" in d:
            chg=round(d["data"].get("market_cap_change_percentage_24h_usd",0),2)
            btc=round(d["data"].get("market_cap_percentage",{}).get("btc",0),1)
            return _cache_set("crypto_sentiment",{"market_cap_change_24h":chg,"btc_dominance":btc,
                "sentiment":"bullish" if chg>1 else "bearish" if chg<-1 else "neutral",
                "sentiment_zh":"加密偏多" if chg>1 else "加密偏空" if chg<-1 else "加密中性"})
    except Exception as e: logger.debug(f"CoinGecko: {e}")
    return _cache_set("crypto_sentiment",{})

def fetch_market_status():
    now=datetime.now(timezone.utc); h,wd=now.hour,now.weekday()
    if wd>=5:        s,sz,t="weekend","週末休市",False
    elif 22<=h or h<8: s,sz,t="asia","亞洲盤",True
    elif 8<=h<13:    s,sz,t="london","倫敦盤",True
    elif 13<=h<17:   s,sz,t="overlap","倫紐重疊（最佳）",True
    else:            s,sz,t="newyork","紐約盤",True
    best={"asia":["USDJPY","AUDUSD","BTCUSD","HK50"],"london":["EURUSD","GBPUSD","XAUUSD","GER40"],
          "overlap":["EURUSD","GBPUSD","USDJPY","XAUUSD","US500"],"newyork":["EURUSD","USDJPY","XAUUSD","WTI","US500","NAS100"],"weekend":["BTCUSD","ETHUSD"]}
    return {"session":s,"session_zh":sz,"tradeable":t,"hour_utc":h,"weekday":wd,
            "best_symbols":best.get(s,[]),"utc_time":now.strftime("%H:%M UTC"),
            "taiwan_time":(now+timedelta(hours=8)).strftime("%H:%M 台灣時間"),"friday_warning":wd==4 and h>=20}
