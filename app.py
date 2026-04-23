"""
app.py v4.1 — 主程式
修正：
1. 財報/川普警報 今日只發一次（防重複）
2. 掃描流程完整 debug log
3. 分階段啟動（網頁先顯示）
4. 訊號流程：數據收集 → 分析評估 → 策略模型 → 產生訊號
"""
import logging, threading, time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from config import SYMBOLS, SYSTEM, CB, THRESH, DISCLAIMER, TELEGRAM_CHAT_ID
from telegram_bot import (send_message, format_signal_message, format_alert_message,
                           format_briefing_message, format_trump_alert,
                           check_and_process_commands)

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
        self.macro_data:        Dict = {
            "vix":         None,
            "dxy":         None,
            "sp500":       None,
            "gold":        None,
            "btc":         None,
            "fear_greed":  None,
            "_initialized": False,
        }
        self.trump_data:        Dict = {}
        self.daily_briefing:    Dict = {}
        self.system_status:     Dict = {"env_score": 70, "env_status": "初始化中"}
        self.market_session:    Dict = {"session_zh": "系統啟動中"}
        self.fred_data:         Dict = {}
        self.earnings_calendar: List = []
        self.us_futures_data:   Dict = {}
        self.sector_etf_data:   Dict = {}
        self.signal_log:        List = []
        self.last_scan_time:    Optional[str] = None
        self.last_trump_check:  Optional[str] = None
        self.last_briefing_date:Optional[str] = None
        self.scan_count:        int = 0
        self.signal_count:      int = 0
        self.version            = SYSTEM["version"]
        self._lock              = threading.Lock()
        # 防重複發送：記錄今日已發過的警報
        self.alerts_sent_today: set = set()
        self._alert_date:       str = ""
        self.consec_loss_count: dict = {}  # {symbol: 連續虧損次數}
        self._last_scores:      dict = {}  # {symbol: 最新評分} 供摘要顯示

    def _reset_alerts_if_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._alert_date != today:
            self.alerts_sent_today = set()
            self._alert_date = today

    def should_send_alert(self, alert_key: str) -> bool:
        """檢查此警報今日是否已發過"""
        self._reset_alerts_if_new_day()
        if alert_key in self.alerts_sent_today:
            return False
        self.alerts_sent_today.add(alert_key)
        return True

    def add_signal(self, sig):
        with self._lock:
            now    = datetime.now(timezone.utc)
            sig["expires_at"] = (now + timedelta(hours=CB["signal_expire_h"])).isoformat()
            sig["status"]     = "active"
            symbol, direction = sig.get("symbol"), sig.get("direction")
            # 同品種同方向只保留最新的
            self.active_signals = [s for s in self.active_signals
                if not (s.get("symbol")==symbol and s.get("direction")==direction)]
            self.active_signals.append(sig)
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
                key=lambda x:x.get("score",0), reverse=True)[:20]
            self.signal_count += 1
            from risk_manager import record_signal_loss
            record_signal_loss(sig.get("risk_usd",0))
            # 初始化該品種的連虧計數
            if symbol not in self.consec_loss_count:
                self.consec_loss_count[symbol] = 0

    def expire_old(self):
        with self._lock:
            now   = datetime.now(timezone.utc)
            valid = []
            for s in self.active_signals:
                exp = s.get("expires_at")
                if exp:
                    try:
                        if now < datetime.fromisoformat(exp):
                            valid.append(s)
                        else:
                            s["status"] = "expired"
                            for h in self.signal_log:
                                if h["id"]==s.get("id") and h["result"]=="pending":
                                    h["result"] = "expired"
                            self.expired_signals.append(s)
                    except:
                        valid.append(s)
                else:
                    valid.append(s)
            self.active_signals  = valid
            self.expired_signals = self.expired_signals[-200:]

    def calc_win_rate(self):
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
# 數據更新（各模組獨立容錯）
# ═══════════════════════════════════════
def update_all_data():
    from data_fetcher import (fetch_macro_data, fetch_market_status,
                               fetch_fred_data, fetch_earnings_calendar,
                               fetch_us_futures, fetch_sector_etf,
                               fetch_crypto_sentiment)
    from risk_manager import get_system_status

    try: state.macro_data = fetch_macro_data()
    except Exception as e: logger.warning(f"macro: {e}")

    try: state.market_session = fetch_market_status()
    except Exception as e: logger.warning(f"session: {e}")

    try: state.fred_data = fetch_fred_data()
    except Exception as e: logger.debug(f"fred: {e}")

    try: state.earnings_calendar = fetch_earnings_calendar()
    except Exception as e: logger.warning(f"earnings: {e}")

    try: state.us_futures_data = fetch_us_futures()
    except Exception as e: logger.warning(f"futures: {e}")

    try: state.sector_etf_data = fetch_sector_etf()
    except Exception as e: logger.warning(f"sector: {e}")

    try:
        crypto = fetch_crypto_sentiment()
        state.macro_data["crypto_sentiment"] = crypto
    except Exception as e: logger.warning(f"crypto: {e}")

    # 整合到 macro_data 供後續使用
    state.macro_data["earnings_calendar"] = state.earnings_calendar
    state.macro_data["us_futures"]        = state.us_futures_data
    state.macro_data["sector_etf"]        = state.sector_etf_data
    state.macro_data["fred"]              = state.fred_data

    try: state.system_status = get_system_status(state.macro_data)
    except Exception as e: logger.warning(f"status: {e}")

    logger.info("✅ 數據更新完成")


