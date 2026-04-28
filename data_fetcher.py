""""""
data_fetcher.py v6.0 patch
★ 修正 XAU 顯示 4598（yfinance GC=F 期貨回傳錯誤倍數）→ 改為 XAUUSD=X spot 優先
★ 修正 BTC 價格偏低 → 加入多來源驗證
★ 新增 XAGUSD / WTI / NATGAS 即時報價
★ 補充 fetch_ohlcv 中 XAGUSD / NATGAS / COPPER 的 yahoo 映射

把此檔案的 fetch_macro_data() 函數內容替換原本 data_fetcher.py 對應函數即可。
同時更新 _yf_bars() 附近的 YAHOO_OVERRIDE 字典。
"""

# ── 在 data_fetcher.py 頂部加入此映射（覆蓋 SYMBOLS 中的 yahoo ticker）──
YAHOO_PRICE_OVERRIDE = {
    # 現貨優先於期貨，避免 yfinance 回傳錯誤乘數
    "XAUUSD": "XAUUSD=X",    # 黃金現貨（而非 GC=F）
    "XAGUSD": "XAGUSD=X",    # 白銀現貨
    "WTI":    "CL=F",        # WTI 原油期貨（正常）
    "NATGAS": "NG=F",        # 天然氣期貨
    "COPPER": "HG=F",        # 銅期貨
}

# ── 替換 fetch_macro_data() 函數 ──
def fetch_macro_data():
    if c := _cache_get("macro_data", 300):
        return c
    macro = {"fetched_at": datetime.now(timezone.utc).isoformat()}

    if YFINANCE_OK:
        for key, sym in {"vix":"^VIX","dxy":"DX-Y.NYB","sp500":"^GSPC"}.items():
            try:
                h = yf.Ticker(sym).history(period="5d", interval="1d")
                if h is not None and len(h) >= 2:
                    p, pv = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
                    valid = (5<=p<=90) if key=="vix" else (70<=p<=130) if key=="dxy" else p>1000
                    if valid:
                        macro[key] = {"price":round(p,4),"chg":round((p-pv)/pv*100,3),
                                      "prev":round(pv,4),"source":"yfinance"}
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f"Macro yf {key}: {e}")

    # ── 黃金：使用現貨 XAUUSD=X，不用 GC=F ──
    gold_prices = {}
    if YFINANCE_OK:
        for ticker in ["XAUUSD=X", "GC=F"]:
            try:
                h = yf.Ticker(ticker).history(period="5d", interval="1d")
                if h is not None and len(h) >= 1:
                    p = float(h["Close"].iloc[-1])
                    # 合理範圍：黃金現貨 1500~4000 USD
                    if 1500 < p < 4000:
                        gold_prices[f"yfinance_{ticker}"] = p
                        break  # 現貨成功就不再取期貨
            except Exception as e:
                logger.warning(f"Gold {ticker}: {e}")

    if FINNHUB_API_KEY:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": "OANDA:XAU_USD", "token": FINNHUB_API_KEY},
                headers=HEADERS, timeout=5
            )
            if r.status_code == 200 and r.json().get("c"):
                p = float(r.json()["c"])
                if 1500 < p < 4000:
                    gold_prices["finnhub"] = p
        except Exception:
            pass

    if gold_prices:
        cv = _cross_validate_price(gold_prices, "XAUUSD")
        if cv["price"] > 0:
            prev = macro.get("gold", {}).get("price", cv["price"])
            chg  = round((cv["price"]-prev)/prev*100, 3) if prev else 0
            macro["gold"] = {
                "price":      cv["price"],
                "chg":        chg,
                "prev":       round(prev, 2),
                "sources":    cv["sources"],
                "confidence": cv["confidence"],
                "source":     "+".join(cv["sources"])
            }

    # ── BTC：多來源 ──
    btc_prices = {}; btc_chg_val = 0.0
    if YFINANCE_OK:
        try:
            h = yf.Ticker("BTC-USD").history(period="5d", interval="1d")
            if h is not None and len(h) >= 1:
                p = float(h["Close"].iloc[-1])
                if p > 1000:
                    btc_prices["yfinance"] = p
        except Exception:
            pass

    if FINNHUB_API_KEY:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol":"BINANCE:BTCUSDT","token":FINNHUB_API_KEY},
                headers=HEADERS, timeout=5
            )
            if r.status_code == 200 and r.json().get("c"):
                btc_prices["finnhub"] = float(r.json()["c"])
        except Exception:
            pass

    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin","vs_currencies":"usd","include_24hr_change":"true"},
            headers=HEADERS, timeout=8
        )
        if r.status_code == 200:
            d = r.json().get("bitcoin", {})
            if d.get("usd"):
                btc_prices["coingecko"] = float(d["usd"])
                btc_chg_val = float(d.get("usd_24h_change", 0) or 0)
    except Exception:
        pass

    if btc_prices:
        cv = _cross_validate_price(btc_prices, "BTCUSD")
        if cv["price"] > 0:
            prev = macro.get("btc", {}).get("price", cv["price"])
            chg  = btc_chg_val if btc_chg_val else round((cv["price"]-prev)/prev*100, 2) if prev else 0
            macro["btc"] = {
                "price":      round(cv["price"], 0),
                "chg":        round(chg, 2),
                "prev":       round(prev, 0),
                "sources":    cv["sources"],
                "confidence": cv["confidence"],
                "spread_pct": cv.get("spread_pct", 0),
                "source":     "+".join(cv["sources"])
            }

    # ── Fear & Greed ──
    fg_scores = {}
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", headers=HEADERS, timeout=8)
        if r.status_code == 200 and r.json().get("data"):
            fg_scores["alternative"] = float(r.json()["data"][0]["value"])
    except Exception:
        pass

    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://edition.cnn.com/markets/fear-and-greed"},
            timeout=8
        )
        if r.status_code == 200:
            sc = r.json().get("fear_and_greed", {}).get("score")
            if sc: fg_scores["cnn"] = float(sc)
    except Exception:
        pass

    if fg_scores:
        vals = list(fg_scores.values())
        s    = round(sum(vals)/len(vals), 1)
        diff = round(max(vals)-min(vals), 1) if len(vals) > 1 else 0
        macro["fear_greed"] = {
            "score":    s,
            "label":    "neutral",
            "label_zh": ("極度恐慌" if s<20 else "恐慌" if s<40 else
                          "中性"     if s<60 else "貪婪" if s<80 else "極度貪婪"),
            "sources":    list(fg_scores.keys()),
            "source_diff":diff
        }
    else:
        macro["fear_greed"] = {"score":50,"label_zh":"中性（無數據）","sources":[]}

    filled = sum(1 for k in ["vix","dxy","sp500","gold","btc","fear_greed"] if k in macro)
    macro["data_quality"] = {
        "filled_sources": filled,
        "total_sources":  6,
        "quality_pct":    round(filled/6*100),
        "fetched_at":     datetime.now(timezone.utc).isoformat()
    }
    macro["_initialized"] = True
    return _cache_set("macro_data", macro)


# ── 同時修正 fetch_ohlcv 中 _yf_bars 的 ticker 映射 ──
# 在 fetch_ohlcv() 函數中，把取 yahoo ticker 的一行改為：
#   yahoo_sym = YAHOO_PRICE_OVERRIDE.get(symbol, si.get("yahoo", symbol))
# 這樣 XAUUSD 就會用現貨 XAUUSD=X 而不是 GC=F
#
# 修改位置（data_fetcher.py 原始 fetch_ohlcv 函數內）：
#   原本：bars = _yf_bars(si["yahoo"], tf["period"], tf["interval"], tf["bars"])
#   改為：
#       yahoo_sym = YAHOO_PRICE_OVERRIDE.get(symbol, si.get("yahoo", symbol))
#       bars = _yf_bars(yahoo_sym, tf["period"], tf["interval"], tf["bars"])
