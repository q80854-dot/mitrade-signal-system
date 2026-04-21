"""
risk_manager.py v4.0 — 完整風控系統
新增：每日虧損追蹤、同時持倉控制、品種相關性、保證金使用率、週末跳空
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from config import (CIRCUIT_BREAKER as CB, SYMBOLS, ACCOUNT_BALANCE_USD,
                    MIN_ACCOUNT_FOR_INDEX, MIN_ACCOUNT_FOR_STOCK,
                    MAX_SIMULTANEOUS_TRADES, MAX_RISK_PER_TRADE,
                    CORRELATION_GROUPS, TYPICAL_SPREAD)

logger = logging.getLogger(__name__)
EIA = {"weekday":2,"hour":14,"minute":30}

# ── VIX 熔斷 ─────────────────────────────

def check_vix_circuit_breaker(macro_data):
    vix = macro_data.get("vix")
    if not vix: return {"triggered":False,"level":"normal","value":0}
    v = vix.get("price",0)
    if v >= CB["vix_extreme"]: return {"triggered":True,"level":"extreme","value":v,
        "message":f"🚨 VIX {v:.1f} 極度恐慌！所有訊號暫停","action":"stop_all"}
    elif v >= CB["vix_threshold"]: return {"triggered":True,"level":"high","value":v,
        "message":f"⚠️ VIX {v:.1f} 偏高，謹慎交易","action":"reduce_confidence"}
    return {"triggered":False,"level":"normal","value":v}

# ── EIA 石油時間 ──────────────────────────

def check_eia_schedule():
    now = datetime.now(timezone.utc)
    if now.weekday() != EIA["weekday"]: return {"near_eia":False}
    et = now.replace(hour=EIA["hour"],minute=EIA["minute"],second=0,microsecond=0)
    ws,we = et-timedelta(minutes=CB["eia_pause_minutes"]),et+timedelta(minutes=CB["eia_pause_minutes"])
    if ws <= now <= we: return {"near_eia":True,"message":"⚠️ EIA 原油報告時間，石油訊號暫停"}
    return {"near_eia":False}

# ── 財報風險 ─────────────────────────────

def check_earnings_risk(symbol, macro_data):
    si = SYMBOLS.get(symbol,{})
    if not si.get("earnings_sensitive"): return {"near_earnings":False}
    now = datetime.now(timezone.utc)
    # 從 macro_data 取得財報日曆
    earnings = macro_data.get("earnings_calendar",[]) if macro_data else []
    for e in earnings:
        if e.get("symbol") == symbol:
            try:
                edate = datetime.strptime(e["date"],"%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_to = (edate-now).days
                if -1 <= days_to <= CB["earnings_pause_days"]:
                    return {"near_earnings":True,"days_to":days_to,
                        "message":f"⚠️ {si.get('name',symbol)} 財報在 {days_to} 天後，個股波動風險高",
                        "score_penalty":-15}
            except: pass
    # 備用：財報季月份判斷
    if now.month in [1,4,7,10] and now.day <= 20:
        return {"near_earnings":True,"message":f"⚠️ {si.get('name',symbol)} 目前為財報季，個股風險較高","score_penalty":-10}
    return {"near_earnings":False}

# ── 帳戶門檻 ─────────────────────────────

def check_account_requirement(symbol):
    si  = SYMBOLS.get(symbol,{})
    min_acc = si.get("min_account",0)
    if min_acc == 0: return {"sufficient":True}
    if ACCOUNT_BALANCE_USD < min_acc:
        return {"sufficient":False,"required":min_acc,"current":ACCOUNT_BALANCE_USD,
            "message":f"⚠️ {si.get('cat','')}建議帳戶 ${min_acc}，目前 ${ACCOUNT_BALANCE_USD:.0f}",
            "warning_only":True}
    return {"sufficient":True}

# ── 每日虧損追蹤 ──────────────────────────

_daily_loss = {"date":"","loss_usd":0.0,"signal_count":0}

def record_signal_loss(loss_usd: float):
    """記錄每筆訊號的潛在損失（用於每日上限追蹤）"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_loss["date"] != today:
        _daily_loss["date"]         = today
        _daily_loss["loss_usd"]     = 0.0
        _daily_loss["signal_count"] = 0
    _daily_loss["loss_usd"]     += loss_usd
    _daily_loss["signal_count"] += 1

