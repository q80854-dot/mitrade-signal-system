"""
indicators.py v4.0 — 技術指標計算
新增：支撐壓力位、K線形態、RSI背離、Fibonacci
"""
import math, logging
from typing import List, Optional, Dict
from config import INDICATOR_PARAMS as P

logger = logging.getLogger(__name__)

# ── 基礎計算 ──────────────────────────────

def ema(prices, period):
    if not prices or len(prices) < period: return None
    k = 2/(period+1); v = prices[0]
    for p in prices[1:]: v = p*k + v*(1-k)
    return round(v, 6)

def ema_series(prices, period):
    if not prices or len(prices) < period: return [None]*len(prices)
    k = 2/(period+1); r = [None]*len(prices); r[0] = prices[0]
    for i in range(1,len(prices)): r[i] = prices[i]*k + r[i-1]*(1-k)
    return r

def sma(prices, period):
    if not prices or len(prices) < period: return None
    return round(sum(prices[-period:])/period, 6)

def stdev(prices, period):
    if not prices or len(prices) < period: return None
    sub = prices[-period:]; m = sum(sub)/period
    return round(math.sqrt(sum((x-m)**2 for x in sub)/period), 6)

# ── EMA 系統 ──────────────────────────────

def calc_ema_system(closes):
    if len(closes) < 210: return {"valid":False,"reason":"數據不足"}
    e9,e21,e50,e200 = ema(closes,P["ema_fast"]),ema(closes,P["ema_mid"]),ema(closes,P["ema_slow"]),ema(closes,P["ema_trend"])
    if None in [e9,e21,e50,e200]: return {"valid":False,"reason":"計算失敗"}
    price = closes[-1]
    if   e9>e21>e50>e200: alignment,bias,score = "完美多頭排列","bullish",3
    elif e9>e21>e50:      alignment,bias,score = "多頭排列","bullish",2
    elif e9<e21<e50<e200: alignment,bias,score = "完美空頭排列","bearish",-3
    elif e9<e21<e50:      alignment,bias,score = "空頭排列","bearish",-2
    else:                 alignment,bias,score = "混亂","neutral",0
    # 交叉檢測
    e9p  = ema(closes[:-1],P["ema_fast"])
    e21p = ema(closes[:-1],P["ema_mid"])
    if e9p and e21p:
        if e9p<e21p and e9>e21:   cross = "金叉（EMA9穿越EMA21）"
        elif e9p>e21p and e9<e21: cross = "死叉（EMA9跌破EMA21）"
        else:                      cross = "無交叉"
    else: cross = "無交叉"
    return {"valid":True,"e9":e9,"e21":e21,"e50":e50,"e200":e200,
            "alignment":alignment,"bias":bias,"score":score,"cross":cross,
            "above_e21":price>e21,"above_e50":price>e50,"above_e200":price>e200}

# ── RSI ──────────────────────────────────

def calc_rsi(closes):
    period = P["rsi_period"]
    if len(closes) < period+2: return {"valid":False,"reason":"數據不足"}
    gains,losses = [],[]
    for i in range(1,period+1):
        d = closes[-(period+1-i+1)] - closes[-(period+1-i)]
        (gains if d>0 else losses).append(abs(d))
        if d<=0: gains.append(0)
        if d>=0: losses.append(0)
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period
    rsi = 100.0 if al==0 else round(100-100/(1+ag/al),2)
    if   rsi >= P["rsi_overbought"]: status,bias,score = "超買","bearish_warning",-1
    elif rsi <= P["rsi_oversold"]:   status,bias,score = "超賣","bullish_warning",1
    elif rsi >= P["rsi_bull_zone"]:  status,bias,score = "多頭健康區","bullish",1
    elif rsi <= P["rsi_bear_zone"]:  status,bias,score = "空頭健康區","bearish",-1
    else:                            status,bias,score = "中性","neutral",0
    return {"valid":True,"value":rsi,"status":status,"bias":bias,"score":score}

# ── RSI 背離 ──────────────────────────────

