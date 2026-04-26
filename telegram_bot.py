"""
telegram_bot.py v5.2 — 直覺化推播，直接告訴你買進/賣出
"""
import requests, logging
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCLAIMER

logger = logging.getLogger(__name__)
TGAPI  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

def send_message(text, chat_id=None, parse_mode="HTML"):
    if not TELEGRAM_BOT_TOKEN: return False
    target=chat_id or TELEGRAM_CHAT_ID
    if not target: return False
    if len(text)>4000: text=text[:3990]+"..."
    try:
        r=requests.post(f"{TGAPI}/sendMessage",
            json={"chat_id":target,"text":text,"parse_mode":parse_mode,
                  "disable_web_page_preview":True},timeout=10)
        return r.status_code==200
    except Exception as e:
        logger.error(f"Telegram send: {e}"); return False

def _urgency(score, ai_rec):
    if score>=85 and ai_rec=="進場": return "🔥 立刻行動","現在就可以下單"
    if score>=75: return "✅ 進場訊號","條件齊備，可以進場"
    if score>=65: return "👀 待確認","等下一根K線確認"
    return "📋 觀察中","尚未達進場條件"

def format_signal_message(s):
    dir_    = s.get("direction","buy"); is_buy=dir_=="buy"
    score   = int(s.get("score",0))
    name    = s.get("name",s.get("symbol",""))
    emoji   = s.get("emoji","📊"); symbol=s.get("symbol","")
    entry   = s.get("entry_price",0); sl=s.get("stop_loss",0)
    tp1     = s.get("tp1",0); tp2=s.get("tp2",0); rr1=s.get("rr1",1.5)
    lot     = s.get("suggested_lot",0.01); risk_pct=s.get("risk_pct",2.0)
    risk_usd= s.get("risk_usd",0); ai_rec=s.get("ai_recommendation","")
    ai_rsn  = s.get("ai_reason",""); tf=s.get("timeframe","")
    conds   = s.get("conditions_met",[]); warnings=s.get("risk_warnings",[])
    costs   = s.get("trading_costs",{}); swap=costs.get("swap_per_day",0)
    gen_time= datetime.now(timezone.utc).strftime("%m/%d %H:%M")
    urgency_title,urgency_sub=_urgency(score,ai_rec)
    action_line=(f"📈 <b>買進 BUY — {emoji} {name}</b>" if is_buy else f"📉 <b>賣出 SELL — {emoji} {name}</b>")
    sl_pct=round(abs(entry-sl)/entry*100,2) if entry else 0
    tp1_pct=round(abs(tp1-entry)/entry*100,2) if entry else 0
    tp2_pct=round(abs(tp2-entry)/entry*100,2) if entry else 0
    outcome=f"賺 {tp1_pct}% 或虧 {sl_pct}%，風險報酬 1:{rr1}"
    ai_line=""
    if ai_rec:
        ai_icon={"進場":"✅","等待":"⏳","跳過":"❌"}.get(ai_rec,"—")
        ai_line=f"\n🤖 AI建議：{ai_icon} <b>{ai_rec}</b>"
        if ai_rsn: ai_line+=f"  {ai_rsn}"
    cond_str="  "+"、".join(conds[:3]) if conds else "  分析中"
    warn_str="".join(f"\n⚠️ {w}" for w in warnings[:1])

    return f"""{urgency_title}  <b>{urgency_sub}</b>

{action_line}
━━━━━━━━━━━━━━

🎯 <b>現在要做什麼</b>
→ {'在 Mitrade 搜尋 '+symbol+'，點 Buy' if is_buy else '在 Mitrade 搜尋 '+symbol+'，點 Sell'}
→ 進場價：<code>{entry}</code>
→ 止損（認輸點）：<code>{sl}</code>  <b>-{sl_pct}%</b>
→ 止盈1（獲利目標）：<code>{tp1}</code>  <b>+{tp1_pct}%</b>
→ 止盈2（延伸目標）：<code>{tp2}</code>  <b>+{tp2_pct}%</b>

💰 <b>這筆交易的代價</b>
→ 下單 <b>{lot} 手</b>
→ 最多虧 <b>${risk_usd:.0f}</b>（佔帳戶 {risk_pct}%）
→ {outcome}
→ 過夜費：${swap:.2f}/天{ai_line}

📌 <b>為什麼現在進場</b>
{cond_str}
時框：{tf}{warn_str}

⏰ {gen_time} UTC
<i>僅供參考，盈虧自負</i>""".strip()

