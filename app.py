"""
app.py — 主程式（雲端穩定版）
"""
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 先確認所有 import 正常 ──
try:
    from flask import Flask, render_template, jsonify
    logger.info("✅ Flask OK")
except Exception as e:
    logger.error(f"❌ Flask import failed: {e}")
    raise

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    logger.info("✅ APScheduler OK")
except Exception as e:
    logger.error(f"❌ APScheduler import failed: {e}")
    raise

try:
    from config import SYMBOLS, SYSTEM, CB, THRESH, DISCLAIMER, TELEGRAM_CHAT_ID
    logger.info("✅ Config OK")
except Exception as e:
    logger.error(f"❌ Config import failed: {e}")
    raise

try:
    from telegram_bot import (
        send_message, format_signal_message,
        format_alert_message, format_briefing_message,
        check_and_process_commands
    )
    logger.info("✅ Telegram OK")
except Exception as e:
    logger.error(f"❌ Telegram import failed: {e}")
    raise

import os as _os
_template_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'templates')
app = Flask(__name__, template_folder=_template_dir)
logger.info(f"Template folder: {_template_dir}")
logger.info(f"Templates exist: {_os.path.exists(_template_dir)}")
if _os.path.exists(_template_dir):
    logger.info(f"Template files: {_os.listdir(_template_dir)}")

# ═══════════════════════════════════════
# 系統狀態
# ═══════════════════════════════════════
class SystemState:
    def __init__(self):
        self.active_signals:  List[Dict] = []
        self.expired_signals: List[Dict] = []
        self.macro_data:      Dict = {}
        self.trump_data:      Dict = {}
        self.daily_briefing:  Dict = {}
        self.system_status:   Dict = {}
        self.market_session:  Dict = {}
        self.signal_log:      List[Dict] = []
        self.last_scan_time:     Optional[str] = None
        self.last_trump_check:   Optional[str] = None
        self.last_briefing_date: Optional[str] = None
        self.scan_count:   int = 0
        self.signal_count: int = 0
        self.version = SYSTEM["version"]
        self._lock = threading.Lock()

    def add_signal(self, sig: Dict):
        with self._lock:
            symbol    = sig.get("symbol")
            direction = sig.get("direction")
            now       = datetime.now(timezone.utc)
            sig["expires_at"] = (now + timedelta(hours=CB["signal_expire_h"])).isoformat()
            sig["status"]     = "active"
            self.active_signals = [
                s for s in self.active_signals
                if not (s.get("symbol")==symbol and s.get("direction")==direction)
            ]
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
            self.signal_log    = self.signal_log[-200:]
            self.active_signals = sorted(
                self.active_signals,
                key=lambda x: x.get("score", 0), reverse=True
            )[:20]
            self.signal_count += 1

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
                            self.expired_signals.append(s)
                    except Exception:
                        valid.append(s)
                else:
                    valid.append(s)
            self.active_signals  = valid
            self.expired_signals = self.expired_signals[-100:]

    def get_snapshot(self) -> Dict:
        with self._lock:
            return {
                "active_signals":    list(self.active_signals),
                "macro_data":        dict(self.macro_data),
                "trump_data":        dict(self.trump_data),
                "daily_briefing":    dict(self.daily_briefing),
                "system_status":     dict(self.system_status),
                "market_session":    dict(self.market_session),
                "signal_log":        list(self.signal_log[-50:]),
                "last_scan_time":    self.last_scan_time,
                "scan_count":        self.scan_count,
                "signal_count":      self.signal_count,
                "version":           self.version,
                "disclaimer":        DISCLAIMER,
            }

state = SystemState()

# ═══════════════════════════════════════
# 掃描函數
# ═══════════════════════════════════════
def run_scan():
    logger.info(f"=== Scan #{state.scan_count+1} ===")
    try:
        from data_fetcher import fetch_macro_data, fetch_market_status
        from risk_manager import get_system_status
        state.macro_data     = fetch_macro_data()
        state.market_session = fetch_market_status()
        state.system_status  = get_system_status(state.macro_data)
        logger.info("Macro data updated")
    except Exception as e:
        logger.error(f"Macro fetch error: {e}")

    state.expire_old()

    env_score = state.system_status.get("env_score", 100)
    if env_score < 20:
        send_message(format_alert_message(
            "市場極度危險",
            f"VIX 極端，今日訊號暫停", "danger"
        ))
        state.scan_count += 1
        state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return

    symbols_to_scan = sorted(
        [s for s in SYMBOLS if not SYMBOLS[s].get("monitor_only")],
        key=lambda s: SYMBOLS[s].get("priority", 3)
    )

    new_signals = []
    for symbol in symbols_to_scan:
        try:
            signal = _scan_symbol(symbol)
            if signal:
                state.add_signal(signal)
                new_signals.append(signal)
                send_message(format_signal_message(signal))
                logger.info(f"✅ {symbol} {signal['direction']} {signal['score']}/100")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Scan error {symbol}: {e}")

    state.scan_count    += 1
    state.last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(f"Scan done. New: {len(new_signals)}, Active: {len(state.active_signals)}")


