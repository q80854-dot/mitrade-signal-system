"""
state_store.py v5.2 — SQLite 持久化
"""
import sqlite3, json, logging, threading
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path

logger  = logging.getLogger(__name__)
DB_PATH = Path("instance/mitrade.db")

class StateStore:
    def __init__(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        logger.info(f"✅ StateStore 初始化：{DB_PATH}")

    def _conn(self):
        return sqlite3.connect(DB_PATH, check_same_thread=False)

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS signal_log (
                id TEXT PRIMARY KEY, symbol TEXT, direction TEXT,
                score REAL, entry REAL, sl REAL, tp1 REAL, tp2 REAL, rr1 REAL,
                result TEXT DEFAULT 'pending', pnl REAL DEFAULT 0,
                generated TEXT, closed_at TEXT, data_json TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts_sent (
                alert_key TEXT PRIMARY KEY, alert_date TEXT, sent_at TEXT
            );
            CREATE TABLE IF NOT EXISTS consec_loss (
                symbol TEXT PRIMARY KEY, count INTEGER DEFAULT 0, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT, balance REAL, recorded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy TEXT,
                result_json TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS system_meta (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_signal_symbol ON signal_log(symbol);
            CREATE INDEX IF NOT EXISTS idx_signal_result ON signal_log(result);
            CREATE INDEX IF NOT EXISTS idx_signal_generated ON signal_log(generated);
            CREATE INDEX IF NOT EXISTS idx_alert_date ON alerts_sent(alert_date);
            """)
            conn.commit(); conn.close()

    def save_signal(self, sig: dict):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                INSERT OR REPLACE INTO signal_log
                (id,symbol,direction,score,entry,sl,tp1,tp2,rr1,result,generated,data_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    sig.get("id",""), sig.get("symbol",""), sig.get("direction",""),
                    sig.get("score",0), sig.get("entry_price",0), sig.get("stop_loss",0),
                    sig.get("tp1",0), sig.get("tp2",0), sig.get("rr1",0),
                    sig.get("result","pending"),
                    sig.get("generated_at", datetime.now(timezone.utc).isoformat()),
                    json.dumps(sig, ensure_ascii=False),
                ))
                conn.commit()
            except Exception as e: logger.error(f"save_signal: {e}")
            finally: conn.close()

    def update_signal_result(self, sig_id: str, result: str, pnl: float = 0):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE signal_log SET result=?, pnl=?, closed_at=? WHERE id=?",
                    (result, pnl, datetime.now(timezone.utc).isoformat(), sig_id))
                conn.commit()
            except Exception as e: logger.error(f"update_signal_result: {e}")
            finally: conn.close()

    def load_signal_log(self, limit: int = 500) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT data_json,result,pnl FROM signal_log ORDER BY generated DESC LIMIT ?",
                    (limit,)).fetchall()
                result = []
                for row in rows:
                    try:
                        d = json.loads(row[0]); d["result"] = row[1]; d["pnl"] = row[2]
                        result.append(d)
                    except: pass
                return result
            except Exception as e: logger.error(f"load_signal_log: {e}"); return []
            finally: conn.close()

    def get_trade_stats(self) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("""
                SELECT result, COUNT(*), SUM(pnl), AVG(score)
                FROM signal_log WHERE result != 'pending' GROUP BY result""").fetchall()
                stats = {"tp":0,"sl":0,"expired":0,"total_pnl":0.0,"avg_score":0}
                for result, cnt, pnl, avg_sc in rows:
                    if result in ["tp1","tp2"]: stats["tp"] += cnt
                    elif result == "sl":        stats["sl"] += cnt
                    elif result == "expired":   stats["expired"] += cnt
                    stats["total_pnl"] += (pnl or 0)
                    stats["avg_score"]  = avg_sc or 0
                total = stats["tp"] + stats["sl"]
                stats["win_rate"] = round(stats["tp"]/total*100,1) if total>0 else 0
                stats["total"]    = total + stats["expired"]
                return stats
            except Exception as e: logger.error(f"get_trade_stats: {e}"); return {}
            finally: conn.close()

    def get_equity_curve(self) -> List[float]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT balance FROM equity_curve ORDER BY id").fetchall()
                return [r[0] for r in rows]
            except: return []
            finally: conn.close()

    def get_trade_list(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("""
                SELECT symbol,direction,score,entry,sl,tp1,rr1,result,pnl,generated
                FROM signal_log WHERE result != 'pending' ORDER BY generated""").fetchall()
                return [{"symbol":r[0],"direction":r[1],"score":r[2],"entry":r[3],
                         "sl":r[4],"tp1":r[5],"rr1":r[6],"result":r[7],
                         "pnl":r[8],"generated":r[9]} for r in rows]
            except: return []
            finally: conn.close()

    def alert_sent_today(self, key: str) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM alerts_sent WHERE alert_key=? AND alert_date=?",
                    (key, today)).fetchone()
                if row: return True
                conn.execute(
                    "INSERT OR REPLACE INTO alerts_sent (alert_key,alert_date,sent_at) VALUES (?,?,?)",
                    (key, today, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                return False
            except: return False
            finally: conn.close()

    def get_consec_loss(self, symbol: str) -> int:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT count FROM consec_loss WHERE symbol=?", (symbol,)).fetchone()
                return row[0] if row else 0
            except: return 0
            finally: conn.close()

    def set_consec_loss(self, symbol: str, count: int):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO consec_loss (symbol,count,updated_at) VALUES (?,?,?)",
                    (symbol, count, datetime.now(timezone.utc).isoformat()))
                conn.commit()
            except Exception as e: logger.error(f"set_consec_loss: {e}")
            finally: conn.close()

    def append_equity(self, balance: float):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("INSERT INTO equity_curve (balance,recorded_at) VALUES (?,?)",
                    (balance, datetime.now(timezone.utc).isoformat()))
                conn.commit()
            except Exception as e: logger.error(f"append_equity: {e}")
            finally: conn.close()

    def save_backtest(self, symbol: str, strategy: str, result: dict):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO backtest_results (symbol,strategy,result_json,created_at) VALUES (?,?,?,?)",
                    (symbol, strategy, json.dumps(result, ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()))
                conn.commit()
            except Exception as e: logger.error(f"save_backtest: {e}")
            finally: conn.close()

    def load_backtest(self, symbol: str = None, limit: int = 20) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                if symbol:
                    rows = conn.execute(
                        "SELECT symbol,strategy,result_json,created_at FROM backtest_results WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
                        (symbol, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT symbol,strategy,result_json,created_at FROM backtest_results ORDER BY created_at DESC LIMIT ?",
                        (limit,)).fetchall()
                result = []
                for r in rows:
                    try:
                        d = json.loads(r[2]); d["symbol"]=r[0]; d["strategy"]=r[1]; d["created_at"]=r[3]
                        result.append(d)
                    except: pass
                return result
            except: return []
            finally: conn.close()

    def set_meta(self, key: str, value):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO system_meta (key,value,updated_at) VALUES (?,?,?)",
                    (key, json.dumps(value), datetime.now(timezone.utc).isoformat()))
                conn.commit()
            except: pass
            finally: conn.close()

    def get_meta(self, key: str, default=None):
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT value FROM system_meta WHERE key=?", (key,)).fetchone()
                return json.loads(row[0]) if row else default
            except: return default
            finally: conn.close()

store = StateStore()
