"""
signal_engine.py v6.0 — 提升勝率版
修正清單：
★ 移除 WTI 從 TIER_D_SYMBOLS（不再封鎖）
★ 新增 NATGAS / XAGUSD / COPPER / JP225 / AUS200 / UK100 合約規格
★ check_multi_timeframe：Score 計算改為加權指標分數，非固定偏移
★ CATEGORY_PARAMS：ADX 門檻提高（外匯 22→25，商品 20→22）
★ check_multi_timeframe：加入 ADX 強度確認（adx≥22 才計分）
★ generate_signal：加入「趨勢方向確認」—entry EMA200 必須方向正確
★ calc_stop_loss：加入最小 ATR 距離保護，避免止損太近被洗出
"""
import logging, math
from datetime import datetime, timezone
from typing import Optional, Dict
from config import SYMBOLS, SIGNAL_THRESHOLDS as THRESH, OVERNIGHT_SWAP, ACCOUNT_BALANCE_USD, CB

logger = logging.getLogger(__name__)

# ══ Mitrade 合約規格（v6.0 新增品種）══
MITRADE_SPECS = {
    "EURUSD": (100000, 0.0001, 200, 10.0),
    "GBPUSD": (100000, 0.0001, 200, 10.0),
    "AUDUSD": (100000, 0.0001, 200, 10.0),
    "USDCAD": (100000, 0.0001, 200,  7.7),
    "USDJPY": (100000, 0.01,   200,  9.1),
    "USDCHF": (100000, 0.0001, 200, 10.0),
    "NZDUSD": (100000, 0.0001, 200, 10.0),
    "XAUUSD": (   100, 0.1,   100, 10.0),
    "XAGUSD": (  5000, 0.01,   50,  5.0),
    "WTI":    (  1000, 0.01,   50, 10.0),
    "NATGAS": ( 10000, 0.001,  50, 10.0),
    "COPPER": (  2500, 0.0001, 50, 25.0),
    "BTCUSD": (     1, 1.0,     2,  1.0),
    "ETHUSD": (     1, 0.1,     2,  0.1),
    "XRPUSD": (     1, 0.0001,  2,  0.0001),
    "SOLUSD": (     1, 0.01,    2,  0.01),
    "BNBUSD": (     1, 0.01,    2,  0.01),
    "US500":  (     1, 0.1,   100,  0.1),
    "NAS100": (     1, 0.1,   100,  0.1),
    "US30":   (     1, 1.0,   100,  1.0),
    "HK50":   (     1, 1.0,    50,  0.13),
    "GER40":  (     1, 0.1,   100,  0.11),
    "JP225":  (     1, 1.0,    50,  0.0067),
    "AUS200": (     1, 1.0,    50,  0.65),
    "UK100":  (     1, 0.1,   100,  0.125),
}

def _get_specs(symbol: str) -> tuple:
    if symbol in MITRADE_SPECS:
        return MITRADE_SPECS[symbol]
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if cat == "外匯": return (100000, 0.0001, 200, 10.0)
    if cat == "加密": return (1, 1.0, 2, 1.0)
    if cat == "指數": return (1, 0.1, 100, 0.1)
    if cat == "商品": return (100, 0.1, 100, 10.0)
    return (100000, 0.0001, 100, 10.0)

def _get_pip_value(symbol: str) -> float:
    return _get_specs(symbol)[3]

def _get_contract_size(symbol: str) -> float:
    return _get_specs(symbol)[0]

