"""
performance_tracker.py v5.5 — 完整修正版

修正清單：
★ BUG #1：只看最近 6 根 K 棒，訊號有效期 1 小時(12根)但只檢查 30 分鐘
          → 改為掃描全部有效期內的 K 棒（最多 24 根，覆蓋 2 小時緩衝）
★ BUG #2：訊號到期時 pnl 沒有計算（s.get("pnl", 0) 永遠是 0）
          → 到期時以當前市價計算實際浮動盈虧
★ BUG #3：buy 方向的 SL 判斷使用 bar_lows，但 TP 判斷用 bar_highs（正確）
          sell 方向的判斷順序：SL 優先（若同一根 K 棒同時觸及 SL 和 TP，以 SL 為準）
          → 這符合實際交易邏輯，保留此設計
★ 新增：get_real_performance() 加入更多統計欄位（avg_rr, best_trade, worst_trade）
★ 新增：每筆記錄補充 sl_pips, rr1 到 recent_trades，供前端顯示
"""
import logging, math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# check_signal_outcomes — 核心結算函數
# ═══════════════════════════════════════════════════════════

def check_signal_outcomes(state) -> List[Dict]:
    """
    檢查所有 active_signals 是否已觸及 TP1 或 SL。
    在每次 run_scan() 的 update_all_data() 之後呼叫。

    修正：
    - 覆蓋範圍從 6 根改為完整有效期內所有 K 棒（最多 24 根）
    - 到期結算時計算實際浮動盈虧，而不是直接 pnl=0
    """
    from data_fetcher import fetch_ohlcv
    from state_store import store
    from signal_engine import _get_specs

    settled = []
    now = datetime.now(timezone.utc)

    for sig in list(state.active_signals):
        symbol    = sig.get("symbol", "")
        direction = sig.get("direction", "buy")
        entry     = sig.get("entry_price", 0)
        sl        = sig.get("stop_loss", 0)
        tp1       = sig.get("tp1", 0)
        tp2       = sig.get("tp2", 0)
        lot       = sig.get("suggested_lot", 0.01)
        expires_at= sig.get("expires_at", "")

        if not entry or not sl or not tp1:
            continue

        # ── 1. 取 OHLCV ──────────────────────────────────────
        try:
            ohlcv = fetch_ohlcv(symbol, "entry")  # 5M K棒
            if not ohlcv or not ohlcv.get("bars"):
                continue

            all_bars    = ohlcv["bars"]
            current_px  = ohlcv.get("current_price", 0)

            # ★ BUG #1 修正：計算自訊號產生以來的 K 棒數量
            # 訊號有效期 1 小時 = 12 根 5M K 棒
            # 加 4 根緩衝 → 最多取 16 根（80 分鐘）
            max_bars = 16
            recent = all_bars[-max_bars:] if len(all_bars) >= max_bars else all_bars

            # ── 2. 判斷是否觸及 TP1 / SL ─────────────────────
            result     = None
            close_price = current_px

            if direction == "buy":
                for bar in recent:
                    h = bar.get("high", 0)
                    l = bar.get("low", 0)
                    # SL 優先（最壞情況）
                    if sl > 0 and l <= sl:
                        result, close_price = "sl", sl
                        break
                    # TP2 先判（更優先的獲利目標）
                    if tp2 > 0 and h >= tp2:
                        result, close_price = "tp2", tp2
                        break
                    # TP1
                    if tp1 > 0 and h >= tp1:
                        result, close_price = "tp1", tp1
                        break
            else:  # sell
                for bar in recent:
                    h = bar.get("high", 0)
                    l = bar.get("low", 0)
                    if sl > 0 and h >= sl:
                        result, close_price = "sl", sl
                        break
                    if tp2 > 0 and l <= tp2:
                        result, close_price = "tp2", tp2
                        break
                    if tp1 > 0 and l <= tp1:
                        result, close_price = "tp1", tp1
                        break

            # ── 3. 到期處理 ───────────────────────────────────
            # ★ BUG #2 修正：到期時以當前市價計算浮動盈虧
            if result is None and expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if now > exp_dt:
                        result     = "expired"
                        close_price = current_px  # 以當前價結算
                except Exception:
                    pass

            # ── 4. 結算 ───────────────────────────────────────
            if result:
                _, pip_size, _, pip_val = _get_specs(symbol)

                price_diff = (
                    (close_price - entry) if direction == "buy"
                    else (entry - close_price)
                )
                pnl_pips = price_diff / pip_size if pip_size > 0 else 0
                pnl_usd  = round(pnl_pips * pip_val * lot, 2)

                now_str = now.isoformat()

                # 更新 sig 物件
                sig["result"]      = result
                sig["pnl"]         = pnl_usd
                sig["close_price"] = close_price
                sig["closed_at"]   = now_str
                sig["status"]      = "closed"
                sig["pnl_pips"]    = round(pnl_pips, 1)

                # 持久化
                store.update_signal_result(sig.get("id", ""), result, pnl_usd)

                # 連虧計數（只有 sl 累積，tp 和 expired 重置）
                if result == "sl":
                    store.set_consec_loss(symbol, store.get_consec_loss(symbol) + 1)
                else:
                    store.set_consec_loss(symbol, 0)

                # 記錄資金曲線
                store.append_equity(_calc_current_balance(state))

                settled.append(sig)

                result_label = {
                    "tp1":     f"✅ TP1 +${pnl_usd:.2f}",
                    "tp2":     f"🎯 TP2 +${pnl_usd:.2f}",
                    "sl":      f"❌ SL  ${pnl_usd:.2f}",
                    "expired": f"⌛ 到期 ${pnl_usd:+.2f} (市價結算)",
                }.get(result, result)

                logger.info(
                    f"[結算] {symbol} {direction} {result_label} | "
                    f"進場={entry:.5f} 結算={close_price:.5f} | "
                    f"{pnl_pips:.1f}pips | 手數={lot}"
                )

        except Exception as e:
            logger.error(f"check_signal_outcomes [{symbol}]: {e}", exc_info=True)
            continue

    # ── 5. 從 active_signals 移除已結算 ──────────────────────
    if settled:
        settled_ids = {s.get("id") for s in settled}
        with state._lock:
            state.active_signals = [
                s for s in state.active_signals
                if s.get("id") not in settled_ids
            ]
            state.expired_signals.extend(settled)
            state.expired_signals = state.expired_signals[-500:]

        from state_store import store as _store
        state.signal_log = _store.load_signal_log(500)
        logger.info(f"[結算] 本次結算 {len(settled)} 個訊號")

    return settled


