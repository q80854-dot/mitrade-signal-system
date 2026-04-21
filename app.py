"""
app.py v4.0 — 主程式（完整雲端版）
新增：每日虧損追蹤、持倉追蹤、勝率統計、財報/期貨數據整合
"""
import logging, threading, time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from config import SYMBOLS, SYSTEM, CB, THRESH, DISCLAIMER, TELEGRAM_CHAT_ID
from telegram_bot import (send_message, format_signal_message, format_alert_message,
                           format_briefing_message, format_trump_alert,
                           format_stats_message, check_and_process_commands)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

import os as _os
_tpl = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'templates')
app  = Flask(__name__, template_folder=_tpl)

# ═══════════════════════════════════════
# 系統狀態
# ═══════════════════════════════════════
class SystemState:
    def __init__(self):
        self.active_signals:    List[Dict] = []
        self.expired_signals:   List[Dict] = []
        self.macro_data:        Dict = {}
        self.trump_data:        Dict = {}
        self.daily_briefing:    Dict = {}
        self.system_status:     Dict = {}
        self.market_session:    Dict = {}
        self.fred_data:         Dict = {}
        self.earnings_calendar: List = []
        self.us_futures_data:   Dict = {}
        self.sector_etf_data:   Dict = {}
        self.signal_log:        List = []   # 歷史訊號記錄（勝率追蹤）
        self.last_scan_time:    Optional[str] = None
        self.last_trump_check:  Optional[str] = None
        self.last_briefing_date:Optional[str] = None
        self.scan_count:   int = 0
        self.signal_count: int = 0
        self.version = SYSTEM["version"]
        self._lock = threading.Lock()

    def add_signal(self, sig):
        with self._lock:
            now = datetime.now(timezone.utc)
            sig["expires_at"] = (now+timedelta(hours=CB["signal_expire_h"])).isoformat()
            sig["status"]     = "active"
            symbol, direction = sig.get("symbol"), sig.get("direction")
            self.active_signals = [s for s in self.active_signals
                if not (s.get("symbol")==symbol and s.get("direction")==direction)]
            self.active_signals.append(sig)
            # 加入歷史記錄
            self.signal_log.append({
                "id":        sig.get("id",""),
                "symbol":    symbol,
                "direction": direction,
                "score":     sig.get("score",0),
                "entry":     sig.get("entry_price",0),
                "sl":        sig.get("stop_loss",0),
                "tp1":       sig.get("tp1",0),
                "tp2":       sig.get("tp2",0),
                "generated": now.isoformat(),
                "result":    "pending",
            })
            self.signal_log    = self.signal_log[-500:]
            self.active_signals = sorted(self.active_signals,
                key=lambda x:x.get("score",0),reverse=True)[:20]
            self.signal_count += 1
            # 記錄每日風險
            from risk_manager import record_signal_loss
            record_signal_loss(sig.get("risk_usd",0))

    def expire_old(self):
        with self._lock:
            now = datetime.now(timezone.utc)
            valid = []
            for s in self.active_signals:
                exp = s.get("expires_at")
                if exp:
                    try:
                        if now < datetime.fromisoformat(exp): valid.append(s)
                        else:
                            s["status"] = "expired"
                            # 更新歷史記錄
                            for h in self.signal_log:
                                if h["id"] == s.get("id") and h["result"] == "pending":
                                    h["result"] = "expired"
                            self.expired_signals.append(s)
                    except: valid.append(s)
                else: valid.append(s)
            self.active_signals  = valid
            self.expired_signals = self.expired_signals[-200:]

    def calc_win_rate(self):
        """計算歷史勝率"""
        completed = [s for s in self.signal_log if s.get("result") not in ["pending","expired"]]
        if not completed: return {"total":0,"wins":0,"losses":0,"win_rate":0}
        wins   = len([s for s in completed if s.get("result") in ["tp1","tp2"]])
        losses = len([s for s in completed if s.get("result") == "sl"])
        return {"total":len(completed),"wins":wins,"losses":losses,
                "win_rate":round(wins/len(completed)*100,1) if completed else 0}

    def get_snapshot(self):
        with self._lock:
            return {
                "active_signals":    list(self.active_signals),
                "macro_data":        dict(self.macro_data),
                "trump_data":        dict(self.trump_data),
                "daily_briefing":    dict(self.daily_briefing),
                "system_status":     dict(self.system_status),
                "market_session":    dict(self.market_session),
                "fred_data":         dict(self.fred_data),
                "earnings_calendar": list(self.earnings_calendar),
                "us_futures_data":   dict(self.us_futures_data),
                "sector_etf_data":   dict(self.sector_etf_data),
                "signal_log":        list(self.signal_log[-100:]),
                "win_rate":          self.calc_win_rate(),
                "last_scan_time":    self.last_scan_time,
                "scan_count":        self.scan_count,
                "signal_count":      self.signal_count,
                "version":           self.version,
                "disclaimer":        DISCLAIMER,
            }

