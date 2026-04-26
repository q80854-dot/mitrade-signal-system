"""
app.py v5.2 — 完整最終修正版
修復清單：
  [嚴重] composite_score 在定義前被引用 → NameError 導致所有掃描崩潰
  Bug 1: AI 調整後分數仍低於門檻卻進入訊號列表
  Bug 2: expire_old() 過期訊號 DB result 未更新
  Bug 3: 冷啟動保護（付費版 Render，無需 keep-alive）
  Bug 4: trump_data 失敗時無回退值
  Bug 5: daily_briefing AI 失敗時無降級邏輯
  Bug 6: 週末不掃加密貨幣
  Bug 7: expire_old() 後 signal_log 未重新載入
"""
import logging, threading, time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from flask import Flask, render_template, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from config import SYMBOLS, SYSTEM, CB, THRESH, DISCLAIMER, TELEGRAM_CHAT_ID
from state_store import store
from watchdog import watchdog, safe_send, checker
from scoring_engine import (calc_composite_score, kelly_position_size,
                              calc_performance_metrics, detect_regime,
                              check_portfolio_correlation, apply_slippage)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

import os as _os
_tpl = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "templates")
app  = Flask(__name__, template_folder=_tpl)

# ═══════════════════════════════════════
# 品種分類
# ═══════════════════════════════════════
CRYPTO_CATS    = {"加密"}
CRYPTO_SYMBOLS = {"BTCUSD","ETHUSD","XRPUSD","SOLUSD","BNBUSD","LTCUSD"}
STOCK_CATS     = {"美股"}
STOCK_SYMBOLS  = {"NVDA","MSFT","AAPL","AMZN","GOOGL","META","TSLA","AMD","INTC","NFLX"}

# ═══════════════════════════════════════
# SystemState
# ═══════════════════════════════════════
class SystemState:
    def __init__(self):
        self.active_signals:     List[Dict] = []
        self.expired_signals:    List[Dict] = []
        self.macro_data:         Dict = {"_initialized": False}
        self.trump_data:         Dict = {}
        self.daily_briefing:     Dict = {}
        self.system_status:      Dict = {"env_score": 70, "env_status": "初始化中"}
        self.market_session:     Dict = {"session_zh": "系統啟動中"}
        self.fred_data:          Dict = {}
        self.earnings_calendar:  List = []
        self.us_futures_data:    Dict = {}
        self.sector_etf_data:    Dict = {}
        self.last_scan_time:     Optional[str] = None
        self.last_trump_check:   Optional[str] = None
        self.last_briefing_date: Optional[str] = None
        self.scan_count:         int = 0
        self.signal_count:       int = 0
        self.version             = "5.2.0"
        self._lock               = threading.Lock()
        self._last_scores:       dict = {}
        self.regime:             Dict = {}
        self.signal_log = store.load_signal_log(500)
        logger.info(f"✅ 從 SQLite 還原 {len(self.signal_log)} 筆歷史訊號")

    def should_send_alert(self, key):
        return not store.alert_sent_today(key)

    def add_signal(self, sig):
        with self._lock:
            now = datetime.now(timezone.utc)
            sig["expires_at"] = (now + timedelta(hours=CB["signal_expire_h"])).isoformat()
            sig["status"]     = "active"
            symbol    = sig.get("symbol")
            direction = sig.get("direction")
            slip = apply_slippage(symbol, sig.get("entry_price", 0), direction)
            sig["fill_price"]    = slip["fill_price"]
            sig["slippage_pct"]  = slip["slippage_pct"]
            stats = store.get_trade_stats()
            wr    = stats.get("win_rate", 50) / 100
            rr    = sig.get("rr1", 1.5)
            kelly = kelly_position_size(wr, rr, CB.get("risk_per_trade_pct", 2) / 100)
            sig["kelly_risk_pct"] = kelly["kelly_pct"]
            sig["kelly_edge"]     = kelly.get("edge", 0)
            self.active_signals = [s for s in self.active_signals
                                   if not (s.get("symbol") == symbol and s.get("direction") == direction)]
            self.active_signals.append(sig)
            self.active_signals = sorted(self.active_signals,
                                         key=lambda x: x.get("score", 0), reverse=True)[:20]
            store.save_signal(sig)
            self.signal_log = store.load_signal_log(500)
            self.signal_count += 1
            store.append_equity(float(_os.getenv("ACCOUNT_BALANCE_USD", "3000")))
            from risk_manager import record_signal_loss
            record_signal_loss(sig.get("risk_usd", 0))

    def expire_old(self):
        # ★ Bug 2+7 修復
        with self._lock:
            now = datetime.now(timezone.utc)
            valid = []
            for s in self.active_signals:
                exp = s.get("expires_at")
                try:
                    if exp and now < datetime.fromisoformat(exp):
                        valid.append(s)
                    else:
                        s["status"] = "expired"
                        s["result"] = "expired"
                        store.update_signal_result(s.get("id", ""), "expired", s.get("pnl", 0))
                        self.expired_signals.append(s)
                        logger.info(f"訊號過期：{s.get('symbol','')} score={s.get('score',0):.1f}")
                except:
                    valid.append(s)
            self.active_signals  = valid
            self.expired_signals = self.expired_signals[-200:]
            self.signal_log      = store.load_signal_log(500)  # ★ Bug 7

    def calc_win_rate(self):
        stats = store.get_trade_stats()
        return {
            "total":    stats.get("total", 0),
            "wins":     stats.get("tp", 0),
            "losses":   stats.get("sl", 0),
            "win_rate": stats.get("win_rate", 0),
        }

    def get_snapshot(self):
        with self._lock:
            wr     = self.calc_win_rate()
            equity = store.get_equity_curve()
            trades = store.get_trade_list()
            perf   = calc_performance_metrics(equity, trades) if len(equity) >= 2 else {}
            return {
                "active_signals":   list(self.active_signals),
                "macro_data":       dict(self.macro_data),
                "trump_data":       dict(self.trump_data),
                "daily_briefing":   dict(self.daily_briefing),
                "system_status":    dict(self.system_status),
                "market_session":   dict(self.market_session),
                "fred_data":        dict(self.fred_data),
                "earnings_calendar":list(self.earnings_calendar),
                "us_futures_data":  dict(self.us_futures_data),
                "sector_etf_data":  dict(self.sector_etf_data),
                "signal_log":       list(self.signal_log[-100:]),
                "win_rate":         wr,
                "performance":      perf,
                "regime":           dict(self.regime),
                "last_scan_time":   self.last_scan_time,
                "scan_count":       self.scan_count,
                "signal_count":     self.signal_count,
                "version":          self.version,
                "disclaimer":       DISCLAIMER,
            }