# ═══════════════════════════════════════
# 訊號產生流程
# 步驟：數據收集 → 分析評估 → 策略模型 → 訊號輸出
# ═══════════════════════════════════════
def _scan_symbol(symbol: str) -> Optional[Dict]:
    # 防連虧：同品種3連虧後暫停20個交易日
    consec = state.consec_loss_count.get(symbol, 0)
    if consec >= 3:
        logger.info(f"  [{symbol}] ⏸️ 防連虧暫停（已連虧{consec}次）")
        return None
    """
    單一品種完整掃描流程：
    Step 1: 數據收集（多時框 K線）
    Step 2: 技術指標計算（EMA/RSI/MACD/ATR/支撐壓力/形態）
    Step 3: 風控評估（VIX/EIA/財報/帳戶門檻）
    Step 4: 多時框共振分析（日線+4H+1H）
    Step 5: 策略評分（≥65才繼續）
    Step 6: AI宏觀驗證（Grok）
    Step 7: 輸出訊號
    """
    try:
        from data_fetcher  import fetch_all_timeframes
        from signal_engine import generate_signal, check_multi_timeframe
        from risk_manager  import run_all_checks, check_correlation_risk

        # Step 1：數據收集
        tf = fetch_all_timeframes(symbol)
        if not tf:
            logger.info(f"  [{symbol}] Step1 ❌ 無K線數據")
            return None
        price = tf.get("entry",{}).get("current_price",0)
        logger.info(f"  [{symbol}] Step1 ✅ K線取得 | 現價={price:.4f}")

        # Step 2+3：技術指標 + 風控評估
        risk = run_all_checks(symbol, tf, state.macro_data, state.active_signals)
        if not risk.get("can_signal"):
            reason = risk.get("blockers",["未知"])[0][:50] if risk.get("blockers") else "風控阻止"
            logger.info(f"  [{symbol}] Step3 ⛔ {reason}")
            return None

        # Step 4+5：多時框共振 + 策略評分
        mtf = check_multi_timeframe(tf)
        direction = mtf.get("direction","none")
        score     = mtf.get("score",0)
        resonance = mtf.get("resonance",False)
        logger.info(f"  [{symbol}] Step4 方向={direction} 分數={score}/100 共振={'✅' if resonance else '❌'}")
        # 記錄分數供Telegram摘要顯示
        state._last_scores[symbol] = score

        # Step 5：門檻判斷
        if score < THRESH["min_score"] or direction == "none":
            conds_met  = len(mtf.get("conditions_met",[]))
            conds_fail = len(mtf.get("conditions_fail",[]))
            logger.info(f"  [{symbol}] Step5 ❌ 分數不足（{score}<{THRESH['min_score']}）通過{conds_met}條/未通過{conds_fail}條")
            return None

        # Step 5通過：產生初步訊號
        sig = generate_signal(symbol, tf, state.macro_data)
        if not sig:
            logger.info(f"  [{symbol}] Step5 ❌ 訊號產生失敗（R:R不足）")
            return None
        logger.info(f"  [{symbol}] Step5 ✅ 初步訊號 | 進場={sig.get('entry_price',0):.4f} SL={sig.get('stop_loss',0):.4f} RR={sig.get('rr1',0)}")

        # 相關性風控
        corr = check_correlation_risk(symbol, direction, state.active_signals)
        if corr.get("correlated"):
            sig["risk_warnings"] = risk.get("warnings",[]) + [corr["message"]]
            sig["score"] = max(0, sig["score"] + corr.get("score_penalty",-10))
        else:
            sig["risk_warnings"] = risk.get("warnings",[])

        # Step 6：AI宏觀驗證
        try:
            from ai_analyst import analyze_signal
            ai  = analyze_signal(sig, state.macro_data, state.trump_data)
            adj = ai.get("macro_score_adjustment",0)
            sig["ai_recommendation"] = ai.get("final_recommendation","等待")
            sig["ai_reason"]         = ai.get("recommendation_reason","")
            sig["score"] = max(0, min(100, sig["score"] + adj + risk.get("score_adj",0)))
            logger.info(f"  [{symbol}] Step6 AI={sig['ai_recommendation']} 調整={adj:+d} 最終分={sig['score']}")
        except Exception as e:
            logger.warning(f"  [{symbol}] Step6 AI跳過: {e}")
            sig["ai_recommendation"] = "等待"
            sig["score"] = max(0, min(100, sig["score"] + risk.get("score_adj",0)))

        # Step 7：輸出
        logger.info(f"  [{symbol}] Step7 ✅ 訊號輸出！分數={sig['score']} 方向={direction}")
        return sig

    except Exception as e:
        logger.error(f"  [{symbol}] 掃描異常: {e}")
        return None


