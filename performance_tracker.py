"""
performance_tracker.py v1.0
自動模擬績效追蹤：
  - 每次掃描自動檢查訊號是否觸及 TP1/SL
  - 計算真實盈虧（根據手數、pip_value）
  - 提供 get_real_performance() 給 dashboard 使用
"""
import logging, math
from datetime import datetime, timezone
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def check_signal_outcomes(state) -> List[Dict]:
    """
    檢查所有 active_signals 是否已觸及 TP1 或 SL
    在 run_scan() 裡 update_all_data() 之後呼叫
    """
    from data_fetcher import fetch_ohlcv
    from state_store  import store
    from signal_engine import _get_specs

    settled = []

    for sig in list(state.active_signals):
        symbol    = sig.get("symbol", "")
        direction = sig.get("direction", "buy")
        entry     = sig.get("entry_price", 0)
        sl        = sig.get("stop_loss", 0)
        tp1       = sig.get("tp1", 0)
        lot       = sig.get("suggested_lot", 0.01)
        if not entry or not sl or not tp1: continue

        try:
            ohlcv = fetch_ohlcv(symbol, "entry")  # 5M K棒
            if not ohlcv or not ohlcv.get("bars"): continue

            # 取最近 K 棒的 high/low 來判斷是否觸及
            recent = ohlcv["bars"][-6:]  # 最近 6 根 5M K棒（30分鐘）
            current = ohlcv.get("current_price", 0)

            result      = None
            close_price = current

            if direction == "buy":
                bar_lows  = [b["low"]  for b in recent]
                bar_highs = [b["high"] for b in recent]
                if sl > 0 and min(bar_lows) <= sl:
                    result, close_price = "sl", sl
                elif tp1 > 0 and max(bar_highs) >= tp1:
                    result, close_price = "tp1", tp1
            else:
                bar_highs = [b["high"] for b in recent]
                bar_lows  = [b["low"]  for b in recent]
                if sl > 0 and max(bar_highs) >= sl:
                    result, close_price = "sl", sl
                elif tp1 > 0 and min(bar_lows) <= tp1:
                    result, close_price = "tp1", tp1

            if result:
                # 計算盈虧
                _, pip_size, _, pip_val = _get_specs(symbol)
                price_diff = (close_price - entry) if direction == "buy" else (entry - close_price)
                pnl_pips   = price_diff / pip_size if pip_size > 0 else 0
                pnl_usd    = round(pnl_pips * pip_val * lot, 2)

                now_str = datetime.now(timezone.utc).isoformat()
                sig["result"]      = result
                sig["pnl"]         = pnl_usd
                sig["close_price"] = close_price
                sig["closed_at"]   = now_str
                sig["status"]      = "closed"

                store.update_signal_result(sig.get("id", ""), result, pnl_usd)

                if result == "sl":
                    store.set_consec_loss(symbol, store.get_consec_loss(symbol) + 1)
                else:
                    store.set_consec_loss(symbol, 0)

                store.append_equity(ACCOUNT_BALANCE_USD_current(state))
                settled.append(sig)

                logger.info(
                    f"  [{symbol}] 自動結算 {result} "
                    f"entry={entry:.5f} close={close_price:.5f} P&L=${pnl_usd:+.2f}"
                )

        except Exception as e:
            logger.error(f"check_signal_outcomes [{symbol}]: {e}")

    # 從 active 移除已結算
    if settled:
        settled_ids = {s.get("id") for s in settled}
        with state._lock:
            state.active_signals  = [s for s in state.active_signals
                                      if s.get("id") not in settled_ids]
            state.expired_signals.extend(settled)
            from state_store import store
            state.signal_log = store.load_signal_log(500)
        logger.info(f"本次自動結算 {len(settled)} 個訊號")

    return settled


def ACCOUNT_BALANCE_USD_current(state) -> float:
    """取得帳戶當前淨值（用於資金曲線）"""
    from config import ACCOUNT_BALANCE_USD
    from state_store import store
    trades  = store.get_trade_list()
    balance = ACCOUNT_BALANCE_USD
    for t in trades:
        balance += t.get("pnl", 0)
    return round(balance, 2)


def get_real_performance(state) -> dict:
    """從真實交易記錄計算所有績效指標"""
    from state_store import store
    from config      import ACCOUNT_BALANCE_USD

    log    = store.load_signal_log(500)
    closed = [s for s in log if s.get("result") in ("tp1", "tp2", "sl")]

    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0,
            "avg_win": 0, "avg_loss": 0,
            "profit_factor": 0, "sharpe": 0,
            "max_drawdown": 0, "equity_curve": [],
            "daily_pnl": [], "recent_trades": [],
        }

    wins   = [s for s in closed if s.get("result") in ("tp1", "tp2")]
    losses = [s for s in closed if s.get("result") == "sl"]
    pnls   = [s.get("pnl", 0) for s in closed]

    win_rate     = round(len(wins) / len(closed) * 100, 1)
    total_pnl    = round(sum(pnls), 2)
    avg_win      = round(sum(s.get("pnl", 0) for s in wins)   / max(len(wins),   1), 2)
    avg_loss     = round(sum(s.get("pnl", 0) for s in losses) / max(len(losses), 1), 2)
    gross_profit = sum(s.get("pnl", 0) for s in wins)
    gross_loss   = abs(sum(s.get("pnl", 0) for s in losses))
    pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

    # Sharpe（簡化版）
    mean_pnl = sum(pnls) / len(pnls)
    std_pnl  = math.sqrt(sum((p - mean_pnl)**2 for p in pnls) / max(len(pnls), 1))
    sharpe   = round(mean_pnl / std_pnl * math.sqrt(252), 3) if std_pnl > 1e-10 else 0
    if not math.isfinite(sharpe): sharpe = 0

    # 資金曲線
    balance  = ACCOUNT_BALANCE_USD
    max_bal  = balance
    max_dd   = 0
    eq_curve = [{"day": "開始", "balance": balance, "result": None, "pnl": 0}]
    for i, s in enumerate(closed):
        balance += s.get("pnl", 0)
        if balance > max_bal: max_bal = balance
        dd = (max_bal - balance) / max_bal * 100
        if dd > max_dd: max_dd = dd
        eq_curve.append({
            "day":       f"T{i+1}",
            "balance":   round(balance, 2),
            "result":    s.get("result"),
            "symbol":    s.get("symbol"),
            "direction": s.get("direction"),
            "pnl":       s.get("pnl", 0),
        })

    # 逐日 P&L
    daily: dict = {}
    for s in closed:
        day = (s.get("closed_at") or s.get("generated", ""))[:10]
        if day: daily[day] = round(daily.get(day, 0) + s.get("pnl", 0), 2)
    daily_pnl = [{"date": k, "pnl": v} for k, v in sorted(daily.items())]

    # 最近 10 筆
    recent = sorted(closed, key=lambda x: x.get("closed_at",""), reverse=True)[:10]

    return {
        "total":          len(closed),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       win_rate,
        "total_pnl":      total_pnl,
        "avg_win":        avg_win,
        "avg_loss":       avg_loss,
        "profit_factor":  pf,
        "sharpe":         sharpe,
        "max_drawdown":   round(max_dd, 2),
        "equity_curve":   eq_curve,
        "daily_pnl":      daily_pnl,
        "recent_trades":  recent,
        "current_balance": round(ACCOUNT_BALANCE_USD + total_pnl, 2),
    }