state = SystemState()

# ═══════════════════════════════════════
# 數據更新
# ═══════════════════════════════════════
def update_all_data():
    from data_fetcher import (fetch_macro_data, fetch_market_status, fetch_fred_data,
                               fetch_earnings_calendar, fetch_us_futures,
                               fetch_sector_etf, fetch_crypto_sentiment)
    from risk_manager import get_system_status

    try:
        macro = fetch_macro_data()
        q = checker.validate_macro(macro)
        if not q["valid"]: logger.warning(f"宏觀數據品質問題: {q['issues']}")
        state.macro_data = macro
    except Exception as e: logger.warning(f"macro: {e}")

    try: state.market_session     = fetch_market_status()
    except Exception as e: logger.warning(f"session: {e}")
    try: state.fred_data          = fetch_fred_data()
    except Exception as e: logger.debug(f"fred: {e}")
    try: state.earnings_calendar  = fetch_earnings_calendar()
    except Exception as e: logger.warning(f"earnings: {e}")
    try: state.us_futures_data    = fetch_us_futures()
    except Exception as e: logger.warning(f"futures: {e}")
    try: state.sector_etf_data    = fetch_sector_etf()
    except Exception as e: logger.warning(f"sector: {e}")
    try: state.macro_data["crypto_sentiment"] = fetch_crypto_sentiment()
    except Exception as e: logger.warning(f"crypto: {e}")

    state.macro_data["earnings_calendar"] = state.earnings_calendar
    state.macro_data["us_futures"]        = state.us_futures_data
    state.macro_data["sector_etf"]        = state.sector_etf_data
    state.macro_data["fred"]              = state.fred_data

    try:
        state.system_status = get_system_status(state.macro_data)
    except Exception as e: logger.warning(f"status: {e}")

    try:
        state.regime = detect_regime(state.macro_data)
        state.macro_data["regime"] = state.regime
        logger.info(f"市場機制：{state.regime.get('regime_zh','—')}")
    except Exception as e: logger.warning(f"regime: {e}")

    # ★ Bug 5 修復：統一 env_status 文字
    is_weekend = state.market_session.get("session") == "weekend"
    if is_weekend:
        state.system_status["env_status"] = "週末・加密監控中"
    elif state.scan_count == 0:
        state.system_status["env_status"] = "初始化中"
    else:
        env_score = state.system_status.get("env_score", 50)
        if env_score >= 70:   state.system_status["env_status"] = "適合交易"
        elif env_score >= 40: state.system_status["env_status"] = "環境中性"
        else:                 state.system_status["env_status"] = "環境偏差"

    logger.info("✅ 數據更新完成")