# ═══════════════════════════════════════
# 主掃描函數
# ═══════════════════════════════════════
def run_scan():
    logger.info(f"{'='*40}")
    logger.info(f"=== 掃描 #{state.scan_count+1} 開始 ===")

    # Step 0：更新所有數據
    update_all_data()
    state.expire_old()

    # 環境評估
    env   = state.system_status.get("env_score",100)
    sess  = state.market_session.get("session_zh","—")
    vix   = state.macro_data.get("vix",{}).get("price","—")
    logger.info(f"環境分={env} 時段={sess} VIX={vix}")

    # VIX 極端 → 停止
    if env < 20:
        send_message(format_alert_message("市場極度危險",f"VIX={vix}，今日訊號暫停","danger"))
        state.scan_count += 1
        state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return

    # 財報警告（今日只發一次）
    ec = state.earnings_calendar
    if ec:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_earnings = [e for e in ec if e.get("date","").startswith(today)]
        if today_earnings:
            alert_key = f"earnings_{today}"
            if state.should_send_alert(alert_key):
                syms = ", ".join([e.get("symbol","") for e in today_earnings])
                send_message(format_alert_message(
                    "📅 今日財報提醒",
                    f"以下品種今日發布財報，進場前請確認：{syms}\n⚠️ 財報前後個股波動劇烈，建議縮小手數",
                    "earnings"
                ))
                logger.info(f"財報提醒已發送：{syms}")
            else:
                logger.info(f"財報提醒今日已發過，跳過")

    # 依優先級排序品種
    syms_to_scan = sorted(
        [s for s in SYMBOLS if not SYMBOLS[s].get("monitor_only")],
        key=lambda s: SYMBOLS[s].get("priority",3)
    )
    logger.info(f"掃描品種：{len(syms_to_scan)} 個")

    new_signals = []
    for symbol in syms_to_scan:
        try:
            sig = _scan_symbol(symbol)
            if sig:
                state.add_signal(sig)
                new_signals.append(sig)
                send_message(format_signal_message(sig))
                logger.info(f"✅ 訊號推送：{symbol} {sig['direction']} {sig['score']}/100")
                # 重置該品種連虧計數
                state.consec_loss_count[symbol] = 0
            time.sleep(1)
        except Exception as e:
            logger.error(f"掃描 {symbol} 異常: {e}")

    state.scan_count    += 1
    state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"=== 掃描完成 新訊號:{len(new_signals)} 有效:{len(state.active_signals)} ===")

    # 有新訊號：立即推送（每個單獨推送，format_signal_message 已在迴圈裡推了）
    if len(new_signals) >= 2:
        summary = f"📡 <b>本次掃描 {len(new_signals)} 個新訊號！</b>\n"
        for s in new_signals[:5]:
            d = "🟢" if s.get("direction")=="buy" else "🔴"
            summary += f"{d} {s.get('emoji','')} {s.get('name','')} {s.get('score',0)}/100\n"
        send_message(summary)

    # 每3次掃描發一次掃描摘要（讓你知道系統在跑 + 目前各品種分數）
    if state.scan_count % 3 == 0:
        active  = len(state.active_signals)
        n_syms  = len([s for s in SYMBOLS if not SYMBOLS[s].get("monitor_only")])
        # 從掃描日誌抓最近的分數
        top_scores = sorted(
            [(k,v) for k,v in state._last_scores.items()],
            key=lambda x: x[1], reverse=True
        )[:5] if hasattr(state,'_last_scores') else []
        top_str = ""
        for sym, sc in top_scores:
            bar = "█" * (sc // 10) + "░" * (10 - sc // 10)
            top_str += f"  {sym}: {sc}分 |{bar}|\n"
        msg = (
            f"📊 <b>掃描 #{state.scan_count} 完成</b>\n"
            f"🕐 時段：{sess} | VIX：{vix}\n"
            f"✅ 有效訊號：{active} 個 | 掃描品種：{n_syms} 個\n"
            + (f"\n📈 <b>最高分品種：</b>\n{top_str}" if top_str else "")
            + f"\n{'🎯 有交易訊號！' if active else '⏳ 等待共振 — 耐心持有'}"
        )
        send_message(msg)


# ═══════════════════════════════════════
# 川普監控（防重複）
# ═══════════════════════════════════════
def check_trump():
    try:
        from ai_analyst import monitor_trump_posts
        trump = monitor_trump_posts()
        state.trump_data = trump
        # 把川普事件類型注入 macro_data，讓 signal_engine 可以讀取
        event_type = trump.get("main_event_type","none")
        state.macro_data["trump_event_type"] = event_type
        if event_type not in ["none","other"]:
            logger.info(f"川普事件類型：{event_type}")
        for post in trump.get("posts",[]):
            if post.get("impact_level") == "high":
                # 用發文摘要前30字作唯一key
                post_key = f"trump_{post.get('original_text','')[:30]}"
                if state.should_send_alert(post_key):
                    send_message(format_trump_alert(post))
        state.last_trump_check = datetime.now(timezone.utc).strftime("%H:%M UTC")
        logger.info("Trump check done")
    except Exception as e:
        logger.error(f"Trump check: {e}")


def morning_briefing():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.last_briefing_date == today:
        return
    try:
        from ai_analyst import generate_daily_briefing
        b = generate_daily_briefing(state.macro_data, state.trump_data)
        state.daily_briefing     = b
        state.last_briefing_date = today
        send_message(format_briefing_message(b))
        logger.info("Morning briefing sent")
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
    try:
        return render_template("dashboard.html")
    except Exception:
        tpl = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),'templates','dashboard.html')
        if _os.path.exists(tpl):
            with open(tpl,'r',encoding='utf-8') as f:
                return f.read(), 200, {'Content-Type':'text/html; charset=utf-8'}
        return "<h1>Starting...</h1><script>setTimeout(()=>location.reload(),5000)</script>", 200