def format_trump_alert(post):
    level=post.get("impact_level","low"); assets=post.get("affected_assets",[])
    interp=post.get("ai_interpretation",""); market=post.get("market_impact","neutral")
    detail=post.get("market_impact_detail",{}); topic=post.get("topic","")
    src=post.get("source","Truth Social")
    level_icon={"high":"🚨","medium":"⚠️","low":"ℹ️"}.get(level,"⚠️")
    market_icon={"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(market,"➡️")
    impact_str=""
    if detail:
        for sym,effect in list(detail.items())[:5]:
            color="🟢" if "多" in effect or "↑" in effect else "🔴" if "空" in effect or "↓" in effect else "⚪"
            impact_str+=f"  {color} {sym}：{effect}\n"
    elif assets:
        impact_str="  "+"  ".join(assets[:5])
    return f"""{level_icon} <b>川普重大發文</b>  [{level.upper()}]

📢 <b>{topic.upper() if topic else '市場相關'}</b> · {src}
{market_icon} 市場情緒：<b>{'偏多' if market=='bullish' else '偏空' if market=='bearish' else '中性'}</b>

💬 <b>AI 解讀</b>
{interp}

📊 <b>受影響品種</b>
{impact_str.strip()}

━━━━━━━━━━━━━━
⚠️ AI 解讀僅供參考
⏰ {datetime.now(timezone.utc).strftime("%m/%d %H:%M")} UTC"""

def format_briefing_message(b):
    if not b or not b.get("ai_available"): return "📊 每日簡報生成中..."
    env=b.get("overall_environment","謹慎交易"); reason=b.get("environment_reason","")
    opps=b.get("best_opportunities",[]); avoid=b.get("avoid_today",[])
    risk=b.get("top_risk_today",""); trump=b.get("trump_impact","")
    session=b.get("session_advice",{})
    env_icon={"適合交易":"🟢","謹慎交易":"🟡","今日觀望":"🔴"}.get(env,"🟡")
    opp_str="\n".join([f"  ✅ {o}" for o in opps[:4]]) if opps else "  暫無明確機會"
    avoid_str="\n".join([f"  ❌ {a}" for a in avoid[:3]]) if avoid else "  無"
    sess_str=""
    for s_name,s_key in [("亞洲","asia"),("倫敦","london"),("紐約","newyork")]:
        adv=session.get(s_key,"")
        if adv: sess_str+=f"  <b>{s_name}盤</b>：{adv}\n"
    return f"""📊 <b>每日市場簡報</b>  {datetime.now(timezone.utc).strftime("%Y/%m/%d")}
{'━'*24}

{env_icon} <b>{env}</b>
{reason}

📈 <b>今日機會</b>
{opp_str}

📉 <b>今日避開</b>
{avoid_str}

🔥 <b>最大風險</b>  {risk}
⚡ <b>川普因素</b>  {trump if trump else '無重大發文'}

⏰ <b>各時段建議</b>
{sess_str.strip()}

━━━━━━━━━━━━━━
<i>{DISCLAIMER[:55]}...</i>""".strip()

def format_alert_message(title, body, level="warning"):
    icons={"danger":"🚨","warning":"⚠️","info":"ℹ️","earnings":"📅","trump":"⚡","success":"✅"}
    border={"danger":"━","warning":"─","info":"·"}.get(level,"─")*24
    return f"{icons.get(level,'⚠️')} <b>{title}</b>\n{border}\n\n{body}\n\n⏰ {datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')}"

def format_scan_summary(scan_num, new_signals, active_count, scores, session, vix, regime_zh, win_rate):
    now=datetime.now(timezone.utc).strftime("%H:%M UTC")
    top=sorted(scores.items(),key=lambda x:x[1],reverse=True)[:5]
    medals=["🥇","🥈","🥉","4️⃣","5️⃣"]
    ranking="".join(f"  {medals[i]} {sym:8s} {sc:.0f}\n" for i,(sym,sc) in enumerate(top))
    sig_str="".join(f"  {'🟢' if s.get('direction')=='buy' else '🔴'} {s.get('emoji','')} {s.get('name','')[:10]} · {s.get('score',0):.0f}分\n" for s in new_signals[:3])
    has_signal_line=(f"🎯 <b>{len(new_signals)} 個新訊號！</b>\n{sig_str.strip()}" if new_signals else "⏳ 等待共振中，耐心持有")
    vix_icon="🔴" if float(vix or 20)>=30 else "🟡" if float(vix or 20)>=20 else "🟢"
    return f"""📡 <b>掃描 #{scan_num} 完成</b>  {now}
━━━━━━━━━━━━━━

{has_signal_line}

📊 <b>品種分數排行</b>
{ranking.strip()}

🌐 <b>市場狀態</b>
  時段：{session}
  VIX：{vix_icon} {vix}  機制：{regime_zh}
  勝率：{win_rate:.1f}%  有效訊號：{active_count}

━━━━━━━━━━━━━━
輸入 /signals 查看詳細訊號"""

def format_stats_message(state):
    log=list(reversed(getattr(state,"signal_log",[])[:50]))
    if not log: return "📊 <b>訊號統計</b>\n\n尚無歷史記錄"
    tp=len([s for s in log if s.get("result") in ["tp1","tp2"]])
    sl=len([s for s in log if s.get("result")=="sl"])
    pen=len([s for s in log if s.get("result")=="pending"])
    tot=tp+sl; wr=round(tp/tot*100,1) if tot>0 else 0
    sym_stats={}
    for s in log:
        sym=s.get("symbol","")
        if sym not in sym_stats: sym_stats[sym]={"w":0,"l":0}
        if s.get("result") in ["tp1","tp2"]: sym_stats[sym]["w"]+=1
        elif s.get("result")=="sl": sym_stats[sym]["l"]+=1
    best=max(sym_stats.items(),key=lambda x:x[1]["w"]/(x[1]["w"]+x[1]["l"]) if x[1]["w"]+x[1]["l"]>0 else 0,default=(None,{}))
    wr_line=f"✅ 止盈 {tp} 次  ❌ 止損 {sl} 次  ⏳ 待定 {pen} 次\n勝率 <b>{wr}%</b>"
    return f"""📊 <b>訊號統計報告</b>
━━━━━━━━━━━━━━

{wr_line}
🏆 最佳品種：{best[0] or '—'}
🔄 掃描次數：{getattr(state,'scan_count',0)}

━━━━━━━━━━━━━━
<i>⚠️ 過去表現不代表未來結果</i>"""

def format_history_message(state, limit=10):
    log=list(reversed(getattr(state,"signal_log",[])))[:limit]
    if not log: return "📋 <b>歷史訊號</b>\n\n尚無記錄"
    rm={"tp1":"✅","tp2":"✅","sl":"❌","pending":"⏳","expired":"⏸"}
    lines=["📋 <b>最近訊號</b>\n"]
    for s in log:
        d="🟢" if s.get("direction")=="buy" else "🔴"
        ri=rm.get(s.get("result","pending"),"⏳")
        date=(s.get("generated","")[:10] if s.get("generated") else "—")
        lines.append(f"{d} {ri} {s.get('symbol',''):8s} {s.get('score',0):.0f}分  {date}")
    return "\n".join(lines)

def get_updates(offset=None):
    try:
        params={"timeout":1}
        if offset: params["offset"]=offset
        r=requests.get(f"{TGAPI}/getUpdates",params=params,timeout=5)
        if r.status_code==200: return r.json().get("result",[])
    except: pass
    return []

def process_command(text, state):
    cmd=text.strip().lower().split()[0] if text.strip() else ""
    snap=state.get_snapshot()
    if cmd in ["/status","狀態"]:
        ss=snap.get("system_status",{}); ses=snap.get("market_session",{})
        dl=ss.get("daily_loss",{}); sigs=snap.get("active_signals",[])
        perf=snap.get("performance",{}); regime=snap.get("regime",{})
        vix=ss.get("vix",0); vix_icon="🔴" if float(vix or 0)>=30 else "🟡" if float(vix or 0)>=20 else "🟢"
        env_icon={"適合交易":"🟢","謹慎交易":"🟡","今日觀望":"🔴"}.get(ss.get("env_status",""),"⚪")
        sig_lines="".join(f"  {'🟢' if s.get('direction')=='buy' else '🔴'} {s.get('emoji','')} {s.get('name','')[:10]} {s.get('score',0):.0f}分\n" for s in sigs[:3])
        return f"""🖥️ <b>系統狀態</b>
━━━━━━━━━━━━━━
{env_icon} <b>{ss.get('env_status','—')}</b>  {snap.get('version','')}
⏰ {ses.get('taiwan_time','—')}  {ses.get('session_zh','—')}
🌐 機制：{regime.get('regime_zh','—')}
{vix_icon} VIX：{vix}  F&G：{ss.get('fg_score',50)}/100

📈 訊號：<b>{len(sigs)}</b> 個有效
{sig_lines.strip()}

💰 今日風險：${dl.get('today_loss',0):.0f} / ${dl.get('max_loss',0):.0f}
📊 Sharpe：{perf.get('sharpe','—')}  MaxDD：{perf.get('max_drawdown','—')}%
🔄 掃描：#{snap.get('scan_count',0)}"""

    elif cmd in ["/signals","訊號"]:
        sigs=snap.get("active_signals",[])
        if not sigs: return "📭 <b>目前無訊號</b>\n\n系統每15分鐘自動掃描"
        lines=[f"📋 <b>當前訊號 ({len(sigs)}個)</b>\n"]
        for s in sigs[:6]:
            is_buy=s.get("direction")=="buy"; act="📈 買進" if is_buy else "📉 賣出"
            entry=s.get("entry_price",0); sl=s.get("stop_loss",0); tp1=s.get("tp1",0); rr1=s.get("rr1",1.5)
            ai=s.get("ai_recommendation",""); ai_tag=f"  AI {ai}" if ai else ""
            lines.append(f"{act} <b>{s.get('emoji','')} {s.get('name','')[:12]}</b>{ai_tag}\n   進 <code>{entry}</code>  損 <code>{sl}</code>  盈 <code>{tp1}</code>  1:{rr1}")
        return "\n".join(lines)

    elif cmd in ["/macro","宏觀"]:
        m=snap.get("macro_data",{}); fred=snap.get("fred_data",{}); fg=m.get("fear_greed",{}); dq=m.get("data_quality",{})
        return f"""🌍 <b>宏觀數據</b>
━━━━━━━━━━━━━━
😱 VIX：{m.get('vix',{}).get('price','—')}
💵 DXY：{m.get('dxy',{}).get('price','—')}
💭 F&G：{fg.get('score','—')}/100 {fg.get('label_zh','')}
🥇 黃金：{m.get('gold',{}).get('price','—')}
₿  BTC：{m.get('btc',{}).get('price','—')}
🏦 Fed：{fred.get('fed_rate',{}).get('value','—')}%
📉 殖利率：{fred.get('yield_curve',{}).get('label_zh','—')}

📡 數據品質：{dq.get('quality_pct',0)}% ({dq.get('filled_sources',0)}/{dq.get('total_sources',6)} 來源)"""

    elif cmd in ["/history","歷史"]: return format_history_message(state)
    elif cmd in ["/stats","統計"]:   return format_stats_message(state)

    elif cmd in ["/earnings","財報"]:
        ec=snap.get("earnings_calendar",[])
        if not ec: return "📅 未來7天無監控品種財報"
        lines=["📅 <b>即將財報</b>\n"]
        for e in ec[:6]: lines.append(f"📌 <b>{e.get('symbol','')}</b> {e.get('name','')[:8]}\n   {e.get('date','')} EPS預估：{e.get('eps_est','—')}")
        return "\n".join(lines)

    elif cmd in ["/futures","期貨"]:
        fu=snap.get("us_futures_data",{})
        if not fu: return "📈 數據載入中..."
        lines=["📈 <b>美股期貨</b>\n"]
        for sym,data in fu.items():
            if sym=="overall": continue
            chg=float(data.get("chg",0)); icon="🟢" if chg>0.1 else "🔴" if chg<-0.1 else "⚪"
            lines.append(f"{icon} {data.get('name',sym)[:12]}  {chg:+.2f}%")
        if fu.get("overall"): lines.append(f"\n→ {fu['overall']['label']}")
        return "\n".join(lines)

    elif cmd in ["/help","說明","幫助"]:
        return """📖 <b>指令說明</b>
━━━━━━━━━━━━━━
/status   — 系統狀態總覽
/signals  — 當前訊號
/macro    — 宏觀數據
/history  — 歷史訊號
/stats    — 勝率統計
/earnings — 財報日曆
/futures  — 美股期貨
/help     — 本說明"""

    return "❓ 不認識這個指令\n輸入 /help 查看所有指令"

def check_and_process_commands(state):
    if not TELEGRAM_BOT_TOKEN: return
    try:
        updates=get_updates(offset=getattr(check_and_process_commands,"_offset",None))
        for upd in updates:
            check_and_process_commands._offset=upd.get("update_id",0)+1
            msg=upd.get("message",{}); text=msg.get("text","")
            cid=str(msg.get("chat",{}).get("id",""))
            if text and cid==str(TELEGRAM_CHAT_ID):
                send_message(process_command(text,state),chat_id=cid)
    except Exception as e:
        logger.error(f"Command error: {e}")
