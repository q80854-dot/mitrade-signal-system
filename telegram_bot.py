"""
telegram_bot.py v5.5 — 修正版

核心概念：城市交易員 / 手動下單輔助系統
─────────────────────────────────────────
系統角色：AI 分析師 → 你是操盤手
系統做：掃描市場、計算訊號、發送明確進出場決策
你做：看到訊號後，自己在 Mitrade 手動按買/賣

每個 TG 訊息格式：
  1. 進場訊號：明確告訴你「現在做多/空 XX」進場價、止損、止盈
  2. 出場提醒：告訴你「XX 訊號觸及 TP1/SL，請平倉」
  3. 摘要：每 30 分鐘一次環境概況（不是分數排行）

修正：
★ format_signal_message：完整進出場決策格式
★ format_outcome_message：新增「請平倉」提醒
★ format_scan_summary：移除無用分數排行，改為環境+當前持倉概況
★ format_trump_alert：加入對應建議動作
★ check_and_process_commands：新增 /status /positions /help 指令
"""
import logging
import os
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
_last_update_id    = 0


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """底層發送，失敗時重試一次"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TG] 未設定 BOT_TOKEN 或 CHAT_ID，跳過發送")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(2):
        try:
            r = requests.post(url, json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": parse_mode,
            }, timeout=10)
            if r.status_code == 200:
                return True
            logger.warning(f"[TG] 發送失敗 {r.status_code}: {r.text[:100]}")
        except Exception as e:
            logger.error(f"[TG] 發送例外 (attempt {attempt+1}): {e}")
    return False


# ════════════════════════════════════════════════════════
# 進場訊號 — 最重要的訊息，讓你知道「現在去哪裡下單」
# ════════════════════════════════════════════════════════
def format_signal_message(sig: dict) -> str:
    """
    城市交易員版進場訊號
    格式：你打開 Mitrade → 找到品種 → 按照這個訊息下單
    """
    direction  = sig.get("direction", "buy")
    symbol     = sig.get("symbol", "")
    name       = sig.get("name", symbol)
    emoji      = sig.get("emoji", "📊")
    score      = sig.get("score", 0)
    entry      = sig.get("entry_price", 0)
    sl         = sig.get("stop_loss",  0)
    tp1        = sig.get("tp1", 0)
    tp2        = sig.get("tp2", 0)
    rr1        = sig.get("rr1", 0)
    rr2        = sig.get("rr2", 0)
    lot        = sig.get("suggested_lot", 0)
    risk_pct   = sig.get("risk_pct", 0)
    sl_pips    = sig.get("sl_pips", 0)
    expires_at = sig.get("expires_at", "")
    ai_rec     = sig.get("ai_recommendation", "")
    conds      = sig.get("conditions_met", [])
    action     = sig.get("action", "")
    margin_pct = sig.get("margin_pct", 0)
    lev        = sig.get("recommended_leverage", 30)
    risk_usd   = sig.get("risk_usd", 0)

    dir_zh = "🟢 做多（BUY）" if direction == "buy" else "🔴 做空（SELL）"
    dir_arrow = "▲" if direction == "buy" else "▼"

    # 有效期剩餘
    exp_str = ""
    if expires_at:
        try:
            diff = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
            mins = int(diff.total_seconds() / 60)
            exp_str = f"⏳ 有效期：{mins} 分鐘內進場"
        except Exception:
            exp_str = "⏳ 有效期：60 分鐘"

    # 信心等級
    if score >= 85:
        conf = "🔥 極強（立刻進場）"
    elif score >= 75:
        conf = "✅ 強（可以進場）"
    elif score >= 65:
        conf = "⚡ 中（等待確認）"
    else:
        conf = "👀 弱（僅觀察）"

    # 條件摘要（最多 3 條）
    cond_str = ""
    if conds:
        cond_str = "\n".join([f"  ✓ {c}" for c in conds[:3]])
        cond_str = f"\n📋 共振條件：\n{cond_str}"

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📡 <b>訊號 | {emoji} {name}</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"方向：<b>{dir_zh}</b>",
        f"信心：{conf}（{score:.0f}/100）",
        f"",
        f"🎯 <b>下單參數（在 Mitrade 輸入）</b>",
        f"進場價：<code>{_fp(entry)}</code>（市價附近）",
        f"止損：<code>{_fp(sl)}</code>  （{sl_pips:.0f} pips）",
        f"止盈1：<code>{_fp(tp1)}</code>  （RR 1:{rr1}）",
        f"止盈2：<code>{_fp(tp2)}</code>  （RR 1:{rr2}）",
        f"",
        f"💰 <b>資金管理</b>",
        f"建議手數：<b>{lot}</b> 手",
        f"建議槓桿：<b>{lev}x</b>",
        f"風險金額：${risk_usd:.2f}（帳戶 {risk_pct:.1f}%）",
        f"保證金佔用：約 {margin_pct:.1f}%",
        f"",
        f"{exp_str}",
        cond_str,
    ]

    if ai_rec and ai_rec != "等待":
        lines.append(f"\n🤖 AI 建議：{ai_rec}")

    lines += [
        f"",
        f"⚠️ 此為輔助訊號，請自行判斷後在 Mitrade 手動下單",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(l for l in lines if l is not None)


# ════════════════════════════════════════════════════════
# 出場提醒 — 告訴你「現在去平倉」
# ════════════════════════════════════════════════════════
def format_outcome_message(sig: dict) -> str:
    """
    ★ 新增：結算通知
    讓你知道：這個倉位已觸及 TP 或 SL，現在去 Mitrade 確認平倉
    """
    result    = sig.get("result", "")
    symbol    = sig.get("symbol", "")
    name      = sig.get("name", symbol)
    emoji     = sig.get("emoji", "📊")
    direction = sig.get("direction", "buy")
    entry     = sig.get("entry_price", 0)
    close_px  = sig.get("close_price", 0)
    pnl       = sig.get("pnl", 0)
    pnl_pips  = sig.get("pnl_pips", 0)
    lot       = sig.get("suggested_lot", 0)
    dir_zh    = "做多" if direction == "buy" else "做空"

    if result == "tp1":
        header = f"🎯 止盈1達成！{emoji} {name}"
        action = "✅ 請到 Mitrade 確認倉位已平倉（或移動止損至 TP1 保本）"
        pnl_str = f"+${pnl:.2f}"
    elif result == "tp2":
        header = f"🏆 止盈2達成！{emoji} {name}"
        action = "✅ 請到 Mitrade 平倉獲利了結"
        pnl_str = f"+${pnl:.2f}"
    elif result == "sl":
        header = f"❌ 觸及止損 {emoji} {name}"
        action = "⛔ 請確認 Mitrade 止損單已執行，倉位已平倉"
        pnl_str = f"${pnl:.2f}"
    elif result == "expired":
        header = f"⌛ 訊號到期 {emoji} {name}"
        action = "📌 訊號已過期。若您有持倉，請根據當前市況自行決定是否平倉"
        pnl_str = f"${pnl:+.2f}（浮動估算）"
    else:
        return ""

    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{header}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"方向：{dir_zh}  |  手數：{lot}\n"
        f"進場：<code>{_fp(entry)}</code> → 結算：<code>{_fp(close_px)}</code>\n"
        f"損益：<b>{pnl_str}</b>（{pnl_pips:+.1f} pips）\n"
        f"\n"
        f"📲 <b>行動：{action}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


# ════════════════════════════════════════════════════════
# 掃描摘要 — 每 30 分鐘，環境概況 + 當前有效訊號
# ════════════════════════════════════════════════════════
def format_scan_summary(scan_num: int, new_signals: list, active_count: int,
                        scores: dict, session: str, vix, regime_zh: str,
                        win_rate: float = 0) -> str:
    """
    ★ 修正：移除純分數排行，改為「當前有效訊號一覽」
    讓你一眼看到現在有哪些倉位可以進場
    """
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # 環境評估
    vix_f = float(vix) if vix and str(vix).replace('.','').isdigit() else 0
    env_str = "🟢 適合交易" if vix_f < 20 else "🟡 謹慎操作" if vix_f < 30 else "🔴 高波動注意"

    lines = [
        f"📊 <b>第 {scan_num} 次掃描摘要</b> | {now_str}",
        f"",
        f"⏰ 時段：{session}",
        f"🌡 VIX：{vix}  |  市場：{regime_zh}",
        f"環境：{env_str}",
        f"勝率：{win_rate:.1f}%（歷史）",
        f"",
    ]

    if new_signals:
        lines.append(f"🆕 <b>本次新訊號（{len(new_signals)} 個）</b>")
        for s in new_signals[:5]:
            d   = "▲ BUY" if s.get("direction") == "buy" else "▼ SELL"
            sc  = s.get("score", 0)
            act = s.get("action", "")
            lines.append(
                f"  {s.get('emoji','📊')} {s.get('symbol','')} "
                f"{d} | 評分 {sc:.0f} | {act}"
            )
    else:
        lines.append("💤 本次無新訊號")

    lines.append("")

    if active_count > 0:
        lines.append(f"📡 <b>當前有效訊號：{active_count} 個</b>（可進場）")
        lines.append("發送 /positions 查看完整進出場參數")
    else:
        lines.append("📭 目前無有效訊號")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════
# 川普動態 — 加入建議動作
# ════════════════════════════════════════════════════════
def format_trump_alert(post: dict) -> str:
    impact    = post.get("impact_level", "medium")
    mood      = post.get("market_impact", "neutral")
    assets    = post.get("affected_assets", [])
    interp    = post.get("ai_interpretation", post.get("original_text", ""))
    event_type= post.get("event_type", "")

    mood_str = "📈 市場偏多" if mood == "bullish" else "📉 市場偏空" if mood == "bearish" else "➡️ 影響中性"
    imp_str  = "🚨 高影響" if impact == "high" else "⚡ 中影響" if impact == "medium" else "ℹ️ 低影響"

    # 建議動作
    if mood == "bullish" and impact == "high":
        suggestion = "建議：留意 BTC/黃金/指數做多機會，等待下次掃描確認"
    elif mood == "bearish" and impact == "high":
        suggestion = "建議：避免新進多單，留意做空機會，提高警惕"
    else:
        suggestion = "建議：暫時觀察，等待市場消化此消息"

    assets_str = "、".join(assets[:5]) if assets else "待確認"

    return (
        f"📢 <b>川普發文 | {imp_str}</b>\n"
        f"\n"
        f"AI 解讀：{interp[:200]}\n"
        f"\n"
        f"市場影響：{mood_str}\n"
        f"影響品種：{assets_str}\n"
        f"\n"
        f"💡 {suggestion}\n"
        f"（下次掃描將自動調整評分）"
    )


# ════════════════════════════════════════════════════════
# 每日簡報
# ════════════════════════════════════════════════════════
def format_briefing_message(briefing: dict) -> str:
    env     = briefing.get("overall_environment", "—")
    reason  = briefing.get("environment_reason",  "—")
    best    = briefing.get("best_opportunities",  [])
    avoid   = briefing.get("avoid_today",         [])
    risk    = briefing.get("top_risk_today",       "")
    trump   = briefing.get("trump_impact",         "")
    date    = briefing.get("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    best_str  = "、".join(best[:4])  if best  else "暫無"
    avoid_str = "、".join(avoid[:3]) if avoid else "無"

    lines = [
        f"📅 <b>每日市場簡報 | {date}</b>",
        f"",
        f"🌍 整體環境：<b>{env}</b>",
        f"原因：{reason}",
        f"",
        f"✅ 今日最佳品種：{best_str}",
        f"⛔ 今日避開：{avoid_str}",
    ]
    if risk:
        lines.append(f"⚠️ 主要風險：{risk}")
    if trump:
        lines.append(f"🇺🇸 川普因素：{trump}")
    lines += [
        f"",
        f"🕐 系統每 5 分鐘掃描一次，有訊號時即時通知",
        f"輸入 /help 查看所有指令",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════
# 一般警報
# ════════════════════════════════════════════════════════
def format_alert_message(title: str, body: str, alert_type: str = "info") -> str:
    icons = {"earnings": "📅", "vix": "🚨", "info": "ℹ️", "warning": "⚠️"}
    icon  = icons.get(alert_type, "ℹ️")
    return f"{icon} <b>{title}</b>\n\n{body}"


# ════════════════════════════════════════════════════════
# 指令處理 — /help /status /positions /stats
# ════════════════════════════════════════════════════════
def check_and_process_commands(state) -> None:
    """處理 Telegram 指令，每 10 秒輪詢一次"""
    global _last_update_id
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        r   = requests.get(url, params={"offset": _last_update_id + 1, "timeout": 5}, timeout=8)
        if r.status_code != 200:
            return
        updates = r.json().get("result", [])
        for upd in updates:
            _last_update_id = upd.get("update_id", _last_update_id)
            msg  = upd.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            # 只回應設定的 CHAT_ID
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            if text in ("/help", "help"):
                _send(_cmd_help())

            elif text in ("/status", "status"):
                _send(_cmd_status(state))

            elif text in ("/positions", "/pos", "positions"):
                _send(_cmd_positions(state))

            elif text in ("/stats", "stats"):
                _send(_cmd_stats(state))

            elif text in ("/scan", "scan"):
                _send("⚡ 觸發強制掃描中，約 10-30 秒後通知結果…")
                import threading
                from app import run_scan
                threading.Thread(target=run_scan, daemon=True).start()

            elif text in ("/briefing", "briefing"):
                b = state.daily_briefing
                if b:
                    _send(format_briefing_message(b))
                else:
                    _send("📭 今日簡報尚未生成")

            elif text.startswith("/"):
                _send(f"❓ 未知指令：{text}\n\n輸入 /help 查看所有指令")

    except Exception as e:
        logger.debug(f"[TG] poll_commands: {e}")


def _cmd_help() -> str:
    return (
        "🤖 <b>Mitrade AI 指令列表</b>\n"
        "\n"
        "/positions — 查看當前所有有效訊號（進場參數）\n"
        "/status    — 系統狀態（環境分、時段、VIX）\n"
        "/stats     — 歷史勝率與損益統計\n"
        "/scan      — 立即觸發一次掃描\n"
        "/briefing  — 查看今日市場簡報\n"
        "/help      — 顯示此列表\n"
        "\n"
        "📌 系統說明：\n"
        "• AI 掃描市場，發送進場訊號\n"
        "• 你在 Mitrade 手動下單\n"
        "• 觸及 TP/SL 時系統發送平倉提醒\n"
        "• 所有訊號含進場價、止損、止盈"
    )


def _cmd_status(state) -> str:
    sys   = state.system_status
    sess  = state.market_session
    macro = state.macro_data
    vix   = macro.get("vix",  {}).get("price", "—")
    fg    = macro.get("fear_greed", {}).get("score", "—")
    now   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return (
        f"📊 <b>系統狀態 | {now}</b>\n"
        f"\n"
        f"環境評分：{sys.get('env_score', 0)}/100\n"
        f"狀態：{sys.get('env_status', '—')}\n"
        f"時段：{sess.get('session_zh', '—')}\n"
        f"市場機制：{state.regime.get('regime_zh', '—')}\n"
        f"VIX：{vix}  |  恐懼貪婪：{fg}\n"
        f"掃描次數：#{state.scan_count}\n"
        f"上次掃描：{state.last_scan_time or '—'}\n"
        f"有效訊號：{len(state.active_signals)} 個"
    )


def _cmd_positions(state) -> str:
    """最重要的指令：顯示當前所有可進場訊號的完整參數"""
    sigs = state.active_signals
    if not sigs:
        return (
            "📭 <b>當前無有效訊號</b>\n"
            "\n"
            "系統每 5 分鐘掃描一次\n"
            "有訊號時會自動推送\n"
            "或輸入 /scan 觸發立即掃描"
        )

    lines = [f"📡 <b>當前有效訊號 ({len(sigs)} 個)</b>\n"]
    for sig in sigs[:5]:
        d      = "▲ BUY（做多）" if sig.get("direction") == "buy" else "▼ SELL（做空）"
        symbol = sig.get("symbol", "")
        name   = sig.get("name", symbol)
        entry  = sig.get("entry_price", 0)
        sl     = sig.get("stop_loss",  0)
        tp1    = sig.get("tp1", 0)
        tp2    = sig.get("tp2", 0)
        rr1    = sig.get("rr1", 0)
        lot    = sig.get("suggested_lot", 0)
        score  = sig.get("score", 0)
        action = sig.get("action", "")

        # 有效期倒數
        exp_str = ""
        expires = sig.get("expires_at", "")
        if expires:
            try:
                diff    = datetime.fromisoformat(expires) - datetime.now(timezone.utc)
                mins    = max(0, int(diff.total_seconds() / 60))
                exp_str = f"⏳ 剩 {mins} 分鐘"
            except Exception:
                pass

        lines += [
            f"━━━━━━━━━━━━━",
            f"{sig.get('emoji','📊')} <b>{name}</b>  {d}",
            f"評分：{score:.0f}/100  {action}  {exp_str}",
            f"進場：<code>{_fp(entry)}</code>",
            f"止損：<code>{_fp(sl)}</code>  止盈1：<code>{_fp(tp1)}</code>  (RR 1:{rr1})",
            f"止盈2：<code>{_fp(tp2)}</code>  手數：{lot}",
            f"",
        ]

    lines.append("⚠️ 以上訊號請自行在 Mitrade 手動下單")
    return "\n".join(lines)


def _cmd_stats(state) -> str:
    wr = state.calc_win_rate()
    return (
        f"📈 <b>歷史績效統計</b>\n"
        f"\n"
        f"總交易：{wr.get('total', 0)} 筆\n"
        f"獲利：{wr.get('wins', 0)} 筆  |  虧損：{wr.get('losses', 0)} 筆\n"
        f"勝率：<b>{wr.get('win_rate', 0):.1f}%</b>\n"
        f"\n"
        f"💡 目標勝率：45–60%（RR 1.5 盈虧平衡 = 40%）\n"
        f"⚠️ 數據來自系統訊號觸及 TP/SL 的真實記錄"
    )


# ════════════════════════════════════════════════════════
# 工具函數
# ════════════════════════════════════════════════════════
def _fp(p) -> str:
    """格式化價格"""
    if not p:
        return "—"
    n = float(p)
    if n > 10000:
        return f"{n:.2f}"
    if n > 100:
        return f"{n:.3f}"
    if n > 1:
        return f"{n:.4f}"
    return f"{n:.5f}"