@app.route("/api/state")
def api_state():
    try:
        snap = state.get_snapshot()
        # 如果 macro_data 還沒初始化，背景觸發一次更新
        if not state.macro_data.get("_initialized"):
            threading.Thread(target=_quick_macro_update, daemon=True).start()
        return jsonify(snap)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

def _quick_macro_update():
    """快速抓取最重要的宏觀數據（不阻塞）"""
    try:
        from data_fetcher import fetch_macro_data, fetch_market_status
        macro = fetch_macro_data()
        if macro:
            macro["_initialized"] = True
            state.macro_data = macro
        state.market_session = fetch_market_status()
        logger.info("Quick macro update done")
    except Exception as e:
        logger.warning(f"Quick macro update: {e}")

@app.route("/api/signals")
def api_signals():
    return jsonify({"signals":state.active_signals,"count":len(state.active_signals)})

@app.route("/api/macro")
def api_macro():
    from data_fetcher import fetch_economic_calendar
    try: eco = fetch_economic_calendar()
    except: eco = []
    return jsonify({
        "macro":        state.macro_data,
        "session":      state.market_session,
        "status":       state.system_status,
        "fred":         state.fred_data,
        "futures":      state.us_futures_data,
        "sector":       state.sector_etf_data,
        "eco_calendar": eco,
    })

