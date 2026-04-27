""" indicators.py v5.4 — 修正版
根本問題修正：
★ calc_ema_system：移除硬編碼 len<210 限制，改用動態週期判斷
★ calc_all_indicators：數據需求從 30 降至實際可用最低值
★ EMA 使用 config.py 的短週期（5/13/21/50），只需 50 根即可運作
"""
import math, logging
from config import INDICATOR_PARAMS as P

logger = logging.getLogger(__name__)

def ema(prices, period):
    if not prices or len(prices) < period:
        return None
    k = 2 / (period + 1)
    v = prices[0]
    for p in prices[1:]:
        v = p * k + v * (1 - k)
    return round(v, 6)

def ema_series(prices, period):
    if not prices or len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    r = [None] * len(prices)
    r[0] = prices[0]
    for i in range(1, len(prices)):
        r[i] = prices[i] * k + r[i-1] * (1 - k)
    return r

def sma(prices, period):
    if not prices or len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 6)

def stdev(prices, period):
    if not prices or len(prices) < period:
        return None
    sub = prices[-period:]
    m = sum(sub) / period
    return round(math.sqrt(sum((x - m) ** 2 for x in sub) / period), 6)

def calc_ema_system(closes):
    """
    ★ 修正：使用 config 的短週期 EMA（5/13/21/50）
       原本硬編碼需要 210 根，改為只需 ema_trend（50）根即可
    """
    fast   = P.get("ema_fast",  5)
    mid    = P.get("ema_mid",  13)
    slow   = P.get("ema_slow", 21)
    trend  = P.get("ema_trend", 50)

    # ★ 關鍵修正：只需要 trend 週期的根數即可
    min_needed = trend + 5
    if len(closes) < min_needed:
        return {"valid": False, "reason": f"數據不足（需{min_needed}根，有{len(closes)}根）"}

    e_fast  = ema(closes, fast)
    e_mid   = ema(closes, mid)
    e_slow  = ema(closes, slow)
    e_trend = ema(closes, trend)

    if None in [e_fast, e_mid, e_slow, e_trend]:
        return {"valid": False, "reason": "EMA計算失敗"}

    price = closes[-1]

    # 多頭/空頭排列判斷
    if e_fast > e_mid > e_slow > e_trend:
        alignment, bias, score = "完美多頭排列", "bullish", 3
    elif e_fast > e_mid > e_slow:
        alignment, bias, score = "多頭排列", "bullish", 2
    elif e_fast < e_mid < e_slow < e_trend:
        alignment, bias, score = "完美空頭排列", "bearish", -3
    elif e_fast < e_mid < e_slow:
        alignment, bias, score = "空頭排列", "bearish", -2
    else:
        alignment, bias, score = "混亂", "neutral", 0

    # 交叉判斷
    e_fast_p = ema(closes[:-1], fast)
    e_mid_p  = ema(closes[:-1], mid)
    cross = "無交叉"
    if e_fast_p and e_mid_p:
        if e_fast_p < e_mid_p and e_fast > e_mid:
            cross = "金叉（EMA穿越）"
        elif e_fast_p > e_mid_p and e_fast < e_mid:
            cross = "死叉（EMA跌破）"

    return {
        "valid": True,
        "e9":    e_fast,   # 保持舊 key 名稱相容
        "e21":   e_mid,
        "e50":   e_slow,
        "e200":  e_trend,
        "e_fast": e_fast,
        "e_mid":  e_mid,
        "e_slow": e_slow,
        "e_trend": e_trend,
        "alignment": alignment,
        "bias":  bias,
        "score": score,
        "cross": cross,
        "above_e21":  price > e_mid,
        "above_e50":  price > e_slow,
        "above_e200": price > e_trend,
    }

def calc_rsi(closes):
    period = P.get("rsi_period", 9)
    if len(closes) < period + 2:
        return {"valid": False, "reason": "數據不足"}
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-(period + 1 - i + 1)] - closes[-(period + 1 - i)]
        gains.append(abs(d) if d > 0 else 0)
        losses.append(abs(d) if d < 0 else 0)
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    rsi = 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)

    if rsi >= P.get("rsi_overbought", 70):
        status, bias, score = "超買", "bearish_warning", -1
    elif rsi <= P.get("rsi_oversold", 30):
        status, bias, score = "超賣", "bullish_warning", 1
    elif rsi >= P.get("rsi_bull_zone", 45):
        status, bias, score = "多頭健康區", "bullish", 1
    elif rsi <= P.get("rsi_bear_zone", 55):
        status, bias, score = "空頭健康區", "bearish", -1
    else:
        status, bias, score = "中性", "neutral", 0

    return {"valid": True, "value": rsi, "status": status, "bias": bias, "zone": bias, "score": score}