# ═══════════════════════════════════════
# 掃描
# ═══════════════════════════════════════
def _scan_symbol(symbol):
    consec = store.get_consec_loss(symbol)
    if consec >= 3:
        logger.info(f"  [{symbol}] ⏸️ 防連虧暫停（已連虧{consec}次）")
        return None

    regime  = state.regime
    allowed = regime.get("allowed_directions", ["buy", "sell"])
    if not allowed:
        logger.info(f"  [{symbol}] 市場機制停止交易")
        return None

    try:
        from data_fetcher          import fetch_all_timeframes
        from signal_engine         import generate_signal, check_multi_timeframe, _get_cat_params
        from risk_manager          import run_all_checks
        from indicators            import calc_all_indicators
        from adaptive_weight_engine import auto_composite_score, record_weight_history

        tf = fetch_all_timeframes(symbol)
        if not tf: return None

        for tf_key, tf_data in tf.items():
            q = checker.validate_ohlcv(tf_data, f"{symbol}_{tf_key}")
            if q["valid"] and q["data"].get("data_quality", {}).get("cleaned"):
                tf[tf_key] = q["data"]

        price = tf.get("entry", {}).get("current_price", 0)
        logger.info(f"  [{symbol}] Step1 ✅ 現價={price:.4f}")

        risk = run_all_checks(symbol, tf, state.macro_data, state.active_signals)
        if not risk.get("can_signal"):
            reason = (risk.get("blockers", ["未知"])[0][:50] if risk.get("blockers") else "風控阻止")
            logger.info(f"  [{symbol}] Step3 ⛔ {reason}")
            return None

        mtf       = check_multi_timeframe(tf)
        direction = mtf.get("direction", "none")
        score     = mtf.get("score", 0)

        if direction not in allowed:
            logger.info(f"  [{symbol}] Regime 過濾：{direction} 不在允許方向")
            return None

        state._last_scores[symbol] = score

        # ★ 使用品種專屬門檻（加密 55、黃金 60 等）
        cp_min = _get_cat_params(symbol).get("min_score", THRESH["min_score"])
        if score < cp_min or direction == "none":
            logger.info(f"  [{symbol}] Step5 ❌ MTF分數不足（{score}<{cp_min}）")
            return None

        entry_data = tf.get("entry", {})
        indicators = calc_all_indicators(entry_data)

        # ★ 先計算 auto/composite_score，再使用
        auto = auto_composite_score(
            macro_data=state.macro_data,
            indicators=indicators,
            trump_data=state.trump_data,
            symbol=symbol,
            category=SYMBOLS.get(symbol, {}).get("cat", "")
        )
        record_weight_history(auto)
        composite_score = auto["composite_100"]  # ★ 定義在此

        logger.info(f"  [{symbol}] 自適應評分={composite_score:.1f} 主導={auto['dominant_state']}")

        # 再次用品種專屬門檻過濾 composite_score
        if composite_score < cp_min:
            logger.info(f"  [{symbol}] ❌ 自適應分數不足（{composite_score:.1f}<{cp_min}）")
            return None

        sig = generate_signal(symbol, tf, state.macro_data)
        if not sig: return None

        sig["score"]         = composite_score
        sig["dominant_state"] = auto["dominant_state"]

        corr = check_portfolio_correlation(symbol, direction, state.active_signals)
        if corr["correlated"]:
            sig["risk_warnings"] = [corr["message"]]
            sig["score"]         = max(0, sig["score"] + corr["score_penalty"])
        else:
            sig["risk_warnings"] = risk.get("warnings", [])

        size_mult = regime.get("size_multiplier", 1.0)
        if size_mult != 1.0:
            sig["suggested_lot"]      = round(sig.get("suggested_lot", 0.01) * size_mult, 2)
            sig["regime_adjustment"]  = f"機制調整倉位 x{size_mult}"

        try:
            from ai_analyst import analyze_signal
            ai  = analyze_signal(sig, state.macro_data, state.trump_data)
            adj = ai.get("macro_score_adjustment", 0)
            sig["ai_recommendation"] = ai.get("final_recommendation", "等待")
            sig["ai_reason"]         = ai.get("recommendation_reason", "")
            sig["score"] = max(0, min(100, sig["score"] + adj + risk.get("score_adj", 0)))
        except Exception as e:
            logger.warning(f"  [{symbol}] AI跳過: {e}")
            sig["ai_recommendation"] = "等待"

        # ★ Bug 1 修復：AI 調整後再次確認門檻
        if sig["score"] < THRESH["min_score"]:
            logger.info(f"  [{symbol}] ❌ AI調整後分數不足（{sig['score']:.1f}<{THRESH['min_score']}），過濾")
            return None

        logger.info(f"  [{symbol}] ✅ 訊號輸出 分數={sig['score']:.1f} 方向={direction}")
        return sig

    except Exception as e:
        logger.error(f"  [{symbol}] 掃描異常: {e}")
        watchdog.record_failure(str(e))
        return None