@app.route("/api/history")
def api_history():
    return jsonify({"history":state.signal_log[-100:],"total":len(state.signal_log),"win_rate":state.calc_win_rate()})

@app.route("/api/stats")
def api_stats():
    wr     = state.calc_win_rate()
    by_sym = {}
    for s in state.signal_log:
        sym = s.get("symbol","")
        if sym not in by_sym: by_sym[sym] = {"total":0,"wins":0,"losses":0}
        by_sym[sym]["total"] += 1
        if s.get("result") in ["tp1","tp2"]: by_sym[sym]["wins"]   += 1
        elif s.get("result") == "sl":         by_sym[sym]["losses"] += 1
    return jsonify({"overall":wr,"by_symbol":by_sym,"scan_count":state.scan_count})

@app.route("/api/earnings")
def api_earnings():
    return jsonify({"earnings":state.earnings_calendar})

@app.route("/api/quotes")
def api_quotes():
    from config import SYMBOLS, FINNHUB_API_KEY
    import requests
    quotes = {}
    macro  = state.macro_data
    # 從 macro 取得已有報價
    macro_map = {
        "XAUUSD":("gold","GC=F"),
        "BTCUSD":("btc","BTC-USD"),
        "US500": ("sp500","^GSPC"),
    }
    for sym,(mk,_) in macro_map.items():
        d = macro.get(mk)
        if d:
            quotes[sym] = {"name":SYMBOLS.get(sym,{}).get("name",sym),
                           "price":d.get("price",0),"chg":d.get("chg",0),
                           "emoji":SYMBOLS.get(sym,{}).get("emoji","📊"),
                           "cat":SYMBOLS.get(sym,{}).get("cat","")}
    # Finnhub 外匯報價
    finnhub_map = {"EURUSD":"OANDA:EUR_USD","GBPUSD":"OANDA:GBP_USD",
                   "USDJPY":"OANDA:USD_JPY","AUDUSD":"OANDA:AUD_USD","USDCAD":"OANDA:USD_CAD"}
    if FINNHUB_API_KEY:
        for sym, fsym in finnhub_map.items():
            try:
                import time as _t
                r = requests.get("https://finnhub.io/api/v1/forex/candle",
                    params={"symbol":fsym,"resolution":"D","count":2,"token":FINNHUB_API_KEY},timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    if d.get('s')=='ok' and d.get('c'):
                        p,pv = d['c'][-1], d['c'][-2] if len(d['c'])>1 else d['c'][-1]
                        chg  = round((p-pv)/pv*100,3) if pv else 0
                        quotes[sym] = {"name":SYMBOLS.get(sym,{}).get("name",sym),
                                       "price":round(p,5),"chg":chg,
                                       "emoji":SYMBOLS.get(sym,{}).get("emoji","💱"),"cat":"外匯"}
            except: pass
    # 標記有訊號的品種
    for sig in state.active_signals:
        sym = sig.get("symbol","")
        if sym in quotes:
            quotes[sym]["has_signal"] = True
            quotes[sym]["direction"]  = sig.get("direction","")
            quotes[sym]["score"]      = sig.get("score",0)
        elif sig.get("current_price"):
            si = SYMBOLS.get(sym,{})
            quotes[sym] = {"name":si.get("name",sym),"price":sig.get("current_price",0),
                           "chg":0,"emoji":si.get("emoji","📊"),"cat":si.get("cat",""),
                           "has_signal":True,"direction":sig.get("direction",""),"score":sig.get("score",0)}
    return jsonify({"quotes":quotes,"count":len(quotes)})

@app.route("/api/scan_summary")
def api_scan_summary():
    summary = []
    for sig in state.active_signals:
        summary.append({"symbol":sig.get("symbol",""),"name":sig.get("name",""),
            "emoji":sig.get("emoji",""),"cat":sig.get("category",""),
            "direction":sig.get("direction","none"),"score":sig.get("score",0),
            "action":sig.get("action",""),"entry":sig.get("entry_price",0),
            "sl":sig.get("stop_loss",0),"tp1":sig.get("tp1",0),
            "rr1":sig.get("rr1",0),"conds_met":len(sig.get("conditions_met",[])),
            "ai_rec":sig.get("ai_recommendation","")})
    macro = state.macro_data
    return jsonify({"signals":summary,"total":len(summary),
        "env":{"vix":macro.get("vix",{}).get("price",0),
               "dxy_chg":macro.get("dxy",{}).get("chg",0),
               "fg_score":macro.get("fear_greed",{}).get("score",50),
               "env_score":state.system_status.get("env_score",70),
               "session":state.market_session.get("session_zh",""),
               "best_syms":state.market_session.get("best_symbols",[])},
        "last_scan":state.last_scan_time})

@app.route("/api/today")
def api_today():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_sigs = [s for s in state.signal_log if s.get("generated","").startswith(today)]
    tp  = [s for s in today_sigs if s.get("result") in ["tp1","tp2"]]
    sl  = [s for s in today_sigs if s.get("result") == "sl"]
    pen = [s for s in today_sigs if s.get("result") == "pending"]
    tot = len(tp)+len(sl)
    return jsonify({"date":today,"total":len(today_sigs),"tp_hits":len(tp),
        "sl_hits":len(sl),"pending":len(pen),
        "win_rate":round(len(tp)/tot*100,1) if tot else 0,
        "daily_loss":state.system_status.get("daily_loss",{})})

@app.route("/api/correlation")
def api_correlation():
    from data_fetcher import fetch_ohlcv
    syms   = ["EURUSD","GBPUSD","USDJPY","XAUUSD","BTCUSD","WTI","US500"]
    prices = {}
    for sym in syms:
        try:
            d = fetch_ohlcv(sym,"entry")
            if d and d.get("closes"): prices[sym] = d["closes"][-20:]
        except: pass
    def corr(a,b):
        n=len(a); ma=sum(a)/n; mb=sum(b)/n
        num=sum((a[i]-ma)*(b[i]-mb) for i in range(n))
        da=(sum((x-ma)**2 for x in a))**.5; db=(sum((x-mb)**2 for x in b))**.5
        return round(num/(da*db),2) if da*db>0 else 0
    sl  = list(prices.keys())
    mat = {s1:{s2:(corr(prices[s1],prices[s2]) if s1!=s2 else 1.0) for s2 in sl} for s1 in sl}
    return jsonify({"matrix":mat,"symbols":sl})

@app.route("/api/test")
def api_test():
    import requests as req2
    results = {}
    HEADERS = {"User-Agent":"Mozilla/5.0"}
    from config import FINNHUB_API_KEY, ALPHA_VANTAGE_KEY, FRED_API_KEY
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="2d",interval="1d")
        results["yfinance_vix"] = f"✅ VIX={float(h['Close'].iloc[-1]):.1f}" if h is not None and len(h)>0 else "❌ 空數據"
    except Exception as e: results["yfinance_vix"] = f"❌ {str(e)[:60]}"
    try:
        r = req2.get("https://finnhub.io/api/v1/quote",params={"symbol":"AAPL","token":FINNHUB_API_KEY},headers=HEADERS,timeout=8)
        results["finnhub"] = f"✅ HTTP {r.status_code} AAPL=${r.json().get('c','?')}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e: results["finnhub"] = f"❌ {str(e)[:60]}"
    try:
        r = req2.get("https://www.alphavantage.co/query",params={"function":"GLOBAL_QUOTE","symbol":"AAPL","apikey":ALPHA_VANTAGE_KEY},headers=HEADERS,timeout=8)
        results["alpha_vantage"] = f"✅ HTTP {r.status_code}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e: results["alpha_vantage"] = f"❌ {str(e)[:60]}"
    try:
        r = req2.get("https://api.stlouisfed.org/fred/series/observations",params={"series_id":"FEDFUNDS","api_key":FRED_API_KEY,"file_type":"json","limit":1},headers=HEADERS,timeout=5)
        results["fred"] = f"✅ HTTP {r.status_code}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e: results["fred"] = f"❌ {str(e)[:60]}"
    try:
        r = req2.get("https://api.alternative.me/fng/?limit=1",headers=HEADERS,timeout=8)
        d = r.json() if r.status_code==200 else {}
        score = d.get("data",[{}])[0].get("value","?") if d.get("data") else "?"
        results["cnn_fg"] = f"✅ F&G={score}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e: results["cnn_fg"] = f"❌ {str(e)[:60]}"
    try:
        r = req2.get("https://api.coingecko.com/api/v3/simple/price",params={"ids":"bitcoin","vs_currencies":"usd"},headers=HEADERS,timeout=8)
        d = r.json() if r.status_code==200 else {}
        results["coingecko"] = f"✅ BTC=${d.get('bitcoin',{}).get('usd','?'):,}" if r.status_code==200 else f"❌ HTTP {r.status_code}"
    except Exception as e: results["coingecko"] = f"❌ {str(e)[:60]}"
    return jsonify({"message":"數據源連接測試","render_environment":True,
                    "results":results,"timestamp":datetime.now(timezone.utc).isoformat()})