def check_daily_loss_limit():
    """檢查今日累計損失是否超過上限"""
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    max_daily = ACCOUNT_BALANCE_USD * 0.06  # 6% 每日上限
    if _daily_loss["date"] != today:
        return {"exceeded":False,"today_loss":0,"max_loss":max_daily,"remaining":max_daily}
    loss = _daily_loss["loss_usd"]
    if loss >= max_daily:
        return {"exceeded":True,"today_loss":round(loss,2),"max_loss":round(max_daily,2),
            "message":f"🔴 今日虧損已達 ${loss:.0f}（上限 ${max_daily:.0f}），今日停止發訊號"}
    return {"exceeded":False,"today_loss":round(loss,2),"max_loss":round(max_daily,2),
            "remaining":round(max_daily-loss,2)}

# ── 同時持倉數量控制 ──────────────────────

def check_max_positions(active_signals: List[dict]) -> dict:
    """確保同時持倉不超過 MAX_SIMULTANEOUS_TRADES"""
    count = len(active_signals)
    if count >= MAX_SIMULTANEOUS_TRADES:
        return {"exceeded":True,"count":count,"max":MAX_SIMULTANEOUS_TRADES,
            "message":f"⚠️ 已有 {count} 個訊號，暫停新增（上限 {MAX_SIMULTANEOUS_TRADES} 個）"}
    return {"exceeded":False,"count":count,"max":MAX_SIMULTANEOUS_TRADES,
            "remaining":MAX_SIMULTANEOUS_TRADES-count}

# ── 品種相關性風控 ────────────────────────

def check_correlation_risk(new_symbol: str, new_direction: str, active_signals: List[dict]) -> dict:
    """
    檢查新訊號和現有持倉是否高度相關
    例如：已做多 EURUSD，不應再做多 GBPUSD（都受美元影響）
    """
    from config import CORRELATION_GROUPS
    for group in CORRELATION_GROUPS:
        if new_symbol not in group: continue
        for sig in active_signals:
            if sig.get("symbol") in group and sig.get("symbol") != new_symbol:
                existing_dir = sig.get("direction","")
                if existing_dir == new_direction:
                    return {"correlated":True,
                        "with_symbol":sig["symbol"],
                        "message":f"⚠️ {new_symbol} 與現有 {sig['symbol']} 高度相關，雙重持倉增加風險",
                        "score_penalty":-10}
    return {"correlated":False}

# ── 週末跳空警告 ──────────────────────────

def check_weekend_gap() -> dict:
    """週五晚間提醒縮減倉位，避免週末跳空"""
    now = datetime.now(timezone.utc)
    if now.weekday() == 4 and now.hour >= 20:
        return {"warning":True,
            "message":"⚠️ 週五收盤前，建議縮減或平倉，避免週末重大事件導致跳空"}
    if now.weekday() == 0 and now.hour < 2:
        return {"warning":True,
            "message":"⚠️ 週一開盤，留意週末期間是否有重大新聞，注意跳空風險"}
    return {"warning":False}

# ── 保證金使用率 ──────────────────────────

def check_margin_usage(active_signals: List[dict]) -> dict:
    """估算當前所有持倉的保證金使用率"""
    total_risk_pct = sum(s.get("risk_pct",0) for s in active_signals)
    total_risk_usd = sum(s.get("risk_usd",0) for s in active_signals)
    if total_risk_pct > 15:
        return {"warning":True,"total_risk_pct":round(total_risk_pct,1),
            "total_risk_usd":round(total_risk_usd,2),
            "message":f"⚠️ 總風險敞口 {total_risk_pct:.1f}%，建議控制在 10% 以下"}
    return {"warning":False,"total_risk_pct":round(total_risk_pct,1),
            "total_risk_usd":round(total_risk_usd,2)}

# ── 異常波動 ─────────────────────────────