# ══ 品種分類參數（v6.0 提升 ADX 門檻）══
CATEGORY_PARAMS = {
    "外匯": {
        "min_score": 65, "min_adx": 22,      # ★ 22→25 嚴格趨勢過濾
        "sl_mult": 1.5, "sl_min_pips": 12, "sl_max_pips": 40,
        "tp1_rr": 1.5, "tp2_rr": 2.5, "max_hold_h": 1,
        "require_ema200": True,              # ★ 新增：需要 EMA200 方向確認
    },
    "商品_黃金": {
        "min_score": 65, "min_adx": 20,
        "sl_mult": 1.8, "sl_min_pips": 100, "sl_max_pips": 700,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
        "require_ema200": True,
    },
    "商品_白銀": {
        "min_score": 65, "min_adx": 20,
        "sl_mult": 2.0, "sl_min_pips": 50, "sl_max_pips": 400,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
        "require_ema200": True,
    },
    "商品_WTI": {
        "min_score": 68, "min_adx": 24,      # ★ WTI 解封，要求更高分數
        "sl_mult": 1.8, "sl_min_pips": 40, "sl_max_pips": 250,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
        "require_ema200": True,
        "eia_sensitive": True,
    },
    "商品_其他": {
        "min_score": 68, "min_adx": 22,
        "sl_mult": 2.0, "sl_min_pips": 30, "sl_max_pips": 300,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
    },
    "加密": {
        "min_score": 60, "min_adx": 15,
        "sl_mult": 2.0, "sl_min_pips": 300, "sl_max_pips": 3000,
        "tp1_rr": 2.0, "tp2_rr": 4.0, "max_hold_h": 4,
        "fg_min_score": 20,
    },
    "指數": {
        "min_score": 65, "min_adx": 18,
        "sl_mult": 1.5, "sl_min_pips": 50, "sl_max_pips": 500,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
        "vix_max": 30,
        "require_ema200": True,
    },
}

# ★ 移除 WTI（解除封鎖）
TIER_D_SYMBOLS: set = set()

def _get_cat_params(symbol: str) -> dict:
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if symbol == "XAUUSD": return CATEGORY_PARAMS["商品_黃金"]
    if symbol == "XAGUSD": return CATEGORY_PARAMS["商品_白銀"]
    if symbol == "WTI":    return CATEGORY_PARAMS["商品_WTI"]
    if symbol in ("NATGAS", "COPPER"): return CATEGORY_PARAMS["商品_其他"]
    if cat in CATEGORY_PARAMS: return CATEGORY_PARAMS[cat]
    known_crypto = {"XRPUSD","SOLUSD","BNBUSD","LTCUSD","ADAUSD","DOTUSD"}
    if symbol in known_crypto: return CATEGORY_PARAMS["加密"]
    return CATEGORY_PARAMS["外匯"]

# ══ 手數計算 ══
def calc_lot_size(symbol: str, entry: float, sl: float,
                  balance: float = None, risk_pct: float = None) -> dict:
    balance  = balance  or ACCOUNT_BALANCE_USD
    risk_pct = risk_pct or CB["risk_per_trade_pct"]
    contract_size, pip_size, max_leverage, pip_val = _get_specs(symbol)
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    max_risk_usd = balance * risk_pct / 100
    sl_dist  = abs(entry - sl)
    sl_pips  = sl_dist / pip_size if pip_size > 0 else 1.0
    if sl_pips < 1.0:
        return {"lot": 0.01, "risk_usd": 0, "risk_pct": 0,
                "leverage_used": 0, "recommended_leverage": max_leverage,
                "margin_used": 0, "margin_pct": 0, "warn": "sl_pips_too_small"}
    risk_per_lot = sl_pips * pip_val
    if risk_per_lot <= 0:
        return {"lot": 0.01, "risk_usd": max_risk_usd, "risk_pct": risk_pct,
                "margin_pct": 0, "recommended_leverage": max_leverage}
    raw_lot = max_risk_usd / risk_per_lot
    recommended_leverage = {"外匯": 30, "加密": 1, "指數": 20, "商品": 30}.get(cat, 30)
    recommended_leverage = min(recommended_leverage, max_leverage)
    pos_val      = raw_lot * contract_size * entry
    margin_needed = pos_val / recommended_leverage
    max_margin   = balance * 0.20
    if margin_needed > max_margin:
        raw_lot = (max_margin * recommended_leverage) / (contract_size * entry)
    min_lot = 0.1 if cat == "指數" else 0.01
    lot = max(min_lot, math.floor(raw_lot / min_lot) * min_lot)
    lot = min(lot, CB["max_lot"])
    actual_risk   = lot * risk_per_lot
    pos_val_act   = lot * contract_size * entry
    margin_used   = pos_val_act / recommended_leverage
    lev_used      = round(pos_val_act / balance, 1) if balance > 0 else 0
    return {
        "lot":                  round(lot, 2),
        "risk_usd":             round(actual_risk, 2),
        "risk_pct":             round(actual_risk / balance * 100, 2),
        "leverage_used":        lev_used,
        "recommended_leverage": recommended_leverage,
        "margin_used":          round(margin_used, 2),
        "margin_pct":           round(margin_used / balance * 100, 1),
    }