state = SystemState()

# ═══════════════════════════════════════
# 掃描邏輯
# ═══════════════════════════════════════
def update_all_data():
    """更新所有宏觀和輔助數據"""
    try:
        from data_fetcher import (fetch_macro_data, fetch_market_status,
                                   fetch_fred_data, fetch_earnings_calendar,
                                   fetch_us_futures, fetch_sector_etf,
                                   fetch_crypto_sentiment, fetch_economic_calendar)
        from risk_manager import get_system_status

        state.macro_data        = fetch_macro_data()
        state.market_session    = fetch_market_status()
        state.fred_data         = fetch_fred_data()
        state.earnings_calendar = fetch_earnings_calendar()
        state.us_futures_data   = fetch_us_futures()
        state.sector_etf_data   = fetch_sector_etf()

        # 整合到 macro_data 供 signal_engine 使用
        state.macro_data["earnings_calendar"] = state.earnings_calendar
        state.macro_data["us_futures"]        = state.us_futures_data
        state.macro_data["sector_etf"]        = state.sector_etf_data
        state.macro_data["fred"]              = state.fred_data

        crypto_sent = fetch_crypto_sentiment()
        state.macro_data["crypto_sentiment"]  = crypto_sent

        state.system_status = get_system_status(state.macro_data)
        logger.info("All data updated")
    except Exception as e:
        logger.error(f"Data update error: {e}")

def run_scan():
    logger.info(f"=== Scan #{state.scan_count+1} ===")
    update_all_data()
    state.expire_old()

    # 環境太差 → 停止
    env = state.system_status.get("env_score",100)
    if env < 20:
        send_message(format_alert_message("市場極度危險",f"VIX 極端，今日訊號暫停","danger"))
        state.scan_count += 1
        state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return

    # 財報警告
    ec = state.earnings_calendar
    if ec:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_earnings = [e for e in ec if e.get("date","").startswith(today)]
        if today_earnings:
            syms = ", ".join([e.get("symbol","") for e in today_earnings])
            send_message(format_alert_message("📅 今日財報提醒",
                f"以下品種今日發布財報，進場前請確認：{syms}","earnings"))

    # 掃描品種
    syms_to_scan = sorted(
        [s for s in SYMBOLS if not SYMBOLS[s].get("monitor_only")],
        key=lambda s: SYMBOLS[s].get("priority",3)
    )

    new_signals = []
    for symbol in syms_to_scan:
        try:
            sig = _scan_symbol(symbol)
            if sig:
                state.add_signal(sig)
                new_signals.append(sig)
                send_message(format_signal_message(sig))
                logger.info(f"✅ {symbol} {sig['direction']} {sig['score']}/100")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Scan {symbol}: {e}")

    # 如果有多個訊號，發一則摘要
    if len(new_signals) >= 3:
        summary = f"📡 <b>本次掃描摘要</b>：{len(new_signals)} 個新訊號\n"
        for s in new_signals[:5]:
            d = "🟢" if s.get("direction")=="buy" else "🔴"
            summary += f"{d} {s.get('emoji','')} {s.get('name','')} {s.get('score',0)}/100\n"
        send_message(summary)

    state.scan_count    += 1
    state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"Scan done. New:{len(new_signals)} Active:{len(state.active_signals)}")