def run_scan():
    logger.info(f"{'='*40}\n=== 掃描 #{state.scan_count+1} 開始 ===")
    try:
        update_all_data()
        state.expire_old()

        env      = state.system_status.get("env_score", 100)
        sess     = state.market_session.get("session_zh", "—")
        vix      = state.macro_data.get("vix", {}).get("price", "—")
        regime_zh = state.regime.get("regime_zh", "正常")
        logger.info(f"環境分={env} 時段={sess} VIX={vix} 機制={regime_zh}")

        if env < 20:
            safe_send(f"🚨 VIX={vix}，今日訊號暫停", priority=1)
            return

        # 財報提醒
        ec    = state.earnings_calendar
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if ec:
            te = [e for e in ec if e.get("date", "").startswith(today)]
            if te and state.should_send_alert(f"earnings_{today}"):
                from telegram_bot import format_alert_message
                safe_send(format_alert_message(
                    "📅 今日財報提醒",
                    f"以下品種今日發布財報：{', '.join([e.get('symbol','') for e in te])}\n⚠️ 財報前後波動劇烈",
                    "earnings"), priority=3)

        # ★ Bug 6 修復：週末只掃加密，平日排除美股
        is_weekend = state.market_session.get("session") == "weekend"
        if is_weekend:
            syms_to_scan = sorted(
                [s for s in SYMBOLS
                 if not SYMBOLS[s].get("monitor_only")
                 and (SYMBOLS[s].get("cat") in CRYPTO_CATS or s in CRYPTO_SYMBOLS)],
                key=lambda s: SYMBOLS[s].get("priority", 3)
            )
            logger.info(f"🌙 週末模式：只掃加密，共 {len(syms_to_scan)} 個：{syms_to_scan}")
            if not syms_to_scan:
                logger.info("週末無加密品種，跳過")
                state.scan_count += 1
                state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                watchdog.ping()
                return
        else:
            syms_to_scan = sorted(
                [s for s in SYMBOLS
                 if not SYMBOLS[s].get("monitor_only")
                 and SYMBOLS[s].get("cat") not in STOCK_CATS
                 and s not in STOCK_SYMBOLS],
                key=lambda s: SYMBOLS[s].get("priority", 3)
            )
            logger.info(f"📊 平日模式（無美股）：共 {len(syms_to_scan)} 個品種：{syms_to_scan}")

        new_signals = []
        for symbol in syms_to_scan:
            try:
                sig = _scan_symbol(symbol)
                if sig:
                    state.add_signal(sig)
                    new_signals.append(sig)
                    from telegram_bot import format_signal_message
                    safe_send(format_signal_message(sig), priority=2)
                    store.set_consec_loss(symbol, 0)
                time.sleep(1)
            except Exception as e:
                logger.error(f"掃描 {symbol} 異常: {e}")

        state.scan_count    += 1
        state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        watchdog.ping()
        logger.info(f"=== 掃描完成 新訊號:{len(new_signals)} 有效:{len(state.active_signals)} ===")

        if state.scan_count % 3 == 0:
            from telegram_bot import format_scan_summary
            wr = state.calc_win_rate()
            safe_send(format_scan_summary(
                scan_num=state.scan_count, new_signals=new_signals,
                active_count=len(state.active_signals), scores=state._last_scores,
                session=sess, vix=vix, regime_zh=regime_zh,
                win_rate=wr.get("win_rate", 0)), priority=8)

    except Exception as e:
        logger.error(f"run_scan 異常: {e}")
        watchdog.record_failure(str(e))


def check_trump():
    # ★ Bug 4 修復
    try:
        from ai_analyst import monitor_trump_posts
        trump = monitor_trump_posts()
        if trump:
            state.trump_data = trump
            state.macro_data["trump_event_type"] = trump.get("main_event_type", "none")
            state.macro_data["trump_data"]       = trump
            for post in trump.get("posts", []):
                if post.get("impact_level") == "high":
                    key = f"trump_{post.get('original_text','')[:30]}"
                    if state.should_send_alert(key):
                        from telegram_bot import format_trump_alert
                        safe_send(format_trump_alert(post), priority=1)
        else:
            logger.warning("check_trump: 回傳空資料")
        state.last_trump_check = datetime.now(timezone.utc).strftime("%H:%M UTC")
    except Exception as e:
        logger.error(f"Trump check: {e}")
        if not state.trump_data:
            state.trump_data = {
                "has_impact_posts": False, "posts": [],
                "overall_market_mood": "neutral", "main_event_type": "none",
                "error": str(e)
            }