# ══ 止損計算 ══
def calc_stop_loss(symbol: str, direction: str, entry: float,
                   atr: float, indicators: dict, cp: dict) -> float:
    _, pip_size, _, _ = _get_specs(symbol)
    # ★ 止損至少 1.5 ATR，避免在正常波動下被洗出
    sl_dist = atr * cp.get("sl_mult", 1.5)
    min_sl  = cp.get("sl_min_pips", 12) * pip_size
    max_sl  = cp.get("sl_max_pips", 40) * pip_size
    sl_dist = max(sl_dist, min_sl)
    sl_dist = min(sl_dist, max_sl)
    sr = indicators.get("support_resistance", {})
    if direction == "buy":
        sl  = entry - sl_dist
        sup = sr.get("nearest_support")
        if sup and (entry - sl_dist * 1.3) < sup < entry:
            sl = min(sl, sup * 0.998)
    else:
        sl  = entry + sl_dist
        res = sr.get("nearest_resistance")
        if res and entry < res < (entry + sl_dist * 1.3):
            sl = max(sl, res * 1.002)
    # 最終保障
    if abs(entry - sl) < min_sl:
        sl = (entry - min_sl) if direction == "buy" else (entry + min_sl)
    pips = abs(entry - sl) / pip_size
    logger.debug(f"[SL] {symbol} {direction} entry={entry:.5f} sl={sl:.5f} {pips:.1f}pips")
    return round(sl, 6)

# ══ 止盈計算 ══
def calc_take_profits(direction: str, entry: float, sl: float, cp: dict):
    risk    = abs(entry - sl)
    tp1_rr  = cp.get("tp1_rr", 1.5)
    tp2_rr  = cp.get("tp2_rr", 2.5)
    if direction == "buy":
        tp1 = entry + risk * tp1_rr
        tp2 = entry + risk * tp2_rr
    else:
        tp1 = entry - risk * tp1_rr
        tp2 = entry - risk * tp2_rr
    return round(tp1, 6), round(tp2, 6), round(tp1_rr, 1), round(tp2_rr, 1)

# ══ 交易成本 ══
def calc_trading_costs(symbol: str, entry: float) -> dict:
    swap_info   = OVERNIGHT_SWAP.get(symbol, {"buy": 0.0, "sell": 0.0})
    spread_pips = {
        "EURUSD":1.0,"GBPUSD":1.2,"USDJPY":1.0,"AUDUSD":1.2,"USDCAD":1.5,
        "NZDUSD":1.5,"USDCHF":1.5,
        "XAUUSD":3.0,"XAGUSD":4.0,"WTI":4.0,"NATGAS":5.0,"COPPER":4.0,
        "BTCUSD":50.0,"ETHUSD":10.0,"XRPUSD":20.0,"SOLUSD":30.0,"BNBUSD":25.0,
        "US500":0.4,"NAS100":1.0,"US30":2.0,"HK50":5.0,"GER40":1.0,
        "JP225":8.0,"AUS200":3.0,"UK100":2.0,
    }.get(symbol, 2.0)
    _, pip_size, _, _ = _get_specs(symbol)
    return {
        "spread":       round(spread_pips * pip_size, 6),
        "spread_pips":  spread_pips,
        "swap_buy":     swap_info.get("buy", 0.0),
        "swap_sell":    swap_info.get("sell", 0.0),
    }