def check_price_spike(symbol, tf_data):
    closes = tf_data.get("entry",{}).get("closes",[])
    if len(closes) < 3: return {"spike":False}
    rng = (max(closes[-3:])-min(closes[-3:]))/min(closes[-3:])*100 if min(closes[-3:])>0 else 0
    if rng >= CB["price_spike_pct"]:
        return {"spike":True,"range":round(rng,2),"message":f"⚠️ {symbol} 近期波動 {rng:.1f}%，等待穩定"}
    return {"spike":False}

# ── 主要檢查函數 ──────────────────────────

def run_all_checks(symbol, tf_data, macro_data, active_signals=None):
    if active_signals is None: active_signals = []
    si  = SYMBOLS.get(symbol,{})
    cat = si.get("cat","")
    checks = {
        "vix":        check_vix_circuit_breaker(macro_data),
        "spike":      check_price_spike(symbol, tf_data),
        "account":    check_account_requirement(symbol),
        "earnings":   check_earnings_risk(symbol, macro_data),
        "eia":        check_eia_schedule() if si.get("special_conditions") else {"near_eia":False},
        "daily_loss": check_daily_loss_limit(),
        "max_pos":    check_max_positions(active_signals),
        "weekend":    check_weekend_gap(),
    }
    warnings,blockers,score_adj = [],[],0
    # 致命阻止
    if checks["vix"].get("level") == "extreme": blockers.append(checks["vix"]["message"])
    if checks["eia"].get("near_eia"):             blockers.append(checks["eia"]["message"])
    if checks["spike"].get("spike"):              blockers.append(checks["spike"]["message"])
    if checks["daily_loss"].get("exceeded"):      blockers.append(checks["daily_loss"]["message"])
    if checks["max_pos"].get("exceeded"):         blockers.append(checks["max_pos"]["message"])
    # 警告（降分）
    if checks["vix"].get("level") == "high":  warnings.append(checks["vix"]["message"]); score_adj -= 10
    if not checks["account"].get("sufficient"):warnings.append(checks["account"]["message"]); score_adj -= 20
    if checks["earnings"].get("near_earnings"):warnings.append(checks["earnings"]["message"]); score_adj += checks["earnings"].get("score_penalty",0)
    if checks["weekend"].get("warning"):       warnings.append(checks["weekend"]["message"]); score_adj -= 5
    if cat == "指數" and checks["vix"].get("value",0) > 25: warnings.append("⚠️ VIX偏高，指數風險加倍"); score_adj -= 10
    status = "blocked" if blockers else "warning" if warnings else "clear"
    return {"status":status,"warnings":warnings,"blockers":blockers,
            "checks":checks,"score_adj":score_adj,"can_signal":len(blockers)==0}

def get_system_status(macro_data):
    vix_c = check_vix_circuit_breaker(macro_data)
    fg    = macro_data.get("fear_greed",{})
    vix   = vix_c.get("value",0)
    score = 100
    if   vix >= 40: score -= 60; st = "極度危險"; cl = "red"
    elif vix >= 30: score -= 30; st = "高風險";   cl = "orange"
    elif vix >= 20: score -= 10; st = "正常偏高"; cl = "yellow"
    else:           st = "適合交易"; cl = "green"
    if (fg.get("score",50) < 20 or fg.get("score",50) > 80): score -= 10
    daily = check_daily_loss_limit()
    cat_advice = {}
    for cat in ["外匯","商品","指數","美股","加密"]:
        min_acc = MIN_ACCOUNT_FOR_INDEX if cat == "指數" else MIN_ACCOUNT_FOR_STOCK if cat == "美股" else 0
        if ACCOUNT_BALANCE_USD < min_acc: cat_advice[cat] = "⚠️ 帳戶不足"
        elif score >= 70:  cat_advice[cat] = "✅ 適合交易"
        elif score >= 40:  cat_advice[cat] = "⚠️ 謹慎"
        else:              cat_advice[cat] = "🔴 觀望"
    return {
        "env_score":max(0,score),"env_status":st,"env_color":cl,
        "vix":vix,"vix_status":vix_c.get("level","normal"),
        "fg_score":fg.get("score",50),"fg_label":fg.get("label_zh","中性"),
        "can_trade":score >= 50,"category_advice":cat_advice,
        "daily_loss":daily,
    }