def morning_briefing():
    # ★ Bug 5 修復
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.last_briefing_date == today: return
    try:
        from ai_analyst import generate_daily_briefing
        b = generate_daily_briefing(state.macro_data, state.trump_data)
        state.daily_briefing     = b
        state.last_briefing_date = today
        from telegram_bot import format_briefing_message
        safe_send(format_briefing_message(b), priority=4)
    except Exception as e:
        logger.error(f"Briefing AI 失敗，使用降級版: {e}")
        macro      = state.macro_data
        vix        = macro.get("vix", {}).get("price", 0)
        fg         = macro.get("fear_greed", {}).get("score", 50)
        regime_zh  = state.regime.get("regime_zh", "正常市場")
        env_score  = state.system_status.get("env_score", 70)
        is_weekend = state.market_session.get("session") == "weekend"
        state.daily_briefing = {
            "ai_available": False,
            "overall_environment": ("適合交易" if env_score >= 70
                                    else "今日觀望" if env_score < 40
                                    else "謹慎操作"),
            "environment_reason":  f"VIX {vix:.1f}，恐懼貪婪 {fg:.0f}，{regime_zh}",
            "best_opportunities":  (["BTCUSD（加密 24/7）","ETHUSD"] if is_weekend
                                    else ["BTCUSD","XAUUSD","EURUSD"] if vix < 25
                                    else []),
            "avoid_today":         [f"高波動品種（VIX={vix:.1f}）"] if vix >= 30 else [],
            "generated_at":        today,
        }
        state.last_briefing_date = today


def poll_commands():
    try:
        from telegram_bot import check_and_process_commands
        check_and_process_commands(state)
    except Exception as e:
        logger.error(f"Poll: {e}")

# ═══════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════
@app.route("/")
def dashboard():
    try:
        return render_template("dashboard.html")
    except:
        tpl = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "templates", "dashboard.html")
        if _os.path.exists(tpl):
            with open(tpl, "r", encoding="utf-8") as f:
                return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
        return "<h1>Starting...</h1><script>setTimeout(()=>location.reload(),5000)</script>", 200


@app.route("/api/state")
def api_state():
    try:
        snap = state.get_snapshot()
        if not state.macro_data.get("_initialized"):
            threading.Thread(target=_quick_macro_update, daemon=True).start()

        # ★ Bug 3 修復（付費版）：超過 10 分鐘未掃描才補觸發
        import time as _t
        if not hasattr(api_state, "_start_time"):
            api_state._start_time = _t.time()
        uptime = _t.time() - api_state._start_time
        if state.scan_count == 0 and uptime > 600:
            logger.warning("⚠️ 啟動超過 10 分鐘仍未掃描，補觸發一次")
            threading.Thread(target=run_scan, daemon=True).start()
            api_state._start_time = _t.time()

        return jsonify(snap)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _quick_macro_update():
    try:
        from data_fetcher import fetch_macro_data, fetch_market_status
        macro = fetch_macro_data()
        if macro:
            macro["_initialized"] = True
            state.macro_data = macro
        state.market_session = fetch_market_status()
    except Exception as e:
        logger.warning(f"Quick macro: {e}")


@app.route("/api/signals")
def api_signals():
    return jsonify({"signals": state.active_signals, "count": len(state.active_signals)})


@app.route("/api/macro")
def api_macro():
    from data_fetcher import fetch_economic_calendar
    try: eco = fetch_economic_calendar()
    except: eco = []
    return jsonify({
        "macro": state.macro_data, "session": state.market_session,
        "status": state.system_status, "fred": state.fred_data,
        "futures": state.us_futures_data, "sector": state.sector_etf_data,
        "eco_calendar": eco, "regime": state.regime
    })


@app.route("/api/history")
def api_history():
    log = store.load_signal_log(100)
    return jsonify({"history": log, "total": len(log), "win_rate": state.calc_win_rate()})


@app.route("/api/performance")
def api_performance():
    equity = store.get_equity_curve()
    trades = store.get_trade_list()
    perf   = calc_performance_metrics(equity, trades) if len(equity) >= 2 else {"valid": False}
    stats  = store.get_trade_stats()
    wr     = stats.get("win_rate", 50) / 100
    kelly  = kelly_position_size(wr, 1.6, 3000)
    return jsonify({"performance": perf, "stats": stats, "kelly": kelly,
                    "equity_curve": equity[-100:], "n_trades": len(trades)})


@app.route("/api/regime")
def api_regime():
    return jsonify({"regime": state.regime, "macro_summary": {
        "vix":       state.macro_data.get("vix", {}).get("price", 0),
        "fg":        state.macro_data.get("fear_greed", {}).get("score", 50),
        "sp500_chg": state.macro_data.get("sp500", {}).get("chg", 0),
    }})


@app.route("/api/backtest/<symbol>")
def api_backtest_symbol(symbol):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": f"品種 {symbol} 不存在"}), 400
    min_score = float(request.args.get("min_score", 65))
    try:
        from backtester import backtest_symbol
        return jsonify(backtest_symbol(symbol, min_score=min_score))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest/walkforward/<symbol>")
def api_walkforward(symbol):
    try:
        from backtester import walk_forward_backtest
        return jsonify(walk_forward_backtest(symbol.upper()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest/all")
def api_backtest_all():
    def _run():
        from backtester import run_full_backtest
        run_full_backtest()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "批量回測已開始"})


@app.route("/api/backtest/results")
def api_backtest_results():
    symbol = request.args.get("symbol")
    return jsonify({"results": store.load_backtest(symbol, limit=20)})