def _scan_symbol(symbol):
    try:
        from data_fetcher  import fetch_all_timeframes
        from signal_engine import generate_signal, check_multi_timeframe
        from risk_manager  import run_all_checks, check_correlation_risk

        tf = fetch_all_timeframes(symbol)
        if not tf:
            logger.info(f"  {symbol}: ❌ 無K線數據")
            return None

        risk = run_all_checks(symbol, tf, state.macro_data, state.active_signals)
        if not risk.get("can_signal"):
            logger.info(f"  {symbol}: ⛔ 風控阻止 {risk.get('blockers',[])[0][:40] if risk.get('blockers') else ''}")
            return None

        # 多時框預分析（幫助 debug）
        mtf = check_multi_timeframe(tf)
        logger.info(f"  {symbol}: 方向={mtf.get('direction','?')} 分數={mtf.get('score',0)} 共振={mtf.get('resonance',False)}")

        sig = generate_signal(symbol, tf, state.macro_data)
        if not sig:
            logger.info(f"  {symbol}: 分數不足或無方向（需≥65）")
            return None

        # 相關性風控
        corr = check_correlation_risk(symbol, sig.get("direction",""), state.active_signals)
        if corr.get("correlated"):
            sig["risk_warnings"] = risk.get("warnings",[]) + [corr["message"]]
            sig["score"] = max(0, sig["score"] + corr.get("score_penalty",-10))
        else:
            sig["risk_warnings"] = risk.get("warnings",[])

        # AI 分析
        try:
            from ai_analyst import analyze_signal
            ai = analyze_signal(sig, state.macro_data, state.trump_data)
            sig["ai_analysis"]       = ai
            sig["ai_recommendation"] = ai.get("final_recommendation","等待")
            sig["ai_reason"]         = ai.get("recommendation_reason","")
            adj = ai.get("macro_score_adjustment",0)
            sig["score"] = max(0, min(100, sig["score"] + adj + risk.get("score_adj",0)))
        except Exception as e:
            logger.warning(f"AI skip {symbol}: {e}")
            sig["ai_recommendation"] = "等待"
            sig["score"] = max(0, min(100, sig["score"] + risk.get("score_adj",0)))

        return sig
    except Exception as e:
        logger.error(f"_scan_symbol {symbol}: {e}")
        return None

def check_trump():
    try:
        from ai_analyst import monitor_trump_posts
        trump = monitor_trump_posts()
        state.trump_data = trump
        for post in trump.get("posts",[]):
            if post.get("impact_level") == "high":
                send_message(format_trump_alert(post))
        state.last_trump_check = datetime.now(timezone.utc).strftime("%H:%M UTC")
    except Exception as e:
        logger.error(f"Trump check: {e}")

def morning_briefing():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.last_briefing_date == today: return
    try:
        from ai_analyst import generate_daily_briefing
        b = generate_daily_briefing(state.macro_data, state.trump_data)
        state.daily_briefing     = b
        state.last_briefing_date = today
        send_message(format_briefing_message(b))
    except Exception as e:
        logger.error(f"Briefing: {e}")

def poll_commands():
    try: check_and_process_commands(state)
    except Exception as e: logger.error(f"Poll: {e}")

# ═══════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════
@app.route("/")
def dashboard():
    try: return render_template("dashboard.html")
    except Exception as e:
        import os
        tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),'templates','dashboard.html')
        if os.path.exists(tpl):
            with open(tpl,'r',encoding='utf-8') as f: return f.read(),200,{'Content-Type':'text/html; charset=utf-8'}
        return f"<h1>Starting...</h1><script>setTimeout(()=>location.reload(),10000)</script>",200

@app.route("/api/state")
def api_state():
    try: return jsonify(state.get_snapshot())
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/signals")
def api_signals():
    return jsonify({"signals":state.active_signals,"count":len(state.active_signals)})

@app.route("/api/macro")
def api_macro():
    from data_fetcher import fetch_economic_calendar
    return jsonify({
        "macro":   state.macro_data,
        "session": state.market_session,
        "status":  state.system_status,
        "fred":    state.fred_data,
        "futures": state.us_futures_data,
        "sector":  state.sector_etf_data,
        "eco_calendar": fetch_economic_calendar(),
    })

