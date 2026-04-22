"""
telegram_bot.py v4.0 — 完整 Telegram Bot
新增：/history、/stats、訊號分組推送、警報分級
"""
import requests, logging
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCLAIMER

logger     = logging.getLogger(__name__)
TGAPI      = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def send_message(text, chat_id=None, parse_mode="HTML"):
    if not TELEGRAM_BOT_TOKEN: return False
    target = chat_id or TELEGRAM_CHAT_ID
    if not target: return False
    # Telegram 訊息長度上限 4096 字
    if len(text) > 4000: text = text[:3990] + "..."
    try:
        r = requests.post(f"{TGAPI}/sendMessage",
            json={"chat_id":target,"text":text,"parse_mode":parse_mode,
                  "disable_web_page_preview":True},timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False

def format_signal_message(s):
    dir_emoji = "🟢" if s.get("direction")=="buy" else "🔴"
    dir_zh    = "做多 BUY" if s.get("direction")=="buy" else "做空 SELL"
    score     = s.get("score",0)
    score_bar = "█"*int(score/10) + "░"*(10-int(score/10))
    conds     = "\n".join([f"  ✓ {c}" for c in s.get("conditions_met",[])[:5]])
    macro     = "\n".join([f"  {n}" for n in s.get("macro_notes",[])[:3]])
    costs     = s.get("trading_costs",{})
    sr        = s.get("support_resistance",{})
    fib       = s.get("fibonacci",{})
    ai_rec    = s.get("ai_recommendation","")
    ai_reason = s.get("ai_reason","")
    gen_time  = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")

    # 支撐壓力位資訊
    sr_text = ""
    if sr.get("nearest_resistance"): sr_text += f"  壓力：{sr['nearest_resistance']}\n"
    if sr.get("nearest_support"):    sr_text += f"  支撐：{sr['nearest_support']}\n"

    # Fibonacci
    fib_text = ""
    if fib.get("key_level_618"): fib_text = f"  Fib 61.8%：{fib['key_level_618']}\n"
    elif fib.get("key_level_382"): fib_text = f"  Fib 38.2%：{fib['key_level_382']}\n"

    msg = f"""{dir_emoji} <b>{s.get('emoji','')} {s.get('name',s.get('symbol',''))} — {dir_zh}</b>
━━━━━━━━━━━━━━━━━━

📊 信心：{score}/100  {score_bar}
{s.get('confidence_label','')} ｜ {s.get('action','等待確認')}

📍 <b>進場</b>：<code>{s.get('entry_price','')}</code>（待定單）
🛑 <b>止損 SL</b>：<code>{s.get('stop_loss','')}</code>
🎯 <b>止盈 TP1</b>：<code>{s.get('tp1','')}</code>（1:{s.get('rr1','')}）
🎯 <b>止盈 TP2</b>：<code>{s.get('tp2','')}</code>（1:{s.get('rr2','')}）

💰 手數：<b>{s.get('suggested_lot',0.01)} 手</b>
⚠️ 風險：帳戶 <b>{s.get('risk_pct',0)}%</b>（${ s.get('risk_usd',0)}）
💸 成本：點差{costs.get('spread','')} + 換倉{costs.get('swap_per_day','')}

📋 <b>Mitrade 操作：</b>
1️⃣ 搜尋 {s.get('symbol','')}
2️⃣ 點 {"Buy" if s.get('direction')=='buy' else "Sell"}
3️⃣ 待定單 → {s.get('entry_price','')}
4️⃣ SL：{s.get('stop_loss','')} | TP1：{s.get('tp1','')} | TP2：{s.get('tp2','')}
5️⃣ 手數：{s.get('suggested_lot',0.01)}

✅ <b>通過條件：</b>
{conds}

📍 <b>關鍵位：</b>
{sr_text}{fib_text}
🌍 <b>宏觀：</b>
{macro if macro else '  無特殊宏觀訊號'}
"""
    if ai_rec:
        ai_c = "🟢" if ai_rec=="進場" else "🔴" if ai_rec=="跳過" else "🟡"
        msg += f"\n🤖 <b>AI 建議</b>：{ai_c} {ai_rec}"
        if ai_reason: msg += f" — {ai_reason}"
    msg += f"\n\n⏰ {gen_time} | 時框：{s.get('timeframe','')}\n{DISCLAIMER}"
    return msg.strip()

def format_trump_alert(post):
    return f"""⚡ <b>川普重大發文</b>

📢 <b>原文：</b>
"{post.get('original_text','')}"

🤖 <b>AI 解讀：</b>{post.get('ai_interpretation','')}
📊 <b>影響品種：</b>{', '.join(post.get('affected_assets',[]))}
⚡ <b>衝擊程度：</b>{post.get('impact_level','')}

⚠️ 此為 AI 解讀，非官方確認，請自行判斷。
⏰ {datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')}"""

def format_alert_message(title, body, level="warning"):
    icons = {"warning":"⚠️","danger":"🚨","info":"ℹ️","trump":"⚡","earnings":"📅"}
    return f"{icons.get(level,'⚠️')} <b>{title}</b>\n\n{body}\n\n⏰ {datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')}"

def format_briefing_message(b):
    if not b or not b.get("ai_available"): return "📊 每日簡報生成中..."
    env     = b.get("overall_environment","謹慎交易")
    ei      = "🟢" if env=="適合交易" else "🔴" if env=="今日觀望" else "🟡"
    opps    = "\n".join([f"  • {o}" for o in b.get("best_opportunities",[])])
    avoid   = "\n".join([f"  • {a}" for a in b.get("avoid_today",[])])
    msg = f"""📊 <b>每日市場簡報</b>
━━━━━━━━━━━━━━━━━━

{ei} <b>{env}</b>
{b.get('environment_reason','')}

✅ <b>今日機會：</b>
{opps if opps else '  暫無明確機會'}

⛔ <b>今日避開：</b>
{avoid if avoid else '  無特別需要避開'}

🔥 <b>最大風險：</b>{b.get('top_risk_today','')}
⚡ <b>川普因素：</b>{b.get('trump_impact','')}

⏰ {datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M UTC')}
{DISCLAIMER}"""
    return msg.strip()

def format_stats_message(state):
    """格式化勝率統計訊息"""
    log = getattr(state,'signal_log',[]) if hasattr(state,'signal_log') else []
    if not log: return "📊 <b>訊號統計</b>\n\n尚無歷史記錄，系統需要運行一段時間後才有數據。"
    total   = len(log)
    tp_hits = len([s for s in log if s.get("result") in ["tp1","tp2"]])
    sl_hits = len([s for s in log if s.get("result") == "sl"])
    pending = len([s for s in log if s.get("result") == "pending"])
    win_rate = round(tp_hits/(tp_hits+sl_hits)*100,1) if (tp_hits+sl_hits)>0 else 0
    # 按品種統計
    sym_stats = {}
    for s in log:
        sym = s.get("symbol","")
        if sym not in sym_stats: sym_stats[sym] = {"total":0,"wins":0}
        sym_stats[sym]["total"] += 1
        if s.get("result") in ["tp1","tp2"]: sym_stats[sym]["wins"] += 1
    best_sym = max(sym_stats.items(), key=lambda x:x[1]["wins"]/x[1]["total"] if x[1]["total"]>0 else 0, default=(None,{}))
    return f"""📊 <b>訊號統計報告</b>
━━━━━━━━━━━━━━━━━━

📈 總訊號：{total}
✅ 達到止盈：{tp_hits}（{win_rate}%）
❌ 觸發止損：{sl_hits}
⏳ 待定中：{pending}

🏆 最佳品種：{best_sym[0] or '—'}
🔄 掃描次數：{getattr(state,'scan_count',0)}

⚠️ 勝率統計僅供參考，過去表現不代表未來結果。"""

def format_history_message(state, limit=10):
    """格式化歷史訊號列表"""
    log = list(reversed(getattr(state,'signal_log',[])))[:limit] if hasattr(state,'signal_log') else []
    if not log: return "📋 <b>歷史訊號</b>\n\n尚無記錄"
    lines = ["📋 <b>最近訊號記錄</b>\n"]
    for s in log:
        d    = "🟢" if s.get("direction")=="buy" else "🔴"
        r    = s.get("result","pending")
        ri   = "✅" if r in ["tp1","tp2"] else "❌" if r=="sl" else "⏳"
        date = s.get("generated","")[:10] if s.get("generated") else "—"
        lines.append(f"{d} {s.get('symbol','')} | {ri} {r} | {date}")
    return "\n".join(lines)

def get_updates(offset=None):
    try:
        params = {"timeout":1}
        if offset: params["offset"] = offset
        r = requests.get(f"{TGAPI}/getUpdates", params=params, timeout=5)
        if r.status_code == 200: return r.json().get("result",[])
    except: pass
    return []

def process_command(text, state):
    cmd = text.strip().lower().split()[0] if text.strip() else ""
    snap = state.get_snapshot()
    if cmd in ["/status","狀態"]:
        ss  = snap.get("system_status",{})
        ses = snap.get("market_session",{})
        dl  = ss.get("daily_loss",{})
        return f"""🖥️ <b>系統狀態</b>

⚡ 運行中 v{snap.get('version','4.0')}
📡 時段：{ses.get('session_zh','—')} ({ses.get('taiwan_time','—')})
🔢 有效訊號：{len(snap.get('active_signals',[]))}
🔄 掃描次數：{snap.get('scan_count',0)}
⏰ 上次掃描：{snap.get('last_scan_time','—')}

📊 環境：{ss.get('env_status','—')} ({ss.get('env_score','—')}/100)
😱 VIX：{ss.get('vix','—')}
💭 恐懼貪婪：{ss.get('fg_score','—')}/100 {ss.get('fg_label','')}
💸 今日風險：${dl.get('today_loss',0):.0f} / ${dl.get('max_loss',0):.0f}"""

    elif cmd in ["/signals","訊號"]:
        sigs = snap.get("active_signals",[])
        if not sigs: return "📭 目前沒有有效訊號\n系統每15分鐘自動掃描"
        lines = [f"📋 <b>當前訊號（{len(sigs)}個）</b>\n"]
        for s in sigs[:8]:
            d = "🟢" if s.get("direction")=="buy" else "🔴"
            lines.append(f"{d} {s.get('emoji','')} {s.get('name','')} | {s.get('score',0)}/100 | {s.get('action','')}")
        return "\n".join(lines)

    elif cmd in ["/macro","宏觀"]:
        m  = snap.get("macro_data",{})
        fg = m.get("fear_greed",{})
        f  = snap.get("us_futures_data",{})
        fred = snap.get("fred_data",{})
        r  = f"🌍 <b>宏觀數據</b>\n\n"
        if m.get("vix"):   r += f"😱 VIX：{m['vix']['price']:.1f} ({m['vix']['chg']:+.2f}%)\n"
        if m.get("dxy"):   r += f"💵 DXY：{m['dxy']['price']:.2f} ({m['dxy']['chg']:+.2f}%)\n"
        if fg:             r += f"💭 恐懼貪婪：{fg['score']:.0f}/100 {fg['label_zh']}\n"
        if m.get("gold"):  r += f"🥇 黃金：{m['gold']['price']:.0f} ({m['gold']['chg']:+.2f}%)\n"
        if m.get("btc"):   r += f"₿ BTC：{m['btc']['price']:.0f} ({m['btc']['chg']:+.2f}%)\n"
        if fred.get("fed_rate"):    r += f"🏦 Fed利率：{fred['fed_rate']['value']}%\n"
        if fred.get("yield_curve"): r += f"📉 殖利率曲線：{fred['yield_curve']['label_zh']}\n"
        return r.strip()

    elif cmd in ["/history","歷史"]:
        return format_history_message(state)

    elif cmd in ["/stats","統計"]:
        return format_stats_message(state)

    elif cmd in ["/earnings","財報"]:
        ec = snap.get("earnings_calendar",[])
        if not ec: return "📅 <b>財報日曆</b>\n\n未來7天無監控品種財報"
        lines = ["📅 <b>即將財報（7天內）</b>\n"]
        for e in ec[:8]:
            lines.append(f"📌 {e.get('symbol','')} {e.get('name','')} | {e.get('date','')} | EPS預估：{e.get('eps_est','—')}")
        return "\n".join(lines)

    elif cmd in ["/futures","期貨"]:
        fu = snap.get("us_futures_data",{})
        if not fu: return "📈 <b>美股期貨</b>\n\n數據載入中..."
        lines = ["📈 <b>美股期貨（開盤方向）</b>\n"]
        for sym,data in fu.items():
            if sym == "overall": continue
            bias_e = "🟢" if data.get("bias")=="bullish" else "🔴" if data.get("bias")=="bearish" else "⬜"
            lines.append(f"{bias_e} {data.get('name',sym)}：{data.get('chg',0):+.2f}%")
        if fu.get("overall"):
            lines.append(f"\n📊 整體：{fu['overall']['label']}")
        return "\n".join(lines)

    elif cmd in ["/help","說明","幫助"]:
        return """📖 <b>指令說明</b>

/status    — 系統狀態
/signals   — 當前訊號
/macro     — 宏觀數據
/history   — 歷史訊號
/stats     — 勝率統計
/earnings  — 財報日曆
/futures   — 美股期貨
/help      — 本說明

💡 也可輸入中文：
「狀態」「訊號」「宏觀」
「歷史」「統計」「財報」「期貨」"""

    return "❓ 不認識這個指令\n輸入 /help 查看說明"

def check_and_process_commands(state):
    if not TELEGRAM_BOT_TOKEN: return
    try:
        updates = get_updates(offset=getattr(check_and_process_commands,'_offset',None))
        for upd in updates:
            check_and_process_commands._offset = upd.get("update_id",0)+1
            msg  = upd.get("message",{})
            text = msg.get("text","")
            cid  = str(msg.get("chat",{}).get("id",""))
            if text and cid == str(TELEGRAM_CHAT_ID):
                reply = process_command(text, state)
                send_message(reply, chat_id=cid)
    except Exception as e:
        logger.error(f"Command error: {e}")