def calc_rsi_divergence(closes, lookback=20):
    """
    RSI 背離偵測
    多頭背離：價格創新低，RSI 沒創新低 → 底部反轉信號
    空頭背離：價格創新高，RSI 沒創新高 → 頂部反轉信號
    """
    if len(closes) < lookback + 20: return {"valid":False}
    recent_closes = closes[-lookback:]
    # 計算 RSI 序列
    rsi_vals = []
    for i in range(len(closes)-lookback, len(closes)):
        sub = closes[:i+1]
        r   = calc_rsi(sub)
        rsi_vals.append(r.get("value",50) if r.get("valid") else 50)
    if len(rsi_vals) < lookback: return {"valid":False}
    price_low  = min(recent_closes[-10:])
    price_high = max(recent_closes[-10:])
    rsi_low    = min(rsi_vals[-10:])
    rsi_high   = max(rsi_vals[-10:])
    prev_price_low  = min(recent_closes[:10])
    prev_price_high = max(recent_closes[:10])
    prev_rsi_low    = min(rsi_vals[:10])
    prev_rsi_high   = max(rsi_vals[:10])
    bullish_div = price_low < prev_price_low and rsi_low > prev_rsi_low
    bearish_div = price_high > prev_price_high and rsi_high < prev_rsi_high
    if bullish_div:   div_type,signal,score = "多頭背離","底部反轉信號",2
    elif bearish_div: div_type,signal,score = "空頭背離","頂部反轉信號",-2
    else:             div_type,signal,score = "無背離","",0
    return {"valid":True,"type":div_type,"signal":signal,"score":score,
            "bullish_divergence":bullish_div,"bearish_divergence":bearish_div}

# ── MACD ────────────────────────────────

def calc_macd(closes):
    if len(closes) < P["macd_slow"]+P["macd_signal"]+5: return {"valid":False,"reason":"數據不足"}
    ef = ema_series(closes,P["macd_fast"])
    es = ema_series(closes,P["macd_slow"])
    ml = [ef[i]-es[i] for i in range(len(closes)) if ef[i] is not None and es[i] is not None]
    if len(ml) < P["macd_signal"]: return {"valid":False,"reason":"MACD數據不足"}
    sig = ema(ml,P["macd_signal"])
    if sig is None: return {"valid":False}
    cur = ml[-1]; hist = round(cur-sig,6)
    prev_sig = ema(ml[:-1],P["macd_signal"])
    prev_hist = ml[-2]-prev_sig if prev_sig and len(ml)>=2 else 0
    hg = hist > prev_hist; hp = hist > 0
    if hp and hg:   status,bias,score = "多頭動能增強","bullish",2
    elif hp:        status,bias,score = "多頭動能減弱","bullish_weak",1
    elif not hg:    status,bias,score = "空頭動能增強","bearish",-2
    else:           status,bias,score = "空頭動能減弱","bearish_weak",-1
    cross = "無交叉"
    if prev_sig and len(ml)>=2:
        ph = ml[-2]-prev_sig
        if ph<0 and hist>0: cross="MACD金叉"
        elif ph>0 and hist<0: cross="MACD死叉"
    return {"valid":True,"macd":round(cur,6),"signal":round(sig,6),"histogram":hist,
            "status":status,"bias":bias,"score":score,"cross":cross,"hist_growing":hg}

# ── ATR ─────────────────────────────────

