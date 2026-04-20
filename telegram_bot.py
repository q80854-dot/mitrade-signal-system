"""
telegram_bot.py — Telegram Bot 模組
負責：主動推送訊號、接收手機指令、回覆查詢
你可以用手機直接發指令控制系統
"""
import requests
import logging
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCLAIMER

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def send_message(text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
    """發送訊息到 Telegram"""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No Telegram token configured")
        return False

    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return False

    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id":    target,
                "text":       text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def format_signal_message(signal: dict) -> str:
    """把訊號格式化成 Telegram 訊息"""
    direction  = signal.get("direction", "buy")
    dir_emoji  = "🟢" if direction == "buy" else "🔴"
    dir_zh     = "做多 BUY" if direction == "buy" else "做空 SELL"
    score      = signal.get("score", 0)
    action     = signal.get("action", "等待確認")
    action_emoji = "✅" if "立刻" in action else "⏳"

    # 通過的條件
    conditions = signal.get("conditions_met", [])
    cond_text  = "\n".join([f"  ✓ {c}" for c in conditions[:5]])

    # 宏觀備注
    macro_notes = signal.get("macro_notes", [])
    macro_text  = "\n".join([f"  {n}" for n in macro_notes[:3]])

    # AI 建議
    ai_rec    = signal.get("ai_recommendation", "")
    ai_reason = signal.get("ai_reason", "")

    msg = f"""
{dir_emoji} <b>{signal.get('emoji','')} {signal.get('name', signal.get('symbol',''))} — {dir_zh}</b>
━━━━━━━━━━━━━━━━━━━━

{action_emoji} <b>行動：{action}</b>
🎯 AI 信心：{score}/100

📍 <b>進場：</b> <code>{signal.get('entry_price','')}</code>
🛑 <b>止損 SL：</b> <code>{signal.get('stop_loss','')}</code>
🎯 <b>止盈 TP1：</b> <code>{signal.get('tp1','')}</code>  (1:{signal.get('rr1','')})
🎯 <b>止盈 TP2：</b> <code>{signal.get('tp2','')}</code>  (1:{signal.get('rr2','')})

💰 建議手數：<b>{signal.get('suggested_lot',0.01)} 手</b>
⚠️ 風險：帳戶 {signal.get('risk_pct',0)}%（約 ${signal.get('risk_usd',0)}）

📋 <b>Mitrade 操作：</b>
1️⃣ 搜尋 {signal.get('symbol','')}
2️⃣ 點 {"Buy" if direction=="buy" else "Sell"}
3️⃣ 待定單進場價 {signal.get('entry_price','')}
4️⃣ SL：{signal.get('stop_loss','')}
5️⃣ TP1：{signal.get('tp1','')} → TP2：{signal.get('tp2','')}
6️⃣ 手數：{signal.get('suggested_lot',0.01)}

✅ <b>訊號依據：</b>
{cond_text}

🌍 <b>宏觀背景：</b>
{macro_text if macro_text else '  正常環境'}
"""

    if ai_rec:
        msg += f"\n🤖 <b>AI 建議：</b>{ai_rec}"
        if ai_reason:
            msg += f" — {ai_reason}"

    msg += f"""

⏰ {datetime.now(timezone.utc).strftime('%m/%d %H:%M')} UTC
{DISCLAIMER}
"""
    return msg.strip()


def format_alert_message(title: str, body: str, level: str = "warning") -> str:
    """格式化警報訊息"""
    icons = {"warning": "⚠️", "danger": "🚨", "info": "ℹ️", "trump": "⚡"}
    icon  = icons.get(level, "⚠️")
    return f"{icon} <b>{title}</b>\n\n{body}\n\n⏰ {datetime.now(timezone.utc).strftime('%m/%d %H:%M')} UTC"


def format_briefing_message(briefing: dict) -> str:
    """格式化每日簡報"""
    env    = briefing.get("overall_environment", "謹慎交易")
    reason = briefing.get("environment_reason", "")
    opps   = briefing.get("best_opportunities", [])
    avoid  = briefing.get("avoid_today", [])
    risk   = briefing.get("top_risk_today", "")
    trump  = briefing.get("trump_impact", "")

    env_emoji = "🟢" if env == "適合交易" else "🔴" if env == "今日觀望" else "🟡"

    msg = f"""
📊 <b>每日市場簡報</b>
━━━━━━━━━━━━━━━━━━━━

{env_emoji} <b>今日環境：{env}</b>
{reason}
"""
    if opps:
        msg += f"\n✅ <b>今日機會：</b>\n" + "\n".join([f"  • {o}" for o in opps])

    if avoid:
        msg += f"\n\n⛔ <b>今日避開：</b>\n" + "\n".join([f"  • {a}" for a in avoid])

    if risk:
        msg += f"\n\n🔥 <b>最大風險：</b>{risk}"

    if trump:
        msg += f"\n\n⚡ <b>川普因素：</b>{trump}"

    msg += f"\n\n⏰ {datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M')} UTC"
    return msg.strip()


def get_updates(offset: int = None) -> list:
    """取得用戶發來的訊息（用於指令處理）"""
    try:
        params = {"timeout": 1}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception:
        pass
    return []


def process_command(text: str, state) -> str:
    """
    處理手機發來的指令
    支援的指令：
    /status  — 系統狀態
    /signals — 當前所有訊號
    /macro   — 宏觀數據
    /scan    — 立刻掃描
    /help    — 說明
    """
    text = text.strip().lower().split()[0] if text.strip() else ""

    if text in ["/status", "狀態"]:
        snap    = state.get_snapshot()
        sys_st  = snap.get("system_status", {})
        session = snap.get("market_session", {})
        return f"""
🖥️ <b>系統狀態</b>

⚡ 運行中 v{snap.get('version','2.0')}
📡 時段：{session.get('session_zh','未知')}
🔢 訊號數：{len(snap.get('active_signals',[]))}
🔄 掃描次數：{snap.get('scan_count',0)}
⏰ 上次掃描：{snap.get('last_scan_time','—')}
📊 環境評分：{sys_st.get('env_score','—')}/100
💹 VIX：{sys_st.get('vix','—')}
😱 恐懼貪婪：{sys_st.get('fg_score','—')}/100
""".strip()

    elif text in ["/signals", "訊號"]:
        snap    = state.get_snapshot()
        signals = snap.get("active_signals", [])
        if not signals:
            return "📭 目前沒有有效訊號\n下次掃描將在 15 分鐘內執行"
        lines = []
        for s in signals[:5]:
            d = "🟢" if s.get("direction")=="buy" else "🔴"
            lines.append(f"{d} {s.get('emoji','')} {s.get('name','')} {s.get('score',0)}/100 — {s.get('action','')}")
        return "📋 <b>當前訊號</b>\n\n" + "\n".join(lines)

    elif text in ["/macro", "宏觀"]:
        snap  = state.get_snapshot()
        macro = snap.get("macro_data", {})
        vix   = macro.get("vix",{})
        dxy   = macro.get("dxy",{})
        fg    = macro.get("fear_greed",{})
        gold  = macro.get("gold",{})
        return f"""
🌍 <b>宏觀數據</b>

😱 VIX：{vix.get('price','—')} ({vix.get('chg','—'):+.2f}% if vix else '—')
💵 DXY：{dxy.get('price','—')}
🧠 恐懼貪婪：{fg.get('score','—')}/100 {fg.get('label_zh','')}
🥇 黃金：{gold.get('price','—')}
""".strip()

    elif text in ["/help", "說明", "幫助"]:
        return """
📖 <b>指令說明</b>

/status  — 系統狀態
/signals — 查看當前訊號
/macro   — 宏觀數據
/scan    — 立刻掃描（需等待）
/help    — 本說明

💡 你也可以直接輸入中文：
「狀態」「訊號」「宏觀」「說明」
""".strip()

    else:
        return "❓ 不認識這個指令\n輸入 /help 查看說明"


def check_and_process_commands(state):
    """輪詢並處理用戶指令"""
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        updates = get_updates(offset=getattr(check_and_process_commands, '_offset', None))
        for update in updates:
            update_id = update.get("update_id", 0)
            check_and_process_commands._offset = update_id + 1

            msg  = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if text and chat_id == str(TELEGRAM_CHAT_ID):
                reply = process_command(text, state)
                send_message(reply, chat_id=chat_id)

    except Exception as e:
        logger.error(f"Command processing error: {e}")