@app.route("/api/history")
def api_history():
    return jsonify({"history":state.signal_log[-100:],"total":len(state.signal_log),"win_rate":state.calc_win_rate()})

@app.route("/api/stats")
def api_stats():
    wr   = state.calc_win_rate()
    by_sym = {}
    for s in state.signal_log:
        sym = s.get("symbol","")
        if sym not in by_sym: by_sym[sym] = {"total":0,"wins":0,"losses":0}
        by_sym[sym]["total"] += 1
        if s.get("result") in ["tp1","tp2"]: by_sym[sym]["wins"] += 1
        elif s.get("result") == "sl":         by_sym[sym]["losses"] += 1
    return jsonify({"overall":wr,"by_symbol":by_sym,"scan_count":state.scan_count})

@app.route("/api/earnings")
def api_earnings():
    return jsonify({"earnings":state.earnings_calendar})

@app.route("/api/health")
def api_health():
    return jsonify({"status":"running","version":SYSTEM["version"],
                    "scan_count":state.scan_count,"signal_count":state.signal_count})


@app.route("/api/test")
def api_test():
    """測試所有數據源連接狀態"""
    import requests, time as tt
    results = {}
    HEADERS = {"User-Agent":"Mozilla/5.0"}
    
    # 測試 yfinance
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="2d", interval="1d")
        results["yfinance_vix"] = f"✅ VIX={float(h['Close'].iloc[-1]):.1f}" if h is not None and len(h)>0 else "❌ 空數據"
    except Exception as e:
        results["yfinance_vix"] = f"❌ {str(e)[:80]}"
    
    # 測試 Finnhub
    try:
        from config import FINNHUB_API_KEY
        r = requests.get("https://finnhub.io/api/v1/quote",
            params={"symbol":"AAPL","token":FINNHUB_API_KEY},
            headers=HEADERS, timeout=8)
        results["finnhub"] = f"✅ HTTP {r.status_code} AAPL=${r.json().get('c','?')}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e:
        results["finnhub"] = f"❌ {str(e)[:80]}"
    
    # 測試 Alpha Vantage
    try:
        from config import ALPHA_VANTAGE_KEY
        r = requests.get("https://www.alphavantage.co/query",
            params={"function":"GLOBAL_QUOTE","symbol":"AAPL","apikey":ALPHA_VANTAGE_KEY},
            headers=HEADERS, timeout=8)
        d = r.json() if r.status_code==200 else {}
        results["alpha_vantage"] = f"✅ HTTP {r.status_code}" if r.status_code==200 and "Global Quote" in d else f"❌ HTTP {r.status_code} {list(d.keys())}"
    except Exception as e:
        results["alpha_vantage"] = f"❌ {str(e)[:80]}"
    
    # 測試 FRED
    try:
        from config import FRED_API_KEY
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id":"FEDFUNDS","api_key":FRED_API_KEY,"file_type":"json","limit":1},
            headers=HEADERS, timeout=8)
        results["fred"] = f"✅ HTTP {r.status_code}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e:
        results["fred"] = f"❌ {str(e)[:80]}"
    
    # 測試 CoinGecko
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
            params={"ids":"bitcoin","vs_currencies":"usd"},
            headers=HEADERS, timeout=8)
        d = r.json() if r.status_code==200 else {}
        results["coingecko"] = f"✅ BTC=${d.get('bitcoin',{}).get('usd','?'):,}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e:
        results["coingecko"] = f"❌ {str(e)[:80]}"
    
    # 測試 CNN
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=HEADERS, timeout=8)
        d = r.json() if r.status_code==200 else {}
        score = d.get("fear_and_greed",{}).get("score",0)
        results["cnn_fg"] = f"✅ 恐懼貪婪={score:.0f}" if r.status_code==200 and score else f"❌ HTTP {r.status_code}"
    except Exception as e:
        results["cnn_fg"] = f"❌ {str(e)[:80]}"
    
    return jsonify({
        "message": "數據源連接測試",
        "render_environment": True,
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.route("/api/quotes")
def api_quotes():
    """即時報價（從 macro_data + Finnhub）"""
    from data_fetcher import fetch_ohlcv
    from config import SYMBOLS, FINNHUB_API_KEY
    import requests
    
    quotes = {}
    macro  = state.macro_data
    
    # 從已有的 macro 數據取得部分報價
    macro_map = {
        "XAUUSD": ("gold",  "GC=F"),
        "BTCUSD": ("btc",   "BTC-USD"),
        "US500":  ("sp500", "^GSPC"),
    }
    for sym, (mk, _) in macro_map.items():
        d = macro.get(mk)
        if d:
            quotes[sym] = {
                "name":  SYMBOLS.get(sym,{}).get("name", sym),
                "price": d.get("price", 0),
                "chg":   d.get("chg", 0),
                "emoji": SYMBOLS.get(sym,{}).get("emoji","📊"),
                "cat":   SYMBOLS.get(sym,{}).get("cat",""),
            }
    
    # 從 Finnhub 取得外匯報價
    finnhub_map = {
        "EURUSD": "OANDA:EUR_USD",
        "GBPUSD": "OANDA:GBP_USD",
        "USDJPY": "OANDA:USD_JPY",
        "AUDUSD": "OANDA:AUD_USD",
        "USDCAD": "OANDA:USD_CAD",
    }
    if FINNHUB_API_KEY:
        for sym, fhub_sym in finnhub_map.items():
            try:
                r = requests.get("https://finnhub.io/api/v1/forex/candle",
                    params={"symbol":fhub_sym,"resolution":"D","count":2,"token":FINNHUB_API_KEY},
                    timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    if d.get('s') == 'ok' and d.get('c'):
                        price = d['c'][-1]
                        prev  = d['c'][-2] if len(d['c']) > 1 else price
                        chg   = round((price-prev)/prev*100, 3) if prev else 0
                        quotes[sym] = {
                            "name":  SYMBOLS.get(sym,{}).get("name", sym),
                            "price": round(price, 5),
                            "chg":   chg,
                            "emoji": SYMBOLS.get(sym,{}).get("emoji","💱"),
                            "cat":   "外匯",
                        }
            except: pass
    
    # 從 active_signals 補充最新價格
    for sig in state.active_signals:
        sym = sig.get("symbol","")
        if sym not in quotes and sig.get("current_price"):
            quotes[sym] = {
                "name":  sig.get("name", sym),
                "price": sig.get("current_price", 0),
                "chg":   0,
                "emoji": sig.get("emoji","📊"),
                "cat":   sig.get("category",""),
                "has_signal": True,
                "direction":  sig.get("direction",""),
                "score":      sig.get("score", 0),
            }
    
    # 標記有訊號的品種
    for sig in state.active_signals:
        sym = sig.get("symbol","")
        if sym in quotes:
            quotes[sym]["has_signal"] = True
            quotes[sym]["direction"]  = sig.get("direction","")
            quotes[sym]["score"]      = sig.get("score", 0)
    
    return jsonify({"quotes": quotes, "count": len(quotes)})


@app.route("/api/scan_summary")
def api_scan_summary():
    """所有品種技術面掃描總表"""
    from config import SYMBOLS
    
    summary = []
    for sig in state.active_signals:
        summary.append({
            "symbol":    sig.get("symbol",""),
            "name":      sig.get("name",""),
            "emoji":     sig.get("emoji",""),
            "cat":       sig.get("category",""),
            "direction": sig.get("direction","none"),
            "score":     sig.get("score", 0),
            "action":    sig.get("action",""),
            "entry":     sig.get("entry_price", 0),
            "sl":        sig.get("stop_loss", 0),
            "tp1":       sig.get("tp1", 0),
            "rr1":       sig.get("rr1", 0),
            "conds_met": len(sig.get("conditions_met",[])),
            "ai_rec":    sig.get("ai_recommendation",""),
        })
    
    # 加入無訊號品種（用 macro 判斷方向）
    macro = state.macro_data
    vix   = macro.get("vix",{}).get("price", 20)
    dxy   = macro.get("dxy",{}).get("chg", 0)
    
    env_summary = {
        "vix":        vix,
        "dxy_chg":    dxy,
        "fg_score":   macro.get("fear_greed",{}).get("score", 50),
        "env_score":  state.system_status.get("env_score", 70),
        "session":    state.market_session.get("session_zh",""),
        "best_syms":  state.market_session.get("best_symbols",[]),
    }
    
    return jsonify({
        "signals": summary,
        "total":   len(summary),
        "env":     env_summary,
        "last_scan": state.last_scan_time,
    })


@app.route("/api/today")
def api_today():
    """今日表現追蹤"""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    today_signals = [s for s in state.signal_log 
                     if s.get("generated","").startswith(today)]
    
    tp_hits  = [s for s in today_signals if s.get("result") in ["tp1","tp2"]]
    sl_hits  = [s for s in today_signals if s.get("result") == "sl"]
    pending  = [s for s in today_signals if s.get("result") == "pending"]
    
    total_completed = len(tp_hits) + len(sl_hits)
    win_rate = round(len(tp_hits)/total_completed*100, 1) if total_completed > 0 else 0
    
    return jsonify({
        "date":         today,
        "total":        len(today_signals),
        "tp_hits":      len(tp_hits),
        "sl_hits":      len(sl_hits),
        "pending":      len(pending),
        "win_rate":     win_rate,
        "signals":      today_signals,
        "daily_loss":   state.system_status.get("daily_loss",{}),
    })


@app.route("/api/correlation")
def api_correlation():
    """品種相關性矩陣（基於近期 K 線）"""
    from data_fetcher import fetch_ohlcv
    import json
    
    # 主要品種
    syms = ["EURUSD","GBPUSD","USDJPY","XAUUSD","BTCUSD","WTI","US500"]
    prices = {}
    
    for sym in syms:
        try:
            d = fetch_ohlcv(sym, "entry")
            if d and d.get("closes"):
                prices[sym] = d["closes"][-20:]  # 最近20根
        except: pass
    
    # 計算相關性
    def corr(a, b):
        if len(a) != len(b) or len(a) < 5: return 0
        n  = len(a)
        ma = sum(a)/n; mb = sum(b)/n
        num  = sum((a[i]-ma)*(b[i]-mb) for i in range(n))
        da   = (sum((x-ma)**2 for x in a))**0.5
        db   = (sum((x-mb)**2 for x in b))**0.5
        return round(num/(da*db), 2) if da*db > 0 else 0
    
    matrix = {}
    syms_available = list(prices.keys())
    for s1 in syms_available:
        matrix[s1] = {}
        for s2 in syms_available:
            matrix[s1][s2] = corr(prices[s1], prices[s2]) if s1 != s2 else 1.0
    
    return jsonify({"matrix": matrix, "symbols": syms_available})


@app.route("/health")
def health(): return "OK",200

# ═══════════════════════════════════════
# 啟動
# ═══════════════════════════════════════
def start_scheduler():
    try:
        s = BackgroundScheduler(timezone="UTC")
        s.add_job(run_scan,         "interval", minutes=SYSTEM["scan_interval_min"], id="scan",     misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(check_trump,      "interval", minutes=SYSTEM["trump_check_min"],   id="trump",    misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(morning_briefing, "cron",     hour=0, minute=30,                   id="briefing", misfire_grace_time=300)
        s.add_job(poll_commands,    "interval", seconds=10,                           id="commands", misfire_grace_time=15, max_instances=1, coalesce=True)
        s.start()
        logger.info("✅ Scheduler started")

        def _startup():
            time.sleep(5)
            send_message(
                f"🚀 <b>Mitrade AI 訊號系統 v{SYSTEM['version']} 已啟動</b>\n\n"
                f"監控品種：{len(SYMBOLS)} 個\n"
                f"數據源：yfinance / Alpha Vantage / Finnhub / FRED / CoinGecko\n"
                f"掃描頻率：每 {SYSTEM['scan_interval_min']} 分鐘\n\n"
                f"輸入 /help 查看所有指令"
            )
            check_trump()
            morning_briefing()
            run_scan()

        threading.Thread(target=_startup, daemon=True).start()
    except Exception as e:
        logger.error(f"Scheduler error: {e}")

start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SYSTEM["web_port"], debug=False)

# 別名相容
def win_rate_calc(): return state.calc_win_rate()
def daily_loss_tracker(): from risk_manager import check_daily_loss_limit; return check_daily_loss_limit()