def calc_rsi_divergence(closes, lookback=20):
    if len(closes) < lookback + 20:
        return {"valid": False}
    recent = closes[-lookback:]
    rsi_vals = []
    for i in range(len(closes) - lookback, len(closes)):
        r = calc_rsi(closes[:i+1])
        rsi_vals.append(r.get("value", 50) if r.get("valid") else 50)
    if len(rsi_vals) < lookback:
        return {"valid": False}
    pl = min(recent[-10:]); ph = max(recent[-10:])
    rl = min(rsi_vals[-10:]); rh = max(rsi_vals[-10:])
    ppl = min(recent[:10]); pph = max(recent[:10])
    prl = min(rsi_vals[:10]); prh = max(rsi_vals[:10])
    bull = pl < ppl and rl > prl
    bear = ph > pph and rh < prh
    if bull:   t, s, sc = "多頭背離", "底部反轉信號", 2
    elif bear: t, s, sc = "空頭背離", "頂部反轉信號", -2
    else:      t, s, sc = "無背離", "", 0
    return {"valid": True, "type": t, "signal": s, "score": sc,
            "bullish_divergence": bull, "bearish_divergence": bear}

def calc_macd(closes):
    fast_p   = P.get("macd_fast", 5)
    slow_p   = P.get("macd_slow", 13)
    signal_p = P.get("macd_signal", 4)
    if len(closes) < slow_p + signal_p + 5:
        return {"valid": False, "reason": "數據不足"}
    ef = ema_series(closes, fast_p)
    es = ema_series(closes, slow_p)
    ml = [ef[i] - es[i] for i in range(len(closes)) if ef[i] is not None and es[i] is not None]
    if len(ml) < signal_p:
        return {"valid": False, "reason": "MACD數據不足"}
    sig = ema(ml, signal_p)
    if sig is None:
        return {"valid": False}
    cur = ml[-1]
    hist = round(cur - sig, 6)
    prev_sig = ema(ml[:-1], signal_p)
    prev_hist = ml[-2] - prev_sig if prev_sig and len(ml) >= 2 else 0
    hg = hist > prev_hist
    hp = hist > 0
    if hp and hg:    status, bias, score = "多頭動能增強", "bullish", 2
    elif hp:         status, bias, score = "多頭動能減弱", "bullish_weak", 1
    elif not hg:     status, bias, score = "空頭動能增強", "bearish", -2
    else:            status, bias, score = "空頭動能減弱", "bearish_weak", -1
    cross = "無交叉"
    if prev_sig and len(ml) >= 2:
        ph = ml[-2] - prev_sig
        if ph < 0 and hist > 0:  cross = "MACD金叉"
        elif ph > 0 and hist < 0: cross = "MACD死叉"
    return {"valid": True, "macd": round(cur, 6), "signal": round(sig, 6), "histogram": hist,
            "status": status, "bias": bias, "score": score, "cross": cross, "hist_growing": hg}

def calc_atr(highs, lows, closes):
    period = P.get("atr_period", 10)
    if len(closes) < period + 2:
        return {"valid": False, "reason": "數據不足"}
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
           for i in range(1, len(closes))]
    if len(trs) < period:
        return {"valid": False}
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return {"valid": True, "value": round(atr, 6), "pct": round(atr / closes[-1] * 100, 2), "price": closes[-1]}

def calc_adx(highs, lows, closes, period=None):
    period = period or P.get("adx_period", 10)
    n = len(closes)
    if n < period * 2 + 5:
        return {"valid": False, "reason": "數據不足", "value": 0, "trend": "無趨勢", "strong": False}
    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = highs[i] - highs[i-1]
        dn = lows[i-1] - lows[i]
        pdm = up if (up > dn and up > 0) else 0
        ndm = dn if (dn > up and dn > 0) else 0
        tr_list.append(tr); pdm_list.append(pdm); ndm_list.append(ndm)

    def wilder(data, p):
        s = sum(data[:p]); result = [s]
        for v in data[p:]: s = s - s/p + v; result.append(s)
        return result

    atr14 = wilder(tr_list, period); pdm14 = wilder(pdm_list, period); ndm14 = wilder(ndm_list, period)
    dx_list, pdi_list, ndi_list = [], [], []
    for i in range(len(atr14)):
        a = atr14[i]
        if a == 0: continue
        pdi = 100 * pdm14[i] / a; ndi = 100 * ndm14[i] / a
        pdi_list.append(pdi); ndi_list.append(ndi)
        dsum = pdi + ndi
        dx_list.append(100 * abs(pdi - ndi) / dsum if dsum != 0 else 0)
    if len(dx_list) < period:
        return {"valid": False, "reason": "DX不足", "value": 0, "trend": "無趨勢", "strong": False}
    adx_val = round(sum(dx_list[-period:]) / period, 2)
    pdi_val = round(pdi_list[-1], 2) if pdi_list else 0
    ndi_val = round(ndi_list[-1], 2) if ndi_list else 0
    if adx_val >= 35:   trend, score = "強趨勢", 2
    elif adx_val >= 25: trend, score = "趨勢中", 1
    elif adx_val >= 20: trend, score = "弱趨勢", 0
    else:               trend, score = "無趨勢", -1
    return {"valid": True, "value": adx_val, "pdi": pdi_val, "ndi": ndi_val,
            "trend": trend, "score": score,
            "bias": "bullish" if pdi_val > ndi_val else "bearish", "strong": adx_val >= 25}