def calc_atr(highs, lows, closes):
    period = P["atr_period"]
    if len(closes) < period+2: return {"valid":False,"reason":"數據不足"}
    trs = [max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
    if len(trs) < period: return {"valid":False}
    atr = sum(trs[:period])/period
    for tr in trs[period:]: atr = (atr*(period-1)+tr)/period
    return {"valid":True,"value":round(atr,6),"pct":round(atr/closes[-1]*100,2),"price":closes[-1]}

# ── 布林帶 ──────────────────────────────

def calc_bollinger(closes):
    period = P["bb_period"]
    if len(closes) < period: return {"valid":False}
    ma = sma(closes,period); sd = stdev(closes,period)
    if ma is None or sd is None: return {"valid":False}
    upper = round(ma+P["bb_std"]*sd,6); lower = round(ma-P["bb_std"]*sd,6)
    price = closes[-1]; bw = round((upper-lower)/ma*100,2)
    if   price >= upper: pos,bias,score = "突破上軌","overbought",-1
    elif price <= lower: pos,bias,score = "突破下軌","oversold",1
    elif price > ma:     pos,bias,score = "中軌上方","bullish",1
    else:                pos,bias,score = "中軌下方","bearish",-1
    return {"valid":True,"upper":upper,"mid":ma,"lower":lower,"price":price,
            "position":pos,"bias":bias,"score":score,"bandwidth":bw}

# ── 支撐壓力位 ──────────────────────────

def calc_support_resistance(highs, lows, closes, lookback=50):
    """
    識別關鍵支撐和壓力位
    方法：找出最近 N 根 K 線的重要高低點
    """
    if len(closes) < lookback: return {"valid":False}
    recent_h = highs[-lookback:]
    recent_l = lows[-lookback:]
    price    = closes[-1]
    # 找出高點（壓力位）：局部最大值
    resistances = []
    for i in range(2, len(recent_h)-2):
        if recent_h[i] > recent_h[i-1] and recent_h[i] > recent_h[i-2] and \
           recent_h[i] > recent_h[i+1] and recent_h[i] > recent_h[i+2]:
            resistances.append(recent_h[i])
    # 找出低點（支撐位）：局部最小值
    supports = []
    for i in range(2, len(recent_l)-2):
        if recent_l[i] < recent_l[i-1] and recent_l[i] < recent_l[i-2] and \
           recent_l[i] < recent_l[i+1] and recent_l[i] < recent_l[i+2]:
            supports.append(recent_l[i])
    # 找出最近的支撐和壓力
    above_price = [r for r in resistances if r > price]
    below_price = [s for s in supports    if s < price]
    nearest_res = min(above_price, default=None)
    nearest_sup = max(below_price, default=None)
    # 整數關口（心理價位）
    round_levels = []
    magnitude = 10 ** (len(str(int(price))) - 2)
    for mult in range(-3, 4):
        level = round(price/magnitude) * magnitude + mult * magnitude
        if abs(level-price)/price < 0.02:
            round_levels.append(round(level,4))
    return {
        "valid":        True,
        "nearest_resistance": round(nearest_res,4) if nearest_res else None,
        "nearest_support":    round(nearest_sup,4) if nearest_sup else None,
        "all_resistances":    [round(r,4) for r in sorted(above_price)[:3]],
        "all_supports":       [round(s,4) for s in sorted(below_price,reverse=True)[:3]],
        "round_levels":       round_levels,
        "distance_to_res":    round((nearest_res-price)/price*100,2) if nearest_res else None,
        "distance_to_sup":    round((price-nearest_sup)/price*100,2) if nearest_sup else None,
    }

# ── Fibonacci 回調位 ──────────────────────

def calc_fibonacci(highs, lows, closes, lookback=50):
    """
    計算 Fibonacci 回調位
    找出最近一波大行情的高低點，計算各 Fibonacci 位
    """
    if len(closes) < lookback: return {"valid":False}
    recent_h = highs[-lookback:]
    recent_l = lows[-lookback:]
    swing_high = max(recent_h)
    swing_low  = min(recent_l)
    price      = closes[-1]
    diff       = swing_high - swing_low
    # 判斷趨勢方向（用來決定是從高往低還是從低往高算）
    hi_idx = recent_h.index(swing_high)
    lo_idx = recent_l.index(swing_low)
    uptrend = lo_idx < hi_idx  # 先有低點再有高點 → 上升趨勢
    levels  = {}
    for fib in P["fib_levels"]:
        if uptrend:
            level = swing_high - diff * fib
        else:
            level = swing_low + diff * fib
        levels[f"fib_{int(fib*1000)}"] = round(level, 4)
    # 找出最近的 Fibonacci 位
    all_levels = list(levels.values())
    above = [l for l in all_levels if l > price]
    below = [l for l in all_levels if l < price]
    return {
        "valid":         True,
        "swing_high":    round(swing_high, 4),
        "swing_low":     round(swing_low,  4),
        "uptrend":       uptrend,
        "levels":        levels,
        "nearest_above": min(above, default=None),
        "nearest_below": max(below, default=None),
        "key_level_236": levels.get("fib_236"),
        "key_level_382": levels.get("fib_382"),
        "key_level_618": levels.get("fib_618"),
    }

# ── K 線形態識別 ──────────────────────────

def calc_candlestick_patterns(opens, highs, lows, closes):
    """
    識別常見 K 線形態
    錘子線、吞噬形態、十字星
    """
    if len(closes) < 3: return {"valid":False}
    o,h,l,c   = opens[-1],highs[-1],lows[-1],closes[-1]
    o2,h2,l2,c2 = opens[-2],highs[-2],lows[-2],closes[-2]
    body    = abs(c-o)
    total   = h-l if h>l else 0.0001
    ul_wick = h - max(c,o)
    ll_wick = min(c,o) - l
    patterns = []
    # 錘子線（多頭反轉）
    if ll_wick > 2*body and ul_wick < 0.1*total and c > o:
        patterns.append({"name":"錘子線","type":"bullish","strength":2})
    # 倒錘子/流星（空頭反轉）
    if ul_wick > 2*body and ll_wick < 0.1*total and c < o:
        patterns.append({"name":"流星","type":"bearish","strength":2})
    # 多頭吞噬
    if c2 < o2 and c > o and c > o2 and o < c2:
        patterns.append({"name":"多頭吞噬","type":"bullish","strength":3})
    # 空頭吞噬
    if c2 > o2 and c < o and c < o2 and o > c2:
        patterns.append({"name":"空頭吞噬","type":"bearish","strength":3})
    # 十字星（不確定）
    if body < total*0.1:
        patterns.append({"name":"十字星","type":"neutral","strength":1})
    # 強勢多頭K線（大陽線）
    if c > o and body > total*0.7:
        patterns.append({"name":"大陽線","type":"bullish","strength":2})
    # 強勢空頭K線（大陰線）
    if c < o and body > total*0.7:
        patterns.append({"name":"大陰線","type":"bearish","strength":2})
    overall_bias = "bullish" if sum(1 for p in patterns if p["type"]=="bullish") > sum(1 for p in patterns if p["type"]=="bearish") else \
                   "bearish" if sum(1 for p in patterns if p["type"]=="bearish") > sum(1 for p in patterns if p["type"]=="bullish") else "neutral"
    return {"valid":True,"patterns":patterns,"overall_bias":overall_bias,
            "strongest":max(patterns,key=lambda x:x["strength"],default=None)}

# ── 成交量 ──────────────────────────────

def calc_volume_ratio(volumes):
    period = P["vol_period"]
    if not volumes or len(volumes) < period+1: return {"valid":False}
    avg  = sum(volumes[-period-1:-1])/period
    curr = volumes[-1]
    if avg == 0: return {"valid":False}
    ratio = round(curr/avg,2)
    if   ratio >= 2.0: status,bias,score = "爆量","confirm",2
    elif ratio >= 1.5: status,bias,score = "量增","confirm",1
    elif ratio <= 0.5: status,bias,score = "極度萎縮","weak",-1
    elif ratio <= 0.7: status,bias,score = "量縮","weak",0
    else:              status,bias,score = "正常量","neutral",0
    return {"valid":True,"ratio":ratio,"current":int(curr),"average":int(avg),
            "status":status,"bias":bias,"score":score}

# ── 綜合計算 ─────────────────────────────

def calc_all_indicators(data):
    closes  = data.get("closes",  [])
    highs   = data.get("highs",   [])
    lows    = data.get("lows",    [])
    opens   = data.get("opens",   [])
    volumes = data.get("volumes", [])
    if len(closes) < 30: return {"valid":False,"reason":"K線不足"}
    ema_s = calc_ema_system(closes)
    rsi   = calc_rsi(closes)
    macd  = calc_macd(closes)
    atr   = calc_atr(highs,lows,closes)
    bb    = calc_bollinger(closes)
    vol   = calc_volume_ratio(volumes)
    sr    = calc_support_resistance(highs,lows,closes)
    fib   = calc_fibonacci(highs,lows,closes)
    cp    = calc_candlestick_patterns(opens,highs,lows,closes)
    rd    = calc_rsi_divergence(closes)
    total = sum(x.get("score",0) for x in [ema_s,rsi,macd,bb] if x.get("valid"))
    if   total >= 3:  bias = "strong_bullish"
    elif total >= 1:  bias = "bullish"
    elif total <= -3: bias = "strong_bearish"
    elif total <= -1: bias = "bearish"
    else:             bias = "neutral"
    return {"valid":True,"ema":ema_s,"rsi":rsi,"macd":macd,"atr":atr,"bb":bb,
            "volume":vol,"support_resistance":sr,"fibonacci":fib,
            "candlestick":cp,"rsi_divergence":rd,
            "total_score":total,"overall_bias":bias,"current_price":closes[-1] if closes else None}
