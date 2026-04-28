"""
watchdog.py v5.5 — 完整修正版

修正：
★ BUG：_process 呼叫 from telegram_bot import send_message
        但 telegram_bot.py 沒有這個公開函數名稱 → ImportError
        → 改為直接使用 requests 發送，不依賴 telegram_bot
        → 同時保留 safe_send 供 app.py 使用（safe_send 內部呼叫 rate_limiter）
"""
import logging, time, threading, os, requests
from datetime import datetime, timezone
from typing import List

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")


def send_message(text: str) -> bool:
    """
    ★ 修正：watchdog 直接呼叫此函數，不再依賴 telegram_bot.send_message
    公開函數，供 watchdog._process 和外部直接呼叫
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("[Watchdog] Telegram 未設定，跳過")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Watchdog send_message: {e}")
        return False


class TelegramRateLimiter:
    def __init__(self, min_interval: float = 1.5, max_queue: int = 50):
        self._lock         = threading.Lock()
        self._queue        = []
        self._last_sent    = 0.0
        self._min_interval = min_interval
        self._max_queue    = max_queue
        self._worker       = threading.Thread(target=self._process, daemon=True)
        self._worker.start()

    def enqueue(self, text: str, priority: int = 5) -> bool:
        with self._lock:
            if len(self._queue) >= self._max_queue:
                # 清理低優先級的訊息
                self._queue = [m for m in self._queue if m["priority"] <= priority]
            if len(self._queue) >= self._max_queue:
                return False
            self._queue.append({
                "text":      text,
                "priority":  priority,
                "queued_at": time.time(),
            })
            self._queue.sort(key=lambda x: x["priority"])
            return True

    def _process(self):
        while True:
            msg = None
            with self._lock:
                if self._queue:
                    now = time.time()
                    if now - self._last_sent >= self._min_interval:
                        msg = self._queue.pop(0)
                        self._last_sent = now
            if msg:
                try:
                    # ★ 修正：直接呼叫本模組的 send_message，不再 import telegram_bot
                    send_message(msg["text"])
                except Exception as e:
                    logger.error(f"Telegram send: {e}")
            time.sleep(0.2)

    def send(self, text: str, priority: int = 5):
        self.enqueue(text, priority)


rate_limiter = TelegramRateLimiter()


def safe_send(text: str, priority: int = 5):
    """供 app.py 和其他模組統一呼叫的發送函數"""
    rate_limiter.send(text, priority)


class DataQualityChecker:

    @staticmethod
    def validate_ohlcv(data: dict, symbol: str = "") -> dict:
        closes = data.get("closes", [])
        highs  = data.get("highs",  [])
        lows   = data.get("lows",   [])

        if not closes:
            return {"valid": False, "reason": "空數據", "data": data}

        n      = len(closes)
        issues = []

        # 清理異常漲跌幅（>15%）
        clean_closes = []
        for i, c in enumerate(closes):
            if i == 0:
                clean_closes.append(c)
                continue
            prev = clean_closes[-1]
            if prev > 0 and abs(c - prev) / prev > 0.15:
                logger.warning(
                    f"{symbol} bar {i} 異常漲跌 {(c - prev) / prev * 100:.1f}%，使用前值"
                )
                clean_closes.append(prev)
                issues.append(f"bar{i}異常")
            else:
                clean_closes.append(c)

        clean_highs = list(highs)
        clean_lows  = list(lows)

        for i in range(min(n, len(highs), len(lows))):
            c = clean_closes[i]
            if len(clean_highs) > i and clean_highs[i] < c:
                clean_highs[i] = c
                issues.append(f"bar{i}H<C")
            if len(clean_lows) > i and clean_lows[i] > c:
                clean_lows[i] = c
                issues.append(f"bar{i}L>C")

        cleaned = dict(data)
        cleaned["closes"] = clean_closes
        if len(clean_highs) == n:
            cleaned["highs"] = clean_highs
        if len(clean_lows) == n:
            cleaned["lows"] = clean_lows
        cleaned["data_quality"] = {
            "valid":   len(issues) == 0,
            "issues":  issues[:10],
            "cleaned": len(issues) > 0,
        }

        return {
            "valid":  len(issues) <= 3,
            "issues": issues,
            "data":   cleaned,
        }

    @staticmethod
    def validate_macro(macro: dict) -> dict:
        issues = []
        if macro.get("vix"):
            v = macro["vix"].get("price", 0)
            if not (5 <= v <= 150):
                issues.append(f"VIX異常:{v}")
        if macro.get("dxy"):
            v = macro["dxy"].get("price", 0)
            if not (70 <= v <= 130):
                issues.append(f"DXY異常:{v}")
        return {"valid": len(issues) == 0, "issues": issues}


checker = DataQualityChecker()


class Watchdog:
    def __init__(self, timeout_min: int = 30):
        self._timeout    = timeout_min * 60
        self._last_ping  = time.time()
        self._fail_count = 0
        self._running    = True
        self._thread     = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        logger.info(f"✅ Watchdog 啟動（超時 {timeout_min} 分鐘）")

    def ping(self):
        self._last_ping  = time.time()
        self._fail_count = 0

    def record_failure(self, error: str = ""):
        self._fail_count += 1
        logger.error(f"Watchdog 失敗 #{self._fail_count}: {error}")
        if self._fail_count >= 3:
            safe_send(
                f"🚨 <b>系統警報</b>\n\n"
                f"連續失敗 {self._fail_count} 次\n"
                f"最後錯誤：{str(error)[:100]}\n"
                f"請檢查 Render 日誌",
                priority=1,
            )

    def _watch(self):
        while self._running:
            time.sleep(60)
            elapsed = time.time() - self._last_ping
            if elapsed > self._timeout:
                minutes = int(elapsed / 60)
                logger.error(f"Watchdog：已 {minutes} 分鐘無掃描！")
                safe_send(
                    f"⚠️ <b>Watchdog 警告</b>\n\n"
                    f"系統已 {minutes} 分鐘未執行掃描\n"
                    f"請檢查 Render 日誌",
                    priority=1,
                )
                self._try_restart()

    def _try_restart(self):
        try:
            import app as _app
            threading.Thread(target=_app.run_scan, daemon=True).start()
            self._last_ping = time.time()
            logger.info("Watchdog 重啟掃描成功")
        except Exception as e:
            logger.error(f"Watchdog 重啟失敗: {e}")

    def stop(self):
        self._running = False


watchdog = Watchdog(timeout_min=30)