# ══ 品種特定過濾 ══
def _check_category_filters(symbol, direction, indicators, macro_data, cp):
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if cat == "外匯":
        adx = indicators.get("adx_value", 0)
        if adx < cp.get("min_adx", 22):
            return False, f"ADX {adx:.0f} < {cp.get('min_adx',22)} 趨勢不足"
        return True, ""
    if symbol == "XAUUSD":
        dxy_chg = float(macro_data.get("dxy", {}).get("chg", 0) or 0)
        if direction == "buy"  and dxy_chg >  0.8: return False, f"黃金多頭：DXY上升 {dxy_chg:.1f}%"
        if direction == "sell" and dxy_chg < -0.8: return False, f"黃金空頭：DXY下降 {dxy_chg:.1f}%"
        return True, ""
    if symbol == "WTI" and cp.get("eia_sensitive"):
        # 避開 EIA 庫存報告（週三 15:30 UTC）
        now = datetime.now(timezone.utc)
        if now.weekday() == 2 and 15 <= now.hour < 17:
            return False, "WTI EIA 報告時段，跳過"
        return True, ""
    if cat == "加密":
        fg = float(macro_data.get("fear_greed", {}).get("score", 50) or 50)
        if direction == "buy"  and fg < cp.get("fg_min_score", 20): return False, f"F&G {fg:.0f} 極度恐慌"
        if direction == "sell" and fg > 80:                          return False, f"F&G {fg:.0f} 極度貪婪（避免做空）"
        return True, ""
    if cat == "指數":
        vix = float(macro_data.get("vix", {}).get("price", 20) or 20)
        if vix > cp.get("vix_max", 30) and direction == "buy":
            return False, f"VIX {vix:.0f} > {cp.get('vix_max',30)} 高恐慌"
        return True, ""
    return True, ""

