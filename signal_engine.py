"""
signal_engine.py v4.0 — 完整訊號引擎
新增：Overnight Swap 成本、點差成本、品種相關性、支撐壓力位進場
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from config import (SYMBOLS, SIGNAL_THRESHOLDS as THRESH, ACCOUNT_BALANCE_USD,
                    MAX_RISK_PER_TRADE, OVERNIGHT_SWAP, TYPICAL_SPREAD)
from indicators import calc_all_indicators

logger = logging.getLogger(__name__)

def calc_trading_costs(symbol, entry_price, hold_days=1):
    """計算交易成本：點差 + Overnight Swap"""
    si       = SYMBOLS.get(symbol,{})
    cat      = si.get("cat","外匯")
    spread   = TYPICAL_SPREAD.get(symbol, entry_price * 0.0002)
    swap_pct = OVERNIGHT_SWAP.get(cat, 0.0003)
    swap_cost = entry_price * swap_pct * hold_days
    return {"spread":round(spread,4),"swap_per_day":round(swap_cost,4),
            "total_1day":round(spread+swap_cost,4),"note":f"點差{spread}+換倉費{swap_cost:.4f}/天"}

def calc_stop_loss(symbol, direction, entry, atr, atr_mult):
    sl = atr * atr_mult
    # 確保止損考慮點差成本
    spread = TYPICAL_SPREAD.get(symbol, entry * 0.0002)
    if direction == "buy":  return round(entry - sl - spread, 6)
    else:                   return round(entry + sl + spread, 6)

def calc_take_profits(direction, entry, stop_loss, costs=None):
    risk = abs(entry - stop_loss)
    if direction == "buy":
        tp1 = round(entry + risk * 1.5, 6)
        tp2 = round(entry + risk * 2.8, 6)
    else:
        tp1 = round(entry - risk * 1.5, 6)
        tp2 = round(entry - risk * 2.8, 6)
    rr1 = round(abs(tp1-entry)/risk,2) if risk else 0
    rr2 = round(abs(tp2-entry)/risk,2) if risk else 0
    return {"tp1":tp1,"tp2":tp2,"rr1":rr1,"rr2":rr2,"risk_amount":round(risk,6)}

def calc_lot_size(entry, stop_loss, symbol, account_usd=None):
    if account_usd is None: account_usd = ACCOUNT_BALANCE_USD
    si       = SYMBOLS.get(symbol,{})
    min_lot  = si.get("min_lot",0.01)
    risk_usd = account_usd * MAX_RISK_PER_TRADE
    sl_dist  = abs(entry - stop_loss)
    if sl_dist <= 0: return {"lot":min_lot,"risk_usd":0,"risk_pct":0}
    cat = si.get("cat","外匯")
    if cat == "外匯":      rpl = sl_dist * 100000
    elif "XAU" in symbol:  rpl = sl_dist * 100
    elif cat == "商品":    rpl = sl_dist * 1000
    elif cat in ["指數","美股"]: rpl = sl_dist * 10
    else:                  rpl = sl_dist * 1
    if rpl <= 0: return {"lot":min_lot,"risk_usd":0,"risk_pct":0}
    sug  = risk_usd / rpl
    steps = max(1, int(sug/min_lot))
    final = round(steps * min_lot, 2)
    final = max(min_lot, min(final, 2.0))
    act_risk = final * rpl
    return {"lot":final,"risk_usd":round(act_risk,2),"risk_pct":round(act_risk/account_usd*100,2)}

def check_multi_timeframe(tf_data):
    ti = calc_all_indicators(tf_data.get("trend",{}))
    mi = calc_all_indicators(tf_data.get("mid",{}))
    ei = calc_all_indicators(tf_data.get("entry",{}))
    if not all([ti.get("valid"),mi.get("valid"),ei.get("valid")]):
        return {"valid":False,"reason":"指標計算失敗","direction":"none"}
    def b2n(b):
        return {"strong_bullish":2,"bullish":1,"neutral":0,"bearish":-1,"strong_bearish":-2}.get(b,0)
    tn,mn,en = b2n(ti["overall_bias"]),b2n(mi["overall_bias"]),b2n(ei["overall_bias"])
    if   tn>0 and mn>0 and en>0: direction,resonance = "buy",True
    elif tn<0 and mn<0 and en<0: direction,resonance = "sell",True
    elif tn>0 and mn>0:          direction,resonance = "buy",False
    elif tn<0 and mn<0:          direction,resonance = "sell",False
    else:                        direction,resonance = "none",False
    score = 0; met = []; fail = []
    # 1. 日線EMA
    if ti.get("ema",{}).get("valid"):
        b = ti["ema"]["bias"]
        if (direction=="buy" and b in ["bullish","strong_bullish"]) or \
           (direction=="sell" and b in ["bearish","strong_bearish"]):
            score += 20; met.append("日線EMA排列")
        else: fail.append("日線EMA不符")
    # 2. 4H RSI
    if mi.get("rsi",{}).get("valid"):
        rv = mi["rsi"]["value"]; rb = mi["rsi"]["bias"]
        if direction=="buy" and rb in ["bullish","bullish_warning"] and rv<70:
            score += 15; met.append(f"4H RSI {rv:.0f} 多頭區")
        elif direction=="sell" and rb in ["bearish","bearish_warning"] and rv>30:
            score += 15; met.append(f"4H RSI {rv:.0f} 空頭區")
        elif rv>70 or rv<30: score -= 10; fail.append(f"4H RSI {rv:.0f} 極端值")
        else: fail.append(f"4H RSI {rv:.0f} 中性")
    # 3. 4H MACD
    if mi.get("macd",{}).get("valid"):
        mb = mi["macd"]["bias"]
        if (direction=="buy" and "bullish" in mb) or (direction=="sell" and "bearish" in mb):
            score += 15; met.append("4H MACD偏多" if direction=="buy" else "4H MACD偏空")
        else: fail.append("4H MACD不符")
    # 4. 1H 布林帶
    if ei.get("bb",{}).get("valid"):
        bs = ei["bb"]["score"]
        if (direction=="buy" and bs>0) or (direction=="sell" and bs<0):
            score += 10; met.append(f"1H布林帶 {ei['bb']['position']}")
        else: fail.append(f"1H布林帶 {ei['bb']['position']}")
    # 5. 1H EMA
    if ei.get("ema",{}).get("valid"):
        eb = ei["ema"]["bias"]
        if (direction=="buy" and "bullish" in eb) or (direction=="sell" and "bearish" in eb):
            score += 15; met.append("1H EMA確認")
        else: fail.append("1H EMA不符")
    # 6. 成交量
    if ei.get("volume",{}).get("valid"):
        vb = ei["volume"]["bias"]
        if vb=="confirm": score += 10; met.append("量能確認")
        elif vb=="weak":  score -= 5;  fail.append("量能萎縮")
    # 7. K線形態（加分項）
    if ei.get("candlestick",{}).get("valid"):
        cp = ei["candlestick"]
        if (direction=="buy" and cp["overall_bias"]=="bullish") or \
           (direction=="sell" and cp["overall_bias"]=="bearish"):
            strongest = cp.get("strongest",{})
            if strongest:
                bonus = min(strongest.get("strength",1)*3, 10)
                score += bonus; met.append(f"K線形態：{strongest.get('name','')}")
    # 8. RSI 背離（額外加分）
    if ei.get("rsi_divergence",{}).get("valid"):
        rd = ei["rsi_divergence"]
        if direction=="buy" and rd.get("bullish_divergence"):
            score += 8; met.append("RSI多頭背離（底部信號）")
        elif direction=="sell" and rd.get("bearish_divergence"):
            score += 8; met.append("RSI空頭背離（頂部信號）")
    # 共振加分
    if resonance: score += 10
    score = max(0, min(100, score))
    return {"valid":True,"direction":direction,"resonance":resonance,
            "score":score,"conditions_met":met,"conditions_fail":fail,
            "trend_bias":ti["overall_bias"],"mid_bias":mi["overall_bias"],
            "entry_bias":ei["overall_bias"],"indicators":{"trend":ti,"mid":mi,"entry":ei}}

def generate_signal(symbol, tf_data, macro_data):
    if not tf_data or not tf_data.get("entry"): return None
    si = SYMBOLS.get(symbol,{})
    if not si or si.get("monitor_only"): return None
    mtf       = check_multi_timeframe(tf_data)
    if not mtf.get("valid"): return None
    direction = mtf.get("direction","none")
    score     = mtf.get("score",0)
    if score < THRESH["min_score"] or direction == "none": return None
    entry_data = tf_data["entry"]
    price      = entry_data.get("current_price",0)
    if not price: return None
    atr_info = mtf["indicators"]["entry"].get("atr",{})
    if not atr_info.get("valid"): return None
    atr      = atr_info["value"]
    atr_mult = si.get("atr_mult",1.5)
    # 支撐壓力位輔助進場定價
    sr = mtf["indicators"]["entry"].get("support_resistance",{})
    e21 = mtf["indicators"]["entry"].get("ema",{}).get("e21", price)
    if direction == "buy":
        # 進場點：取 EMA21 和最近支撐位較高者，但不超過當前價
        sup = sr.get("nearest_support", e21)
        entry_price = round(min(price, max(e21 or price*0.999, sup or price*0.999)), 6)
    else:
        res = sr.get("nearest_resistance", e21)
        entry_price = round(max(price, min(e21 or price*1.001, res or price*1.001)), 6)
    stop_loss = calc_stop_loss(symbol, direction, entry_price, atr, atr_mult)
    # 止損合理性驗證
    sl_pct = abs(entry_price-stop_loss)/entry_price*100
    if sl_pct > 5: return None
    tp_info  = calc_take_profits(direction, entry_price, stop_loss)
    if tp_info["rr1"] < THRESH["min_rr_ratio"]: return None
    lot_info = calc_lot_size(entry_price, stop_loss, symbol)
    costs    = calc_trading_costs(symbol, entry_price)
    # 行動判斷
    dist = abs(price - entry_price)
    if dist <= atr * 0.1:  action,ac = "立刻進場","green"
    elif direction=="buy" and price>entry_price: action,ac = "等待回調至進場點","yellow"
    elif direction=="sell" and price<entry_price: action,ac = "等待反彈至進場點","yellow"
    else:                  action,ac = "等待進場點確認","yellow"
    # 信心等級
    cl = "高信心" if score >= THRESH["high_confidence"] else "中等信心"
    # 宏觀備注
    macro_notes = []
    vix = macro_data.get("vix",{}) if macro_data else {}
    dxy = macro_data.get("dxy",{}) if macro_data else {}
    if vix: macro_notes.append(f"VIX {vix.get('price',0):.0f} {'偏高注意' if vix.get('price',0)>25 else '正常'}")
    if dxy and si.get("cat")=="外匯":
        dc = dxy.get("chg",0)
        if abs(dc) > 0.2: macro_notes.append(f"美元{'走強' if dc>0 else '走弱'} {dc:+.1f}%")
    if si.get("cat")=="商品" and "XAU" in symbol:
        if dxy.get("chg",0) < -0.2: macro_notes.append("✅ 美元走弱，有利黃金")
    # 期貨方向
    futures = macro_data.get("us_futures",{}) if macro_data else {}
    if futures.get("overall"):
        fo = futures["overall"]
        if si.get("cat") in ["指數","美股"]:
            macro_notes.append(f"美股期貨：{fo.get('label','—')} ({fo.get('avg_chg',0):+.2f}%)")
    # 板塊輪動
    sector = macro_data.get("sector_etf",{}) if macro_data else {}
    if sector.get("rotation") and si.get("cat") in ["指數","美股"]:
        macro_notes.append(f"板塊輪動：{sector['rotation']}")
    return {
        "id":            f"{symbol}_{direction}_{int(datetime.now(timezone.utc).timestamp())}",
        "symbol":        symbol,
        "name":          si["name"],
        "category":      si.get("cat",""),
        "emoji":         si.get("emoji","📊"),
        "direction":     direction,
        "direction_zh":  "做多 (Buy)" if direction=="buy" else "做空 (Sell)",
        "current_price": price,
        "entry_price":   entry_price,
        "stop_loss":     stop_loss,
        "tp1":           tp_info["tp1"],
        "tp2":           tp_info["tp2"],
        "rr1":           tp_info["rr1"],
        "rr2":           tp_info["rr2"],
        "risk_amount":   tp_info["risk_amount"],
        "suggested_lot": lot_info["lot"],
        "risk_usd":      lot_info["risk_usd"],
        "risk_pct":      lot_info["risk_pct"],
        "score":         score,
        "confidence_label": cl,
        "action":        action,
        "action_color":  ac,
        "conditions_met":  mtf["conditions_met"],
        "conditions_fail": mtf["conditions_fail"],
        "timeframe":     "日線+4H+1H 共振",
        "macro_notes":   macro_notes,
        "trading_costs": costs,
        "support_resistance": sr,
        "fibonacci":     mtf["indicators"]["entry"].get("fibonacci",{}),
        "atr":           atr,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "expires_at":    None,
        "valid":         True,
    }

# 別名相容
def spread_cost(symbol, price): return calc_trading_costs(symbol, price)
def correlation_check(sym, dir, sigs): from risk_manager import check_correlation_risk; return check_correlation_risk(sym, dir, sigs)