def calc_bollinger(closes):
    period = P.get("bb_period", 15)
    if len(closes) < period:
        return {"valid": False}
    ma = sma(closes, period); sd = stdev(closes, period)
    if ma is None or sd is None: return {"valid": False}
    upper = round(ma + P.get("bb_std", 2) * sd, 6)
    lower = round(ma - P.get("bb_std", 2) * sd, 6)
    price = closes[-1]; bw = round((upper - lower) / ma * 100, 2)
    if price >= upper:    pos, bias, score = "突破上軌", "overbought", -1
    elif price <= lower:  pos, bias, score = "突破下軌", "oversold", 1
    elif price > ma:      pos, bias, score = "中軌上方", "bullish", 1
    else:                 pos, bias, score = "中軌下方", "bearish", -1
    return {"valid": True, "upper": upper, "mid": ma, "lower": lower, "price": price,
            "position": pos, "bias": bias, "score": score, "bandwidth": bw}

def calc_support_resistance(highs, lows, closes, lookback=30):
    if len(closes) < lookback:
        lookback = len(closes)
    rh = highs[-lookback:]; rl = lows[-lookback:]; price = closes[-1]
    resistances = [rh[i] for i in range(2, len(rh)-2)
                   if rh[i] > rh[i-1] and rh[i] > rh[i-2] and rh[i] > rh[i+1] and rh[i] > rh[i+2]]
    supports = [rl[i] for i in range(2, len(rl)-2)
                if rl[i] < rl[i-1] and rl[i] < rl[i-2] and rl[i] < rl[i+1] and rl[i] < rl[i+2]]
    above = [r for r in resistances if r > price]
    below = [s for s in supports if s < price]
    nr = min(above, default=None); ns = max(below, default=None)
    return {"valid": True,
            "nearest_resistance": round(nr, 4) if nr else None,
            "nearest_support":    round(ns, 4) if ns else None,
            "all_resistances":    [round(r, 4) for r in sorted(above)[:3]],
            "all_supports":       [round(s, 4) for s in sorted(below, reverse=True)[:3]],
            "distance_to_res":    round((nr - price) / price * 100, 2) if nr else None,
            "distance_to_sup":    round((price - ns) / price * 100, 2) if ns else None}

def calc_fibonacci(highs, lows, closes, lookback=30):
    if len(closes) < lookback:
        lookback = max(10, len(closes))
    rh = highs[-lookback:]; rl = lows[-lookback:]
    sh = max(rh); sl = min(rl); price = closes[-1]; diff = sh - sl
    uptrend = rl.index(sl) < rh.index(sh)
    levels = {f"fib_{int(fib*1000)}": round((sh - diff*fib if uptrend else sl + diff*fib), 4)
              for fib in P.get("fib_levels", [0.236, 0.382, 0.5, 0.618, 0.786])}
    all_lv = list(levels.values())
    above = [l for l in all_lv if l > price]; below = [l for l in all_lv if l < price]
    return {"valid": True, "swing_high": round(sh, 4), "swing_low": round(sl, 4), "uptrend": uptrend,
            "levels": levels, "nearest_above": min(above, default=None),
            "nearest_below": max(below, default=None),
            "key_level_236": levels.get("fib_236"), "key_level_382": levels.get("fib_382"),
            "key_level_618": levels.get("fib_618")}