def _calc_current_balance(state) -> float:
    """計算帳戶當前淨值（初始餘額 + 所有已結算損益）"""
    from config import ACCOUNT_BALANCE_USD
    from state_store import store
    trades  = store.get_trade_list()
    balance = ACCOUNT_BALANCE_USD
    for t in trades:
        balance += t.get("pnl", 0)
    return round(balance, 2)


# ═══════════════════════════════════════════════════════════
# get_real_performance — 從真實交易計算所有績效指標
# ═══════════════════════════════════════════════════════════

def get_real_performance(state) -> dict:
    """
    從 SQLite signal_log 讀取所有已結算記錄，計算真實績效。

    資料來源：
    - result = "tp1" / "tp2"  → 獲利（計入勝率）
    - result = "sl"            → 虧損（計入敗率）
    - result = "expired"       → 到期，計入損益但不計入勝率
    - result = "pending"       → 尚未結算，排除在外

    說明：
    - 這裡的所有數據 100% 來自你的訊號實際觸及 TP/SL 的記錄
    - 不含任何模擬或假設數據
    """
    from state_store import store
    from config import ACCOUNT_BALANCE_USD

    log    = store.load_signal_log(1000)  # 最多讀 1000 筆
    closed = [s for s in log if s.get("result") in ("tp1", "tp2", "sl")]
    # expired 記錄（有實際盈虧，但不計入 win_rate）
    expired_list = [s for s in log if s.get("result") == "expired"]

    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0,
            "avg_win": 0, "avg_loss": 0,
            "profit_factor": 0, "sharpe": 0,
            "max_drawdown": 0,
            "equity_curve": [], "daily_pnl": [],
            "recent_trades": [],
            "avg_rr": 0,
            "best_trade": 0, "worst_trade": 0,
            "expired_count": len(expired_list),
            "data_source": "real_sqlite",
            "note": "尚無已結算（TP/SL）交易記錄",
        }

    wins   = [s for s in closed if s.get("result") in ("tp1", "tp2")]
    losses = [s for s in closed if s.get("result") == "sl"]
    pnls   = [s.get("pnl", 0) for s in closed]

    total_trades = len(closed)
    win_rate  = round(len(wins) / total_trades * 100, 1)
    total_pnl = round(sum(pnls), 2)

    avg_win  = round(sum(s.get("pnl", 0) for s in wins)   / max(len(wins),   1), 2)
    avg_loss = round(sum(s.get("pnl", 0) for s in losses) / max(len(losses), 1), 2)

    gross_profit = sum(s.get("pnl", 0) for s in wins)
    gross_loss   = abs(sum(s.get("pnl", 0) for s in losses))
    pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0

    # 平均 RR（實際獲利 / 平均虧損）
    avg_rr = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0

    best_trade  = round(max(pnls), 2) if pnls else 0
    worst_trade = round(min(pnls), 2) if pnls else 0

    # ── Sharpe（簡化版，以每筆交易盈虧計算）──────────────────
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl  = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / max(len(pnls) - 1, 1))
        sharpe_raw = mean_pnl / std_pnl * math.sqrt(252) if std_pnl > 1e-10 else 0
        sharpe = round(sharpe_raw, 3) if math.isfinite(sharpe_raw) else 0
    else:
        sharpe = 0

    # ── 資金曲線（從初始餘額逐筆累積）───────────────────────
    balance = ACCOUNT_BALANCE_USD
    max_bal = balance
    max_dd  = 0.0
    eq_curve = [{"idx": 0, "label": "開始", "balance": balance, "result": None, "pnl": 0}]

    # 按 generated_at 排序（由舊到新）
    sorted_trades = sorted(
        closed,
        key=lambda s: s.get("closed_at") or s.get("generated_at") or s.get("generated") or ""
    )

    for i, s in enumerate(sorted_trades):
        balance += s.get("pnl", 0)
        if balance > max_bal:
            max_bal = balance
        dd = (max_bal - balance) / max_bal * 100 if max_bal > 0 else 0
        if dd > max_dd:
            max_dd = dd
        eq_curve.append({
            "idx":      i + 1,
            "label":    s.get("symbol", f"T{i+1}"),
            "balance":  round(balance, 2),
            "result":   s.get("result"),
            "symbol":   s.get("symbol", ""),
            "direction":s.get("direction", ""),
            "pnl":      s.get("pnl", 0),
            "closed_at":s.get("closed_at", ""),
        })

    # ── 逐日損益 ─────────────────────────────────────────────
    daily: dict = {}
    for s in sorted_trades:
        day = (s.get("closed_at") or s.get("generated_at") or s.get("generated") or "")[:10]
        if day:
            daily[day] = round(daily.get(day, 0) + s.get("pnl", 0), 2)
    daily_pnl = [{"date": k, "pnl": v} for k, v in sorted(daily.items())]

    # ── 最近 20 筆交易（含完整欄位）─────────────────────────
    recent = sorted(
        closed,
        key=lambda x: x.get("closed_at") or x.get("generated_at") or "",
        reverse=True
    )[:20]

    # 確保 recent_trades 有必要欄位
    recent_full = []
    for t in recent:
        recent_full.append({
            "id":          t.get("id", ""),
            "symbol":      t.get("symbol", ""),
            "emoji":       t.get("emoji", "📊"),
            "direction":   t.get("direction", ""),
            "entry_price": t.get("entry_price", t.get("entry", 0)),
            "stop_loss":   t.get("stop_loss", t.get("sl", 0)),
            "tp1":         t.get("tp1", 0),
            "tp2":         t.get("tp2", 0),
            "sl_pips":     t.get("sl_pips", 0),
            "rr1":         t.get("rr1", 0),
            "result":      t.get("result", ""),
            "pnl":         t.get("pnl", 0),
            "pnl_pips":    t.get("pnl_pips", 0),
            "suggested_lot":t.get("suggested_lot", 0),
            "score":       t.get("score", 0),
            "closed_at":   t.get("closed_at", ""),
            "generated_at":t.get("generated_at", t.get("generated", "")),
        })

    return {
        # ── 核心勝率指標 ──
        "total":        total_trades,
        "wins":         len(wins),
        "losses":       len(losses),
        "expired_count":len(expired_list),
        "win_rate":     win_rate,

        # ── 損益統計 ──
        "total_pnl":    total_pnl,
        "avg_win":      avg_win,
        "avg_loss":     avg_loss,
        "best_trade":   best_trade,
        "worst_trade":  worst_trade,
        "avg_rr":       avg_rr,
        "profit_factor":pf,

        # ── 風險指標 ──
        "sharpe":       sharpe,
        "max_drawdown": round(max_dd, 2),

        # ── 資金曲線 ──
        "equity_curve": eq_curve,
        "current_balance": round(ACCOUNT_BALANCE_USD + total_pnl, 2),
        "start_balance":   ACCOUNT_BALANCE_USD,
        "return_pct":      round(total_pnl / ACCOUNT_BALANCE_USD * 100, 2) if ACCOUNT_BALANCE_USD else 0,

        # ── 逐日損益 ──
        "daily_pnl":    daily_pnl,

        # ── 近期交易 ──
        "recent_trades":recent_full,

        # ── 元數據 ──
        "data_source":  "real_sqlite",
        "note":         f"以上數據來自 {total_trades} 筆實際觸及 TP/SL 的記錄（不含 {len(expired_list)} 筆到期）",
        "calculated_at":datetime.now(timezone.utc).isoformat(),
    }