@app.route("/health")
def health(): return "OK", 200


# ═══════════════════════════════════════
# 排程器啟動
# ═══════════════════════════════════════
def start_scheduler():
    try:
        s = BackgroundScheduler(timezone="UTC")
        s.add_job(run_scan,         "interval", minutes=SYSTEM["scan_interval_min"], id="scan",
                  misfire_grace_time=60,  max_instances=1, coalesce=True)
        s.add_job(check_trump,      "interval", minutes=SYSTEM["trump_check_min"],   id="trump",
                  misfire_grace_time=60,  max_instances=1, coalesce=True)
        s.add_job(morning_briefing, "cron",     hour=0, minute=30, id="briefing",
                  misfire_grace_time=300, max_instances=1)
        s.add_job(poll_commands,    "interval", seconds=10, id="commands",
                  misfire_grace_time=15,  max_instances=1, coalesce=True)
        s.start()
        logger.info("✅ 排程器啟動")

        def _startup():
            # 階段一：立刻抓宏觀數據（讓網頁有內容）
            time.sleep(3)
            try:
                from data_fetcher import fetch_macro_data, fetch_market_status
                state.macro_data     = fetch_macro_data()
                state.market_session = fetch_market_status()
                logger.info(f"✅ 初始宏觀數據載入 VIX={state.macro_data.get('vix',{}).get('price','?')}")
            except Exception as e:
                logger.error(f"初始宏觀: {e}")

            # 階段二：10秒後發啟動通知
            time.sleep(10)
            send_message(
                f"🚀 <b>Mitrade AI 訊號系統 v{SYSTEM['version']} 已啟動</b>\n\n"
                f"監控品種：{len(SYMBOLS)} 個\n"
                f"數據源：yfinance / Finnhub / CoinGecko / FRED\n"
                f"掃描頻率：每 {SYSTEM['scan_interval_min']} 分鐘\n"
                f"財報/警報：今日只發一次\n\n"
                f"輸入 /help 查看所有指令"
            )

            # 階段三：30秒後川普監控
            time.sleep(20)
            try: check_trump()
            except Exception as e: logger.error(f"初始川普: {e}")

            # 階段四：60秒後第一次完整掃描
            time.sleep(30)
            try:
                morning_briefing()
                run_scan()
            except Exception as e:
                logger.error(f"初始掃描: {e}")

        threading.Thread(target=_startup, daemon=True).start()

    except Exception as e:
        logger.error(f"排程器錯誤: {e}")


start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SYSTEM["web_port"], debug=False)
