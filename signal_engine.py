"""
signal_engine.py v5.2
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict
from config import SYMBOLS, SIGNAL_THRESHOLDS as THRESH, OVERNIGHT_SWAP, ACCOUNT_BALANCE_USD, CB

logger = logging.getLogger(__name__)

CATEGORY_PARAMS = {
    "外匯":   {"min_score":65,"min_adx":25,"sl_mult":0.9,"tp1_mult":1.6,"tp2_mult":2.8,"max_hold_days":7},
    "商品_黃金":{"min_score":60,"min_adx":18,"sl_mult":1.3,"tp1_mult":1.8,"tp2_mult":4.0,"max_hold_days":15},
    "商品_WTI":{"min_score":80,"min_adx":35,"sl_mult":1.2,"tp1_mult":1.8,"tp2_mult":3.0,"max_hold_days":8},
    "加密":   {"min_score":55,"min_adx":15,"sl_mult":1.8,"tp1_mult":2.2,"tp2_mult":4.0,"max_hold_days":20,"require_bull_market":True,"fg_min_score":15},
    "指數":   {"min_score":60,"min_adx":18,"sl_mult":1.1,"tp1_mult":1.8,"tp2_mult":3.2,"max_hold_days":14,"require_bull_market":True,"vix_max":32},
    "美股":   {"min_score":62,"min_adx":18,"sl_mult":1.2,"tp1_mult":2.0,"tp2_mult":3.8,"max_hold_days":12,"earnings_blackout":7},
}
TIER_D_SYMBOLS = {"TSLA","WTI","HK50","AUDUSD","USDCAD"}

def _get_cat_params(symbol):
    cat=SYMBOLS.get(symbol,{}).get("cat","")
    if symbol=="XAUUSD": return CATEGORY_PARAMS["商品_黃金"]
    if symbol=="WTI":    return CATEGORY_PARAMS["商品_WTI"]
    return CATEGORY_PARAMS.get(cat,{"min_score":68,"min_adx":20,"sl_mult":1.3,"tp1_mult":2.0,"tp2_mult":3.5,"max_hold_days":12})

def calc_trading_costs(symbol, entry):
    si=SYMBOLS.get(symbol,{}); swap_info=OVERNIGHT_SWAP.get(symbol,{"buy":0.0,"sell":0.0})
    swap_buy=swap_info.get("buy",0.0); swap_sell=swap_info.get("sell",0.0)
    spread_pips={"EURUSD":1.0,"GBPUSD":1.2,"USDJPY":1.0,"AUDUSD":1.2,"USDCAD":1.5,
                 "XAUUSD":3.0,"WTI":4.0,"BTCUSD":50.0,"ETHUSD":10.0,
                 "US500":0.4,"NAS100":1.0,"US30":2.0,"HK50":5.0,"GER40":1.0,
                 "AAPL":0.02,"NVDA":0.05,"TSLA":0.10,"MSFT":0.02,"AMZN":0.05,"GOOGL":0.05
                 }.get(symbol,2.0)
    pip=si.get("pip",0.0001)
    return {"spread":round(spread_pips*pip,6),"spread_pips":spread_pips,
            "swap_buy":swap_buy,"swap_sell":swap_sell,"swap_per_day":abs(swap_buy)}

def calc_lot_size(symbol, entry, sl, balance=None, risk_pct=None):
    balance=balance or ACCOUNT_BALANCE_USD; risk_pct=risk_pct or CB["risk_per_trade_pct"]
    cat=SYMBOLS.get(symbol,{}).get("cat",""); si=SYMBOLS.get(symbol,{}); pip=si.get("pip",0.0001)
    risk_usd=balance*risk_pct/100; sl_dist=abs(entry-sl)
    if sl_dist<1e-10: return {"lot":0.01,"risk_usd":risk_usd,"risk_pct":risk_pct}
    if cat=="外匯":                pip_val=10.0
    elif symbol=="XAUUSD":         pip_val=1.0
    elif symbol=="WTI":            pip_val=10.0
    elif cat in ["加密","指數","美股"]: pip_val=1.0
    else:                          pip_val=1.0
    sl_pips=sl_dist/pip; risk_per_lot=sl_pips*pip_val
    raw_lot=risk_usd/risk_per_lot if risk_per_lot>0 else 0.01
    min_lot=0.1 if cat in ["指數","美股"] else 0.01
    lot=max(min_lot,round(raw_lot/min_lot)*min_lot); lot=min(lot,CB["max_lot"])
    actual_risk=lot*risk_per_lot
    return {"lot":round(lot,2),"risk_usd":round(actual_risk,2),"risk_pct":round(actual_risk/balance*100,2)}

def calc_stop_loss(symbol, direction, entry, atr, indicators, cp):
    sl_dist=min(atr*cp.get("sl_mult",1.3),entry*0.05)
    sr=indicators.get("support_resistance",{})
    if direction=="buy":
        sl=entry-sl_dist; sup=sr.get("nearest_support")
        if sup and sup<entry and sup>sl: sl=sup*0.998
    else:
        sl=entry+sl_dist; res=sr.get("nearest_resistance")
        if res and res>entry and res<sl: sl=res*1.002
    return round(sl,6)

def calc_take_profits(direction, entry, sl, cp):
    risk=abs(entry-sl); tp1_mul=cp.get("tp1_mult",1.8); tp2_mul=cp.get("tp2_mult",3.0)
    if direction=="buy": tp1=entry+risk*tp1_mul; tp2=entry+risk*tp2_mul
    else:                tp1=entry-risk*tp1_mul; tp2=entry-risk*tp2_mul
    return round(tp1,6),round(tp2,6),round(tp1_mul,1),round(tp2_mul,1)

def check_multi_timeframe(tf_data):
    from indicators import calc_all_indicators
    results={}
    for tf_key in ["trend","mid","entry"]:
        d=tf_data.get(tf_key)
        if d:
            ind=calc_all_indicators(d); results[tf_key]=ind
    if not results:
        return {"direction":"none","score":0,"resonance":False,"conditions_met":[],"conditions_fail":[]}
    directions={};scores={}
    for tf_key,ind in results.items():
        if not ind.get("valid"): continue
        directions[tf_key]=ind.get("overall_bias","neutral"); scores[tf_key]=ind.get("total_score",0)
    bull_tfs=[k for k,v in directions.items() if "bullish" in v]
    bear_tfs=[k for k,v in directions.items() if "bearish" in v]
    if len(bull_tfs)>=2:
        direction="buy"; raw_score=sum(scores.get(k,0) for k in bull_tfs)//max(len(bull_tfs),1)
    elif len(bear_tfs)>=2:
        direction="sell"; raw_score=sum(scores.get(k,0) for k in bear_tfs)//max(len(bear_tfs),1)
    else:
        direction="none"; raw_score=0
    resonance=(len(bull_tfs)>=3 or len(bear_tfs)>=3)
    score=max(0,min(100,50+raw_score*8+(10 if resonance else 0)))
    conds_met=[]; conds_fail=[]; entry_ind=results.get("entry",{})
    rsi_bias=entry_ind.get("rsi",{}).get("bias","")
    ema_bias=entry_ind.get("ema",{}).get("bias","")
    macd_bias=entry_ind.get("macd",{}).get("bias","")
    checks=[("EMA多頭排列",ema_bias,"bullish",direction=="buy"),
            ("EMA空頭排列",ema_bias,"bearish",direction=="sell"),
            ("RSI多頭區間",rsi_bias,"bullish",direction=="buy"),
            ("RSI空頭區間",rsi_bias,"bearish",direction=="sell"),
            ("MACD偏多",macd_bias,"bullish",direction=="buy"),
            ("MACD偏空",macd_bias,"bearish",direction=="sell")]
    for label,val,expect,relevant in checks:
        if not relevant: continue
        (conds_met if expect in str(val) else conds_fail).append(label)
    if   len(bull_tfs)==3: conds_met.append("三時框多頭共振")
    elif len(bear_tfs)==3: conds_met.append("三時框空頭共振")
    elif len(bull_tfs)==2: conds_met.append("雙時框多頭共振")
    elif len(bear_tfs)==2: conds_met.append("雙時框空頭共振")
    cp_data=entry_ind.get("candlestick",{})
    if cp_data.get("valid"):
        if cp_data.get("bullish") and direction=="buy":
            conds_met.append(f"K線形態：{cp_data.get('name','多頭形態')}")
        elif cp_data.get("bearish") and direction=="sell":
            conds_met.append(f"K線形態：{cp_data.get('name','空頭形態')}")
    adx_val=entry_ind.get("adx_value",0)
    if adx_val>=25: conds_met.append(f"ADX {adx_val:.0f} 趨勢確認")
    elif adx_val>0: conds_fail.append(f"ADX {adx_val:.0f} 趨勢偏弱")
    return {"direction":direction,"score":score,"resonance":resonance,
            "bull_timeframes":bull_tfs,"bear_timeframes":bear_tfs,
            "conditions_met":conds_met,"conditions_fail":conds_fail,"entry_indicators":entry_ind}

def _check_category_filters(symbol, direction, indicators, macro_data, cp):
    cat=SYMBOLS.get(symbol,{}).get("cat","")
    if cat=="外匯":
        adx=indicators.get("adx_value",0)
        if adx<cp.get("min_adx",20): return False,f"外匯ADX {adx:.0f}<{cp.get('min_adx',20)}，趨勢不足"
        return True,""
    if symbol=="XAUUSD":
        dxy_chg=float(macro_data.get("dxy",{}).get("chg",0) or 0)
        if direction=="buy"  and dxy_chg> 0.5: return False,f"黃金多頭：DXY上升 {dxy_chg:.1f}%"
        if direction=="sell" and dxy_chg<-0.5: return False,f"黃金空頭：DXY下降 {dxy_chg:.1f}%"
        return True,""
    if cat=="加密":
        ema200=indicators.get("ema",{}).get("e200"); price=indicators.get("current_price",0)
        if ema200 and price and price<ema200*0.95: return False,"加密熊市：價格低於200MA 5%以上"
        fg_score=float(macro_data.get("fear_greed",{}).get("score",50) or 50)
        if direction=="buy" and fg_score<cp.get("fg_min_score",25): return False,f"恐懼貪婪 {fg_score:.0f}<25，市場極度恐慌"
        return True,""
    if cat=="指數":
        ema200=indicators.get("ema",{}).get("e200"); price=indicators.get("current_price",0)
        if ema200 and price and price<ema200 and direction=="buy": return False,"指數熊市：價格低於200MA，禁止做多"
        vix=float(macro_data.get("vix",{}).get("price",20) or 20)
        if vix>cp.get("vix_max",30) and direction=="buy": return False,f"VIX {vix:.0f}>30，高恐慌不做多"
        return True,""
    if cat=="美股":
        earnings=macro_data.get("earnings_calendar",[]); today=datetime.now(timezone.utc)
        for e in earnings:
            if e.get("symbol")==symbol:
                try:
                    days_away=abs((datetime.fromisoformat(e.get("date",""))-today).days)
                    if days_away<=cp.get("earnings_blackout",7): return False,f"財報前後{cp.get('earnings_blackout',7)}天禁止交易"
                except: pass
        return True,""
    return True,""

def generate_signal(symbol, tf_data, macro_data):
    try:
        from indicators import calc_all_indicators
        if symbol in TIER_D_SYMBOLS: return None
        cp=dict(_get_cat_params(symbol)); si=SYMBOLS.get(symbol,{}); cat=si.get("cat","")
        entry_data=tf_data.get("entry")
        if not entry_data: return None
        indicators=calc_all_indicators(entry_data)
        if not indicators.get("valid"): return None
        mtf=check_multi_timeframe(tf_data)
        direction=mtf.get("direction","none"); score=mtf.get("score",0)
        if direction=="none": return None
        if score<cp.get("min_score",68): return None
        adx=indicators.get("adx_value",0)
        if adx<cp.get("min_adx",20): return None
        pass_filter,reason=_check_category_filters(symbol,direction,indicators,macro_data,cp)
        if not pass_filter: logger.info(f"  [{symbol}] 類別過濾：{reason}"); return None
        trump_event=macro_data.get("trump_event_type","")
        if trump_event=="tariff_broad":
            cp["sl_mult"]=cp.get("sl_mult",1.0)*0.8; cp["tp1_mult"]=cp.get("tp1_mult",1.8)*0.9
        if trump_event=="crypto_hostile" and cat=="加密": return None
        adx_mult=1.2 if adx>=40 else 1.1 if adx>=30 else 1.0
        cp["sl_mult"]=cp.get("sl_mult",1.0)*adx_mult
        cp["tp1_mult"]=cp.get("tp1_mult",1.8)*(1.05 if adx>=30 else 1.0)
        cp["max_hold_days"]=int(cp.get("max_hold_days",10)*(1.2 if adx>=35 else 1.0))
        atr=indicators.get("atr",{}).get("value",0); price=entry_data.get("current_price",0)
        if not atr or not price: return None
        sl=calc_stop_loss(symbol,direction,price,atr,indicators,cp)
        if abs(price-sl)/price*100>5: return None
        tp1,tp2,rr1,rr2=calc_take_profits(direction,price,sl,cp)
        if rr1<THRESH.get("min_rr",1.3): return None
        lot_info=calc_lot_size(symbol,price,sl)
        costs=calc_trading_costs(symbol,price)
        sr=indicators.get("support_resistance",{}); fib=indicators.get("fibonacci",{})
        if   score>=85: action,action_color="🔥 立刻進場","green"
        elif score>=75: action,action_color="✅ 可以進場","green"
        elif score>=68: action,action_color="⏳ 等待確認","yellow"
        else:           action,action_color="👀 觀察","gray"
        sig_id=f"{symbol}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        return {"id":sig_id,"symbol":symbol,"name":si.get("name",symbol),"emoji":si.get("emoji","📊"),
                "category":cat,"direction":direction,"score":score,"action":action,"action_color":action_color,
                "current_price":round(price,6),"entry_price":round(price,6),"stop_loss":sl,
                "tp1":tp1,"tp2":tp2,"rr1":rr1,"rr2":rr2,"suggested_lot":lot_info["lot"],
                "risk_usd":lot_info["risk_usd"],"risk_pct":lot_info["risk_pct"],
                "trading_costs":costs,"support_resistance":sr,"fibonacci":fib,
                "nearest_support":sr.get("nearest_support"),"nearest_resistance":sr.get("nearest_resistance"),
                "conditions_met":mtf.get("conditions_met",[]),"conditions_fail":mtf.get("conditions_fail",[]),
                "macro_notes":[],"timeframe":entry_data.get("label",""),
                "generated_at":datetime.now(timezone.utc).isoformat(),"adx_value":adx,
                "category_filter_applied":True}
    except Exception as e:
        logger.error(f"generate_signal {symbol}: {e}"); return None