def _scan_symbol(symbol: str) -> Optional[Dict]:
    try:
        from data_fetcher  import fetch_all_timeframes
        from signal_engine import generate_signal
        from risk_manager  import run_all_checks

        tf_data = fetch_all_timeframes(symbol)
        if not tf_data:
            return None

        risk = run_all_checks(symbol, tf_data, state.macro_data)
        if not risk.get("can_signal"):
            return None

        signal = generate_signal(symbol, tf_data, state.macro_data)
        if not signal:
            return None

        signal["risk_warnings"] = risk.get("warnings", [])

        try:
            from ai_analyst import analyze_signal
            ai = analyze_signal(signal, state.macro_data, state.trump_data)
            signal["ai_analysis"]       = ai
            signal["ai_recommendation"] = ai.get("final_recommendation", "等待")
            signal["ai_reason"]         = ai.get("recommendation_reason", "")
            adj = ai.get("macro_score_adjustment", 0)
            signal["score"] = max(0, min(100, signal["score"] + adj))
        except Exception as e:
            logger.warning(f"AI skip: {e}")
            signal["ai_recommendation"] = "等待"

        return signal
    except Exception as e:
        logger.error(f"_scan_symbol {symbol} error: {e}")
        return None


def check_trump():
    try:
        from ai_analyst import monitor_trump_posts
        trump = monitor_trump_posts()
        state.trump_data = trump
        for post in trump.get("posts", []):
            if post.get("impact_level") == "high":
                send_message(format_alert_message(
                    "川普重大發文",
                    f"原文：{post.get('original_text','')}\n\n"
                    f"AI解讀：{post.get('ai_interpretation','')}\n"
                    f"影響：{', '.join(post.get('affected_assets',[]))}\n\n"
                    f"{post.get('disclaimer','')}",
                    "trump"
                ))
        state.last_trump_check = datetime.now(timezone.utc).strftime("%H:%M UTC")
        logger.info("Trump check done")
    except Exception as e:
        logger.error(f"Trump check error: {e}")


def morning_briefing():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.last_briefing_date == today:
        return
    try:
        from ai_analyst import generate_daily_briefing
        briefing = generate_daily_briefing(state.macro_data, state.trump_data)
        state.daily_briefing     = briefing
        state.last_briefing_date = today
        send_message(format_briefing_message(briefing))
        logger.info("Briefing sent")
    except Exception as e:
        logger.error(f"Briefing error: {e}")


def poll_commands():
    try:
        check_and_process_commands(state)
    except Exception as e:
        logger.error(f"Poll commands error: {e}")


# ═══════════════════════════════════════
# Flask 路由
# ═══════════════════════════════════════
@app.route("/")
def dashboard():
    import os
    # 方法1：render_template
    try:
        return render_template("dashboard.html")
    except Exception as e:
        logger.error(f"render_template failed: {e}")
    # 方法2：直接讀檔案
    try:
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'dashboard.html')
        logger.info(f"Trying direct file read: {template_path}")
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
        else:
            logger.error(f"File not found: {template_path}")
            # 列出目錄內容幫助除錯
            base = os.path.dirname(os.path.abspath(__file__))
            logger.error(f"Files in {base}: {os.listdir(base)}")
    except Exception as e2:
        logger.error(f"Direct read failed: {e2}")
    return "<h1>Mitrade Signal System</h1><p>Loading... Please refresh in 30 seconds.</p><script>setTimeout(()=>location.reload(),10000)</script>", 200

@app.route("/api/state")
def api_state():
    try:
        return jsonify(state.get_snapshot())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/signals")
def api_signals():
    return jsonify({"signals": state.active_signals, "count": len(state.active_signals)})

@app.route("/api/macro")
def api_macro():
    return jsonify({"macro": state.macro_data, "session": state.market_session, "status": state.system_status})

@app.route("/api/briefing")
def api_briefing():
    return jsonify(state.daily_briefing)

@app.route("/api/history")
def api_history():
    return jsonify({"history": state.signal_log, "total": len(state.signal_log)})

@app.route("/api/health")
def api_health():
    return jsonify({
        "status":       "running",
        "version":      SYSTEM["version"],
        "scan_count":   state.scan_count,
        "signal_count": state.signal_count,
        "last_scan":    state.last_scan_time,
    })

@app.route("/health")
def health():
    return "OK", 200

# ═══════════════════════════════════════
# 啟動排程器
# ═══════════════════════════════════════
def start_scheduler():
    try:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(run_scan,         "interval", minutes=SYSTEM["scan_interval_min"], id="scan",     misfire_grace_time=60)
        scheduler.add_job(check_trump,      "interval", minutes=SYSTEM["trump_check_min"],   id="trump",    misfire_grace_time=60)
        scheduler.add_job(morning_briefing, "cron",     hour=0, minute=30,                   id="briefing", misfire_grace_time=300)
        scheduler.add_job(poll_commands,    "interval", seconds=5,                            id="commands", misfire_grace_time=10)
        scheduler.start()
        logger.info("✅ Scheduler started")

        def _startup():
            time.sleep(5)
            logger.info("Running startup sequence...")
            try:
                send_message(
                    f"🚀 <b>Mitrade AI 訊號系統已啟動</b>\n\n"
                    f"版本：v{SYSTEM['version']}\n"
                    f"監控品種：{len(SYMBOLS)} 個\n"
                    f"掃描頻率：每 {SYSTEM['scan_interval_min']} 分鐘\n\n"
                    f"輸入 /help 查看可用指令"
                )
            except Exception as e:
                logger.error(f"Startup message failed: {e}")
            check_trump()
            morning_briefing()
            run_scan()

        threading.Thread(target=_startup, daemon=True).start()

    except Exception as e:
        logger.error(f"Scheduler start failed: {e}")


start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SYSTEM["web_port"], debug=False)