def calc_candlestick_patterns(opens, highs, lows, closes):
    if len(closes) < 3:
        return {"valid": False}
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    o2, _, _, c2 = opens[-2], highs[-2], lows[-2], closes[-2]
    body = abs(c - o); total = h - l if h > l else 0.0001
    ul = h - max(c, o); ll = min(c, o) - l
    patterns = []
    if ll > 2*body and ul < 0.1*total and c > o:
        patterns.append({"name": "錘子線", "type": "bullish", "strength": 2})
    if ul > 2*body and ll < 0.1*total and c < o:
        patterns.append({"name": "流星", "type": "bearish", "strength": 2})
    if c2 < o2 and c > o and c > o2 and o < c2:
        patterns.append({"name": "多頭吞噬", "type": "bullish", "strength": 3})
    if c2 > o2 and c < o and c < o2 and o > c2:
        patterns.append({"name": "空頭吞噬", "type": "bearish", "strength": 3})
    if body < total * 0.1:
        patterns.append({"name": "十字星", "type": "neutral", "strength": 1})
    if c > o and body > total * 0.7:
        patterns.append({"name": "大陽線", "type": "bullish", "strength": 2})
    if c < o and body > total * 0.7:
        patterns.append({"name": "大陰線", "type": "bearish", "strength": 2})
    bull_cnt = sum(1 for p in patterns if p["type"] == "bullish")
    bear_cnt = sum(1 for p in patterns if p["type"] == "bearish")
    bias = "bullish" if bull_cnt > bear_cnt else "bearish" if bear_cnt > bull_cnt else "neutral"
    strongest = max(patterns, key=lambda x: x["strength"], default=None)
    return {"valid": True, "patterns": patterns, "overall_bias": bias, "strongest": strongest,
            "candlestick_pattern": patterns, "bullish": bull_cnt > 0 and bull_cnt >= bear_cnt,
            "bearish": bear_cnt > 0 and bear_cnt > bull_cnt,
            "name": strongest["name"] if strongest else ""}

def calc_volume_ratio(volumes):
    period = P.get("vol_period", 10)
    if not volumes or len(volumes) < period + 1:
        return {"valid": False}
    avg = sum(volumes[-period-1:-1]) / period
    curr = volumes[-1]
    if avg == 0: return {"valid": False}
    ratio = round(curr / avg, 2)
    if ratio >= 2.0:   status, bias, score = "爆量", "confirm", 2
    elif ratio >= 1.5: status, bias, score = "量增", "confirm", 1
    elif ratio <= 0.5: status, bias, score = "極度萎縮", "weak", -1
    elif ratio <= 0.7: status, bias, score = "量縮", "weak", 0
    else:              status, bias, score = "正常量", "neutral", 0
    return {"valid": True, "ratio": ratio, "current": int(curr), "average": int(avg),
            "status": status, "bias": bias, "score": score}

def calc_all_indicators(data):
    closes  = data.get("closes", [])
    highs   = data.get("highs", [])
    lows    = data.get("lows", [])
    opens   = data.get("opens", [])
    volumes = data.get("volumes", [])

    # ★ 修正：最低需求從 30 降至實際可用值（ema_trend=50 為最大需求）
    min_bars = P.get("ema_trend", 50) + 5
    if len(closes) < min_bars:
        logger.warning(f"[indicators] K線不足：需{min_bars}根，有{len(closes)}根")
        return {"valid": False, "reason": f"K線不足（需{min_bars}根，有{len(closes)}根）"}

    ema_s = calc_ema_system(closes)
    rsi   = calc_rsi(closes)
    macd  = calc_macd(closes)
    atr   = calc_atr(highs, lows, closes)
    bb    = calc_bollinger(closes)
    vol   = calc_volume_ratio(volumes)
    sr    = calc_support_resistance(highs, lows, closes)
    fib   = calc_fibonacci(highs, lows, closes)
    cp    = calc_candlestick_patterns(opens, highs, lows, closes)
    rd    = calc_rsi_divergence(closes)
    adx   = calc_adx(highs, lows, closes)

    # 診斷日誌：幫助偵測哪個指標無效
    for name, ind in [("EMA", ema_s), ("RSI", rsi), ("MACD", macd), ("ATR", atr), ("ADX", adx)]:
        if not ind.get("valid"):
            logger.debug(f"[indicators] {name} 無效: {ind.get('reason','')}")

    total = sum(x.get("score", 0) for x in [ema_s, rsi, macd, bb] if x.get("valid"))
    if total >= 3:    bias = "strong_bullish"
    elif total >= 1:  bias = "bullish"
    elif total <= -3: bias = "strong_bearish"
    elif total <= -1: bias = "bearish"
    else:             bias = "neutral"

    return {
        "valid": True,
        "ema":   ema_s, "rsi": rsi, "macd": macd, "atr": atr, "bb": bb,
        "volume": vol, "support_resistance": sr, "fibonacci": fib,
        "candlestick": cp, "candlestick_pattern": cp,
        "rsi_divergence": rd, "adx": adx,
        "adx_value":  adx.get("value", 0) if adx.get("valid") else 0,
        "adx_trend":  adx.get("trend", "無趨勢"),
        "adx_strong": adx.get("strong", False),
        "total_score": total, "overall_bias": bias,
        "current_price": closes[-1] if closes else None,
    }