@app.route("/api/scoring/<symbol>")
def api_scoring(symbol):
    symbol = symbol.upper()
    try:
        from data_fetcher  import fetch_all_timeframes
        from indicators    import calc_all_indicators
        from signal_engine import check_multi_timeframe
        tf = fetch_all_timeframes(symbol)
        if not tf: return jsonify({"error": "無數據"}), 404
        mtf = check_multi_timeframe(tf)
        ind = calc_all_indicators(tf.get("entry", {}))
        sc  = calc_composite_score(ind, mtf.get("direction","buy"),
                                   mtf.get("bull_timeframes",[]), mtf.get("bear_timeframes",[]))
        return jsonify({"symbol": symbol, "scoring": sc, "mtf": mtf, "adx": ind.get("adx_value",0)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/weights")
def api_weights():
    from adaptive_weight_engine import calc_adaptive_weights, get_weight_history
    result = calc_adaptive_weights(macro_data=state.macro_data)
    return jsonify({"current": result, "history": get_weight_history()[-20:], "regime": state.regime})


@app.route("/api/stats")
def api_stats():
    return jsonify({"overall": store.get_trade_stats(), "scan_count": state.scan_count})


@app.route("/api/earnings")
def api_earnings():
    return jsonify({"earnings": state.earnings_calendar})


@app.route("/api/quotes")
def api_quotes():
    quotes = {}
    macro  = state.macro_data
    for sym, mk in {"XAUUSD":"gold","BTCUSD":"btc","US500":"sp500"}.items():
        d = macro.get(mk)
        if d:
            si = SYMBOLS.get(sym, {})
            quotes[sym] = {"name": si.get("name",sym), "price": d.get("price",0),
                           "chg": d.get("chg",0), "emoji": si.get("emoji","📊"),
                           "cat": si.get("cat","")}
    for sig in state.active_signals:
        sym = sig.get("symbol","")
        if sym in quotes:
            quotes[sym]["has_signal"] = True
            quotes[sym]["direction"]  = sig.get("direction","")
            quotes[sym]["score"]      = sig.get("score",0)
    return jsonify({"quotes": quotes, "count": len(quotes)})


@app.route("/api/scan_summary")
def api_scan_summary():
    sigs = [{"symbol":s.get("symbol",""),"name":s.get("name",""),"emoji":s.get("emoji",""),
             "direction":s.get("direction","none"),"score":s.get("score",0),"action":s.get("action",""),
             "entry":s.get("entry_price",0),"sl":s.get("stop_loss",0),"tp1":s.get("tp1",0),
             "rr1":s.get("rr1",0),"ai_rec":s.get("ai_recommendation",""),
             "kelly_pct":s.get("kelly_risk_pct",0)} for s in state.active_signals]
    macro = state.macro_data
    return jsonify({"signals": sigs, "total": len(sigs),
                    "env": {"vix": macro.get("vix",{}).get("price",0),
                            "env_score": state.system_status.get("env_score",70),
                            "session": state.market_session.get("session_zh",""),
                            "regime": state.regime.get("regime_zh","")},
                    "last_scan": state.last_scan_time})


@app.route("/api/signal/<sig_id>/result", methods=["POST"])
def api_signal_result(sig_id):
    data   = request.get_json() or {}
    result = data.get("result","")
    pnl    = float(data.get("pnl", 0))
    if result not in ["tp1","tp2","sl","expired"]:
        return jsonify({"error": "result 必須是 tp1/tp2/sl/expired"}), 400
    store.update_signal_result(sig_id, result, pnl)
    with state._lock:
        for s in state.active_signals:
            if s.get("id") == sig_id:
                state.active_signals.remove(s)
                state.expired_signals.append(s)
                break
        for s in state.signal_log:
            if s.get("id") == sig_id:
                symbol = s.get("symbol","")
                if result == "sl":
                    store.set_consec_loss(symbol, store.get_consec_loss(symbol)+1)
                elif result in ["tp1","tp2"]:
                    store.set_consec_loss(symbol, 0)
                break
    state.signal_log = store.load_signal_log(500)
    return jsonify({"ok": True, "id": sig_id, "result": result, "pnl": pnl})


@app.route("/api/signal/<sig_id>/close", methods=["POST"])
def api_signal_close(sig_id):
    data        = request.get_json() or {}
    close_price = float(data.get("close_price", 0))
    target      = None
    with state._lock:
        for s in state.active_signals:
            if s.get("id") == sig_id:
                target = s; break
    if not target:
        return jsonify({"error": "訊號不存在或已到期"}), 404
    direction = target.get("direction","buy")
    entry     = target.get("entry_price", close_price)
    pnl_pts   = (close_price - entry) if direction=="buy" else (entry - close_price)
    pnl_usd   = round(pnl_pts * target.get("suggested_lot",0.01) * 10000, 2)
    result_type = "tp1" if pnl_usd >= 0 else "sl"
    store.update_signal_result(sig_id, result_type, pnl_usd)
    with state._lock:
        state.active_signals = [s for s in state.active_signals if s.get("id") != sig_id]
    state.signal_log = store.load_signal_log(500)
    return jsonify({"ok": True, "id": sig_id, "pnl_usd": pnl_usd, "result": result_type})


@app.route("/api/trump")
def api_trump():
    return jsonify(state.trump_data or {"has_impact_posts": False, "posts": []})


@app.route("/api/briefing")
def api_briefing():
    return jsonify(state.daily_briefing or {"ai_available": False})


@app.route("/api/briefing/generate", methods=["POST"])
def api_briefing_generate():
    def _gen():
        try:
            from ai_analyst import generate_daily_briefing
            b = generate_daily_briefing(state.macro_data, state.trump_data)
            state.daily_briefing     = b
            state.last_briefing_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            from telegram_bot import format_briefing_message
            safe_send(format_briefing_message(b), priority=4)
        except Exception as e:
            logger.error(f"briefing generate: {e}")
    threading.Thread(target=_gen, daemon=True).start()
    return jsonify({"ok": True, "message": "每日簡報重新生成中"})


@app.route("/api/watchdog/status")
def api_watchdog_status():
    import time as _t
    elapsed  = _t.time() - watchdog._last_ping
    queue_sz = (len(getattr(__import__("watchdog"),"rate_limiter",object())._queue)
                if hasattr(__import__("watchdog"),"rate_limiter") else 0)
    return jsonify({
        "last_ping_ago_sec": round(elapsed),
        "last_ping_ago_min": round(elapsed/60, 1),
        "fail_count":        watchdog._fail_count,
        "status":            "healthy" if elapsed<1800 else "warning" if elapsed<3600 else "dead",
        "telegram_queue":    queue_sz,
        "scan_count":        state.scan_count,
        "last_scan_time":    state.last_scan_time,
    })


@app.route("/api/correlation")
def api_correlation():
    from data_fetcher  import fetch_ohlcv
    from scoring_engine import calc_rolling_correlation
    syms   = ["EURUSD","GBPUSD","USDJPY","XAUUSD","BTCUSD","US500","NAS100"]
    prices = {}
    for sym in syms:
        try:
            d = fetch_ohlcv(sym, "entry")
            if d and d.get("closes"): prices[sym] = d["closes"][-20:]
        except: pass
    sl  = list(prices.keys())
    mat = {s1:{s2:(1.0 if s1==s2 else calc_rolling_correlation(prices[s1],prices[s2],20))
               for s2 in sl} for s1 in sl}
    return jsonify({"matrix": mat, "symbols": sl,
                    "high_corr": [[s1,s2,mat[s1][s2]] for s1 in sl for s2 in sl
                                  if s1<s2 and abs(mat[s1].get(s2,0))>=0.7]})


@app.route("/api/kelly")
def api_kelly():
    from config import ACCOUNT_BALANCE_USD
    wr  = float(request.args.get("win_rate", 0))
    rr  = float(request.args.get("rr", 0))
    bal = float(request.args.get("balance", ACCOUNT_BALANCE_USD))
    if not wr or not rr:
        stats = store.get_trade_stats()
        wr    = wr or stats.get("win_rate",50)/100
        rr    = rr or 1.6
    else:
        wr /= 100
    result = kelly_position_size(wr, rr, bal)
    return jsonify({**result, "win_rate_pct": round(wr*100,1), "rr": rr, "balance": bal})


@app.route("/api/equity")
def api_equity():
    from config import ACCOUNT_BALANCE_USD
    curve   = store.get_equity_curve()
    trades  = store.get_trade_list()
    balance = ACCOUNT_BALANCE_USD
    points  = [{"balance": balance, "trade": None}]
    for t in trades:
        balance += t.get("pnl", 0)
        points.append({"balance": round(balance,2), "symbol": t.get("symbol",""),
                        "direction": t.get("direction",""), "result": t.get("result",""),
                        "pnl": t.get("pnl",0), "date": t.get("generated","")[:10]})
    return jsonify({"points": points, "raw_curve": curve[-200:], "n_points": len(points),
                    "start": ACCOUNT_BALANCE_USD, "current": round(balance,2),
                    "total_pnl": round(balance-ACCOUNT_BALANCE_USD,2),
                    "return_pct": round((balance-ACCOUNT_BALANCE_USD)/ACCOUNT_BALANCE_USD*100,2)})


@app.route("/api/alert/test", methods=["POST"])
def api_alert_test():
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN 未設定"}), 400
    safe_send(
        f"✅ <b>Telegram 測試訊息</b>\n\nMitrade AI v{state.version} 連接正常\n"
        f"時間：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        priority=1)
    return jsonify({"ok": True, "message": "測試訊息已發送"})


@app.route("/api/scan/force", methods=["POST"])
def api_scan_force():
    import time as _t
    last = getattr(api_scan_force, "_last", 0)
    if _t.time() - last < 60:
        return jsonify({"ok": False, "error": "60秒內只能觸發一次"}), 429
    api_scan_force._last = _t.time()
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"ok": True, "message": "掃描已觸發"})


@app.route("/api/settings", methods=["GET","POST"])
def api_settings():
    from config import SIGNAL_THRESHOLDS, CIRCUIT_BREAKER, SYSTEM
    if request.method == "GET":
        return jsonify({
            "min_score":        SIGNAL_THRESHOLDS["min_score"],
            "min_rr":           SIGNAL_THRESHOLDS["min_rr"],
            "risk_per_trade_pct": CIRCUIT_BREAKER["risk_per_trade_pct"],
            "max_lot":          CIRCUIT_BREAKER["max_lot"],
            "scan_interval_min": SYSTEM["scan_interval_min"],
            "vix_extreme":      CIRCUIT_BREAKER["vix_extreme"],
            "vix_threshold":    CIRCUIT_BREAKER["vix_threshold"],
        })
    data    = request.get_json() or {}
    changed = []
    if "min_score"         in data: SIGNAL_THRESHOLDS["min_score"]        = float(data["min_score"]);         changed.append(f"min_score={data['min_score']}")
    if "min_rr"            in data: SIGNAL_THRESHOLDS["min_rr"]           = float(data["min_rr"]);            changed.append(f"min_rr={data['min_rr']}")
    if "risk_per_trade_pct"in data: CIRCUIT_BREAKER["risk_per_trade_pct"] = float(data["risk_per_trade_pct"]);changed.append(f"risk_per_trade_pct={data['risk_per_trade_pct']}")
    if "max_lot"           in data: CIRCUIT_BREAKER["max_lot"]            = float(data["max_lot"]);           changed.append(f"max_lot={data['max_lot']}")
    if "vix_threshold"     in data: CIRCUIT_BREAKER["vix_threshold"]      = float(data["vix_threshold"]);     changed.append(f"vix_threshold={data['vix_threshold']}")
    store.set_meta("settings_override", data)
    if changed: safe_send(f"⚙️ 系統設定已更新：{', '.join(changed)}", priority=3)
    return jsonify({"ok": True, "changed": changed})


@app.route("/api/news/<symbol>")
def api_news(symbol):
    try:
        from data_fetcher import fetch_company_news
        news = fetch_company_news(symbol.upper(), days_back=3)
        return jsonify({"symbol": symbol.upper(), "news": news, "count": len(news)})
    except Exception as e:
        return jsonify({"symbol": symbol.upper(), "news": [], "error": str(e)}), 500


@app.route("/health")
def health():
    return "OK", 200

# ═══════════════════════════════════════
# 排程器（Render 付費版，永不休眠，無需 keep-alive）
# ═══════════════════════════════════════
def start_scheduler():
    try:
        s = BackgroundScheduler(timezone="UTC")
        s.add_job(run_scan,        "interval", minutes=SYSTEM["scan_interval_min"],
                  id="scan",     misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(check_trump,     "interval", minutes=SYSTEM["trump_check_min"],
                  id="trump",    misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(morning_briefing,"cron",     hour=0, minute=30,
                  id="briefing", misfire_grace_time=300, max_instances=1)
        s.add_job(poll_commands,   "interval", seconds=10,
                  id="commands", misfire_grace_time=15, max_instances=1, coalesce=True)
        s.start()
        logger.info("✅ 排程器啟動")

        def _startup():
            time.sleep(3)
            try:
                from data_fetcher import fetch_macro_data, fetch_market_status
                state.macro_data     = fetch_macro_data()
                state.market_session = fetch_market_status()
                state.regime         = detect_regime(state.macro_data)
            except Exception as e:
                logger.error(f"初始宏觀: {e}")

            time.sleep(10)
            safe_send(
                f"🚀 <b>Mitrade AI v{state.version} 已啟動</b>\n\n"
                f"監控品種：外匯 / 商品 / 加密 / 指數\n"
                f"市場機制：{state.regime.get('regime_zh','初始化中')}\n"
                f"掃描頻率：每 {SYSTEM['scan_interval_min']} 分鐘\n"
                f"時段：{state.market_session.get('session_zh','—')}\n\n"
                f"輸入 /help 查看指令",
                priority=5
            )

            time.sleep(20)
            try:   check_trump()
            except: pass

            time.sleep(10)
            try:   morning_briefing()
            except Exception as e: logger.error(f"初始簡報: {e}")

            time.sleep(5)
            try:
                logger.info("🔄 啟動後首次掃描...")
                run_scan()
            except Exception as e:
                logger.error(f"初始掃描: {e}")

        threading.Thread(target=_startup, daemon=True).start()

    except Exception as e:
        logger.error(f"排程器錯誤: {e}")


start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SYSTEM["web_port"], debug=False)