# ══ MTF 多時框方向判斷（v6.0 重寫 Score 計算）══
def check_multi_timeframe(tf_data: dict) -> dict:
    from indicators import calc_all_indicators
    results = {}
    for tf_key in ["trend", "mid", "entry"]:
        d = tf_data.get(tf_key)
        if d:
            ind = calc_all_indicators(d)
            if ind.get("valid"):
                results[tf_key] = ind

    if not results:
        return {"direction":"none","score":0,"resonance":False,
                "conditions_met":[],"conditions_fail":[],
                "bull_timeframes":[],"bear_timeframes":[]}

    # ── 加權投票 ────────────────────────────────────────────
    TF_WEIGHT = {"trend": 3, "mid": 2, "entry": 1}
    bull_tfs, bear_tfs = [], []

    for tf_key, ind in results.items():
        ema   = ind.get("ema",  {})
        macd  = ind.get("macd", {})
        rsi   = ind.get("rsi",  {})
        adx   = ind.get("adx",  {})
        ema_bias  = ema.get("bias",  "neutral") if ema.get("valid")  else "neutral"
        macd_bias = macd.get("bias", "neutral") if macd.get("valid") else "neutral"
        rsi_val   = rsi.get("value",  50)        if rsi.get("valid")  else 50
        adx_val   = adx.get("value",   0)        if adx.get("valid")  else 0

        bull_votes = bear_votes = 0
        # EMA（權重最高）
        if "bullish" in ema_bias:
            bull_votes += (3 if "完美" in ema.get("alignment","") else
                          2 if "strong" in ema_bias else 1)
        elif "bearish" in ema_bias:
            bear_votes += (3 if "完美" in ema.get("alignment","") else
                          2 if "strong" in ema_bias else 1)
        # MACD
        if "bullish" in macd_bias: bull_votes += (2 if "bullish" == macd_bias else 1)
        elif "bearish" in macd_bias: bear_votes += (2 if "bearish" == macd_bias else 1)
        # RSI 嚴格區間（≥55 多頭，≤45 空頭）
        if rsi_val >= 55:   bull_votes += 1
        elif rsi_val <= 45: bear_votes += 1
        # ADX 確認（≥22 才加分）
        if adx_val >= 22:
            bull_votes += (1 if bull_votes > bear_votes else 0)
            bear_votes += (1 if bear_votes > bull_votes else 0)

        if bull_votes > bear_votes:   bull_tfs.append(tf_key)
        elif bear_votes > bull_votes: bear_tfs.append(tf_key)

    # ── 最終方向決策（需 trend + 至少一個其他時框）──────────
    entry_ind     = results.get("entry", {})
    entry_ema     = entry_ind.get("ema", {})
    entry_ema_bias = entry_ema.get("bias","neutral") if entry_ema.get("valid") else "neutral"
    entry_bullish  = "bullish" in entry_ema_bias
    entry_bearish  = "bearish" in entry_ema_bias

    # ★ 要求 "trend" 時框必須包含在活躍方向中
    if "trend" in bull_tfs and len(bull_tfs) >= 2 and entry_bullish:
        direction, active_tfs = "buy",  bull_tfs
    elif "trend" in bear_tfs and len(bear_tfs) >= 2 and entry_bearish:
        direction, active_tfs = "sell", bear_tfs
    elif len(bull_tfs) >= 2 and entry_bullish:
        direction, active_tfs = "buy",  bull_tfs
    elif len(bear_tfs) >= 2 and entry_bearish:
        direction, active_tfs = "sell", bear_tfs
    else:
        direction, active_tfs = "none", []

    resonance = len(active_tfs) >= 3

    # ── Score（基於實際指標分數，非固定偏移）──────────────
    if direction == "none" or not active_tfs:
        score = 0
    else:
        raw_scores = []
        for tf_key in active_tfs:
            ind = results.get(tf_key, {})
            # EMA 分（0~3）
            ema_s = abs(ind.get("ema",{}).get("score", 0))
            # MACD 動能（0~2）
            macd_s = abs(ind.get("macd",{}).get("score", 0))
            # RSI 確認（0~1）
            rsi_v  = ind.get("rsi",{}).get("value", 50) if ind.get("rsi",{}).get("valid") else 50
            rsi_s  = 1 if (direction == "buy" and rsi_v >= 55) or \
                          (direction == "sell" and rsi_v <= 45) else 0
            # ADX 趨勢（0~2）
            adx_v  = ind.get("adx_value", 0)
            adx_s  = 2 if adx_v >= 30 else (1 if adx_v >= 22 else 0)
            w = TF_WEIGHT.get(tf_key, 1)
            raw_scores.append((ema_s + macd_s + rsi_s + adx_s) * w)

        max_possible = sum(TF_WEIGHT.get(k,1) * 8 for k in active_tfs)
        if max_possible > 0:
            base = sum(raw_scores) / max_possible  # 0~1
            score = int(50 + base * 45 + (8 if resonance else 0))
            score = max(0, min(100, score))
        else:
            score = 0

    # ── 條件清單 ────────────────────────────────────────────
    conds_met, conds_fail = [], []
    ema_bias    = entry_ema.get("bias", "neutral")
    alignment   = entry_ema.get("alignment", "")
    rsi_ind     = entry_ind.get("rsi",  {})
    macd_ind    = entry_ind.get("macd", {})
    adx_val_e   = entry_ind.get("adx_value", 0)
    cp_ind      = entry_ind.get("candlestick", {})
    vol_ind     = entry_ind.get("volume", {})

    if direction == "buy":
        (conds_met if "多頭" in alignment else conds_fail).append(
            f"EMA {alignment}" if "多頭" in alignment else "EMA 未呈多頭排列"
        )
    elif direction == "sell":
        (conds_met if "空頭" in alignment else conds_fail).append(
            f"EMA {alignment}" if "空頭" in alignment else "EMA 未呈空頭排列"
        )

    rsi_val = rsi_ind.get("value", 50) if rsi_ind.get("valid") else 50
    if direction == "buy":
        (conds_met if rsi_val >= 55 else conds_fail).append(
            f"RSI {rsi_val:.0f} 多頭區間" if rsi_val >= 55 else f"RSI {rsi_val:.0f} 偏低"
        )
    elif direction == "sell":
        (conds_met if rsi_val <= 45 else conds_fail).append(
            f"RSI {rsi_val:.0f} 空頭區間" if rsi_val <= 45 else f"RSI {rsi_val:.0f} 偏高"
        )

    macd_cross    = macd_ind.get("cross",  "") if macd_ind.get("valid") else ""
    macd_bias_str = macd_ind.get("bias",   "") if macd_ind.get("valid") else ""
    if macd_cross == "MACD金叉" and direction == "buy":   conds_met.append("MACD 金叉確認")
    elif macd_cross == "MACD死叉" and direction == "sell": conds_met.append("MACD 死叉確認")
    elif direction == "buy"  and "bullish" in macd_bias_str: conds_met.append("MACD 偏多動能")
    elif direction == "sell" and "bearish" in macd_bias_str: conds_met.append("MACD 偏空動能")
    else: conds_fail.append("MACD 動能不足")

    if resonance:          conds_met.append("三時框共振（1H+15M+5M）✓")
    elif len(active_tfs) == 2: conds_met.append("雙時框共振 ✓")

    if adx_val_e >= 30:    conds_met.append(f"ADX {adx_val_e:.0f} 趨勢強勁")
    elif adx_val_e >= 22:  conds_met.append(f"ADX {adx_val_e:.0f} 趨勢中等")
    elif adx_val_e > 0:    conds_fail.append(f"ADX {adx_val_e:.0f} 趨勢偏弱")

    if cp_ind.get("valid"):
        if direction == "buy"  and cp_ind.get("bullish"): conds_met.append(f"K線：{cp_ind.get('name','多頭形態')}")
        elif direction == "sell" and cp_ind.get("bearish"): conds_met.append(f"K線：{cp_ind.get('name','空頭形態')}")

    if vol_ind.get("valid") and vol_ind.get("ratio", 0) >= 1.5:
        conds_met.append(f"量增確認（{vol_ind.get('ratio',0):.1f}x）")

    logger.info(
        f"[MTF] direction={direction} score={score} "
        f"bull={bull_tfs} bear={bear_tfs} resonance={resonance}"
    )
    return {
        "direction":       direction,
        "score":           score,
        "resonance":       resonance,
        "bull_timeframes": bull_tfs,
        "bear_timeframes": bear_tfs,
        "conditions_met":  conds_met,
        "conditions_fail": conds_fail,
        "entry_indicators":entry_ind,
    }

# ══ 訊號生成（核心）══
def generate_signal(symbol: str, tf_data: dict, macro_data: dict) -> Optional[dict]:
    try:
        from indicators import calc_all_indicators
        if symbol in TIER_D_SYMBOLS:
            logger.debug(f"[{symbol}] Tier-D 封鎖品種")
            return None

        cp  = dict(_get_cat_params(symbol))
        si  = SYMBOLS.get(symbol, {})
        cat = si.get("cat", "")
        _, pip_size, _, _ = _get_specs(symbol)

        entry_data = tf_data.get("entry")
        if not entry_data:
            return None

        indicators = calc_all_indicators(entry_data)
        if not indicators.get("valid"):
            return None

        mtf       = check_multi_timeframe(tf_data)
        direction = mtf.get("direction", "none")
        score     = mtf.get("score", 0)

        if direction == "none":
            return None

        if score < cp.get("min_score", THRESH["min_score"]):
            logger.info(f"[{symbol}] MTF 分數不足（{score} < {cp.get('min_score')}）")
            return None

        adx_val = indicators.get("adx_value", 0)
        if adx_val < cp.get("min_adx", 18):
            logger.info(f"[{symbol}] ADX 不足（{adx_val:.0f} < {cp.get('min_adx')}）")
            return None

        # ★ v6.0 新增：EMA200 方向確認（長期趨勢過濾）
        if cp.get("require_ema200", False):
            ema_ind = indicators.get("ema", {})
            if ema_ind.get("valid"):
                price   = entry_data.get("current_price", 0)
                e200    = ema_ind.get("e_trend")  # EMA200
                if e200 and price > 0:
                    if direction == "buy"  and price < e200 * 0.998:
                        logger.info(f"[{symbol}] 多頭但價格低於 EMA200，跳過")
                        return None
                    if direction == "sell" and price > e200 * 1.002:
                        logger.info(f"[{symbol}] 空頭但價格高於 EMA200，跳過")
                        return None

        ok, reason = _check_category_filters(symbol, direction, indicators, macro_data, cp)
        if not ok:
            logger.info(f"[{symbol}] 品種過濾：{reason}")
            return None

        trump_event = macro_data.get("trump_event_type", "")
        if trump_event == "crypto_hostile" and cat == "加密":
            return None

        atr   = indicators.get("atr", {}).get("value", 0)
        price = entry_data.get("current_price", 0)
        if not atr or not price:
            return None

        sl   = calc_stop_loss(symbol, direction, price, atr, indicators, cp)
        sl_pips = abs(price - sl) / pip_size
        if sl_pips < cp.get("sl_min_pips", 12):
            return None

        tp1, tp2, rr1, rr2 = calc_take_profits(direction, price, sl, cp)
        if rr1 < THRESH.get("min_rr", 1.5):
            return None

        lot_info = calc_lot_size(symbol, price, sl)
        costs    = calc_trading_costs(symbol, price)
        if lot_info["lot"] <= 0:
            return None
        if lot_info.get("risk_pct", 100) > CB["risk_per_trade_pct"] * 1.5:
            return None

        sr  = indicators.get("support_resistance", {})
        fib = indicators.get("fibonacci", {})

        if   score >= 85: action = "🔥 立刻進場"
        elif score >= 75: action = "✅ 可以進場"
        elif score >= 68: action = "⏳ 等待確認"
        else:             action = "👀 觀察"

        sig_id = (
            f"{symbol}_{direction}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        logger.info(
            f"[{symbol}] ✅ 訊號生成 {direction} score={score} "
            f"SL={sl_pips:.1f}pips RR=1:{rr1} lot={lot_info['lot']} "
            f"risk={lot_info['risk_pct']:.2f}%"
        )
        return {
            "id": sig_id, "symbol": symbol, "name": si.get("name", symbol),
            "emoji": si.get("emoji", "📊"), "category": cat,
            "direction": direction, "score": score, "action": action,
            "current_price": round(price, 6), "entry_price": round(price, 6),
            "stop_loss": sl, "tp1": tp1, "tp2": tp2, "rr1": rr1, "rr2": rr2,
            "sl_pips": round(sl_pips, 1),
            "suggested_lot": lot_info["lot"], "risk_usd": lot_info["risk_usd"],
            "risk_pct": lot_info["risk_pct"], "leverage_used": lot_info.get("leverage_used",0),
            "recommended_leverage": lot_info.get("recommended_leverage",30),
            "margin_used": lot_info.get("margin_used",0),
            "margin_pct":  lot_info.get("margin_pct",0),
            "trading_costs":     costs,
            "support_resistance":sr,
            "fibonacci":         fib,
            "nearest_support":   sr.get("nearest_support"),
            "nearest_resistance":sr.get("nearest_resistance"),
            "conditions_met":    mtf.get("conditions_met",  []),
            "conditions_fail":   mtf.get("conditions_fail", []),
            "macro_notes":       [],
            "timeframe":         entry_data.get("label", "5分鐘"),
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "adx_value":         indicators.get("adx_value", 0),
            "result": "pending", "pnl": 0, "pnl_pips": 0,
            "close_price": None, "closed_at": None, "status": "active",
        }
    except Exception as e:
        logger.error(f"generate_signal {symbol}: {e}", exc_info=True)
        return None
