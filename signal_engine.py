"""
signal_engine.py v5.5 — 全面修正版

修正清單：
★ BUG #1：_get_pip_value / _get_contract_size 函數不存在，被 scoring_engine 呼叫會 ImportError
           → 新增這兩個函數
★ BUG #2：calc_lot_size 當 sl_pips 極小（< 0.5）時沿用 1.0，但計算出的 lot 可能為 max_lot → 加強保護
★ BUG #3：generate_signal 的 sl_pips 驗證條件 < cp.sl_min_pips * 0.8 過於寬鬆 → 改為直接驗證
★ BUG #4：check_multi_timeframe 的 score 計算：50 + raw_score*8 最高只有 74（3個時框），
           但 min_score=65 → 只有 "完美共振" 才能過 → 放寬計算
★ BUG #5：_get_cat_params 對 XRPUSD/SOLUSD 等加密品種沒有適配，回傳外匯預設 → 修正
★ BUG #6：calc_take_profits rr2 固定來自 cp，未考慮實際 RR 驗證
★ 新增：generate_signal 加入更詳細的日誌，便於診斷為何不產生訊號
"""
import logging, math
from datetime import datetime, timezone
from typing import Optional, Dict
from config import SYMBOLS, SIGNAL_THRESHOLDS as THRESH, OVERNIGHT_SWAP, ACCOUNT_BALANCE_USD, CB

logger = logging.getLogger(__name__)

# ══ Mitrade 合約規格 ══════════════════════════════════════════
# (contract_size, pip_size, max_leverage, pip_value_per_lot_usd)
MITRADE_SPECS = {
    "EURUSD": (100000, 0.0001, 200, 10.0),
    "GBPUSD": (100000, 0.0001, 200, 10.0),
    "AUDUSD": (100000, 0.0001, 200, 10.0),
    "USDCAD": (100000, 0.0001, 200,  7.7),
    "USDJPY": (100000, 0.01,   200,  9.1),
    "USDCHF": (100000, 0.0001, 200, 10.0),
    "NZDUSD": (100000, 0.0001, 200, 10.0),
    "XAUUSD": (   100, 0.1,   100, 10.0),
    "WTI":    (  1000, 0.01,   50, 10.0),
    "BTCUSD": (     1, 1.0,    2,  1.0),
    "ETHUSD": (     1, 0.1,    2,  0.1),
    "XRPUSD": (     1, 0.0001, 2,  0.0001),
    "SOLUSD": (     1, 0.01,   2,  0.01),
    "BNBUSD": (     1, 0.01,   2,  0.01),
    "LTCUSD": (     1, 0.01,   2,  0.01),
    "US500":  (     1, 0.1,  100,  0.1),
    "NAS100": (     1, 0.1,  100,  0.1),
    "US30":   (     1, 1.0,  100,  1.0),
    "HK50":   (     1, 1.0,   50,  0.13),
    "GER40":  (     1, 0.1,  100,  0.11),
}

def _get_specs(symbol: str) -> tuple:
    """回傳 (contract_size, pip_size, max_leverage, pip_value_per_lot)"""
    if symbol in MITRADE_SPECS:
        return MITRADE_SPECS[symbol]
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if cat == "外匯": return (100000, 0.0001, 200, 10.0)
    if cat == "加密": return (1, 1.0, 2, 1.0)
    if cat == "指數": return (1, 0.1, 100, 0.1)
    if cat == "商品": return (100, 0.1, 100, 10.0)
    return (100000, 0.0001, 100, 10.0)

# ★ BUG #1 修正：新增這兩個函數，供 scoring_engine 呼叫
def _get_pip_value(symbol: str) -> float:
    """回傳每手每 pip 的 USD 損益"""
    return _get_specs(symbol)[3]

def _get_contract_size(symbol: str) -> float:
    """回傳合約規模"""
    return _get_specs(symbol)[0]

# ══ 品種分類參數 ══════════════════════════════════════════════
CATEGORY_PARAMS = {
    "外匯": {
        "min_score": 62, "min_adx": 18,
        "sl_mult": 1.5, "sl_min_pips": 10, "sl_max_pips": 35,
        "tp1_rr": 1.5, "tp2_rr": 2.5, "max_hold_h": 1,
    },
    "商品_黃金": {
        "min_score": 62, "min_adx": 15,
        "sl_mult": 1.5, "sl_min_pips": 80, "sl_max_pips": 600,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
    },
    "商品_WTI": {
        "min_score": 72, "min_adx": 22,
        "sl_mult": 1.5, "sl_min_pips": 30, "sl_max_pips": 200,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2,
    },
    "加密": {
        "min_score": 55, "min_adx": 12,
        "sl_mult": 2.0, "sl_min_pips": 200, "sl_max_pips": 2000,
        "tp1_rr": 2.0, "tp2_rr": 4.0, "max_hold_h": 4,
        "fg_min_score": 15,
    },
    "指數": {
        "min_score": 60, "min_adx": 15,
        "sl_mult": 1.5, "sl_min_pips": 40, "sl_max_pips": 400,
        "tp1_rr": 1.5, "tp2_rr": 3.0, "max_hold_h": 2, "vix_max": 32,
    },
}

TIER_D_SYMBOLS = {"TSLA", "WTI"}

def _get_cat_params(symbol: str) -> dict:
    """★ BUG #5 修正：加密品種全部映射到 '加密' 參數，不再誤用外匯預設"""
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if symbol == "XAUUSD":
        return CATEGORY_PARAMS["商品_黃金"]
    if symbol == "WTI":
        return CATEGORY_PARAMS["商品_WTI"]
    if cat in CATEGORY_PARAMS:
        return CATEGORY_PARAMS[cat]
    # 加密品種 fallback（XRPUSD/SOLUSD/BNBUSD 等）
    if symbol.endswith("USD") and cat == "":
        # 推斷加密
        known_crypto = {"XRPUSD","SOLUSD","BNBUSD","LTCUSD","ADAUSD","DOTUSD"}
        if symbol in known_crypto or MITRADE_SPECS.get(symbol, (0,0,2,0))[2] <= 2:
            return CATEGORY_PARAMS["加密"]
    return CATEGORY_PARAMS["外匯"]

def calc_lot_size(symbol: str, entry: float, sl: float,
                  balance: float = None, risk_pct: float = None) -> dict:
    """根據 Mitrade 真實合約規格計算手數"""
    balance  = balance  or ACCOUNT_BALANCE_USD
    risk_pct = risk_pct or CB["risk_per_trade_pct"]
    contract_size, pip_size, max_leverage, pip_val = _get_specs(symbol)
    cat = SYMBOLS.get(symbol, {}).get("cat", "")

    max_risk_usd = balance * risk_pct / 100
    sl_dist  = abs(entry - sl)
    sl_pips  = sl_dist / pip_size if pip_size > 0 else 1.0

    # ★ BUG #2 修正：若 sl_pips 過小直接回傳保守值，不計算可能失控的手數
    if sl_pips < 1.0:
        logger.warning(f"[{symbol}] sl_pips={sl_pips:.4f} 異常小，使用保守 lot=0.01")
        return {"lot": 0.01, "risk_usd": round(0.01 * pip_val * 1.0, 2),
                "risk_pct": 0.0, "leverage_used": 0, "recommended_leverage": max_leverage,
                "margin_used": 0, "margin_pct": 0, "warn": "sl_pips_too_small"}

    risk_per_lot = sl_pips * pip_val
    if risk_per_lot <= 0:
        return {"lot": 0.01, "risk_usd": max_risk_usd, "risk_pct": risk_pct,
                "margin_pct": 0, "recommended_leverage": max_leverage}

    raw_lot = max_risk_usd / risk_per_lot

    recommended_leverage = {
        "外匯": 30, "加密": 1, "指數": 20, "商品": 30,
    }.get(cat, 30)
    recommended_leverage = min(recommended_leverage, max_leverage)

    # 保證金不超過帳戶 20%
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

def calc_stop_loss(symbol: str, direction: str, entry: float,
                   atr: float, indicators: dict, cp: dict) -> float:
    """保證最小 pips，避免正常波動被止損"""
    _, pip_size, _, _ = _get_specs(symbol)
    sl_dist  = atr * cp.get("sl_mult", 1.5)
    min_sl   = cp.get("sl_min_pips", 10) * pip_size
    max_sl   = cp.get("sl_max_pips", 35) * pip_size
    sl_dist  = max(sl_dist, min_sl)
    sl_dist  = min(sl_dist, max_sl)

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

    # 再次確認最小距離
    if abs(entry - sl) < min_sl:
        sl = (entry - min_sl) if direction == "buy" else (entry + min_sl)

    pips = abs(entry - sl) / pip_size
    logger.debug(f"[SL] {symbol} {direction} entry={entry:.5f} sl={sl:.5f} dist={pips:.1f}pips")
    return round(sl, 6)

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

def calc_trading_costs(symbol: str, entry: float) -> dict:
    swap_info    = OVERNIGHT_SWAP.get(symbol, {"buy": 0.0, "sell": 0.0})
    spread_pips  = {
        "EURUSD":1.0,"GBPUSD":1.2,"USDJPY":1.0,"AUDUSD":1.2,"USDCAD":1.5,
        "XAUUSD":3.0,"WTI":4.0,"BTCUSD":50.0,"ETHUSD":10.0,
        "US500":0.4,"NAS100":1.0,"US30":2.0,"HK50":5.0,"GER40":1.0,
    }.get(symbol, 2.0)
    _, pip_size, _, _ = _get_specs(symbol)
    return {
        "spread":      round(spread_pips * pip_size, 6),
        "spread_pips": spread_pips,
        "swap_buy":    swap_info.get("buy",  0.0),
        "swap_sell":   swap_info.get("sell", 0.0),
    }

def check_multi_timeframe(tf_data: dict) -> dict:
    """
    超短線：entry=5M，mid=15M，trend=1H
    ★ BUG #4 修正：score 計算上限太低 → 調整公式，三框共振可達 85+
    """
    from indicators import calc_all_indicators
    results = {}
    for tf_key in ["trend", "mid", "entry"]:
        d = tf_data.get(tf_key)
        if d:
            ind = calc_all_indicators(d)
            if ind.get("valid"):
                results[tf_key] = ind

    if not results:
        logger.warning("[MTF] 所有時框指標無效")
        return {"direction": "none", "score": 0, "resonance": False,
                "conditions_met": [], "conditions_fail": []}

    directions = {k: v.get("overall_bias", "neutral") for k, v in results.items()}
    scores     = {k: v.get("total_score", 0) for k, v in results.items()}

    bull_tfs = [k for k, v in directions.items() if "bullish" in v]
    bear_tfs = [k for k, v in directions.items() if "bearish" in v]

    entry_bias     = directions.get("entry", "neutral")
    entry_bullish  = "bullish" in entry_bias
    entry_bearish  = "bearish" in entry_bias

    if len(bull_tfs) >= 2 and entry_bullish:
        direction = "buy"
        raw_score = sum(scores.get(k, 0) for k in bull_tfs)
    elif len(bear_tfs) >= 2 and entry_bearish:
        direction = "sell"
        raw_score = sum(scores.get(k, 0) for k in bear_tfs)
    else:
        direction, raw_score = "none", 0

    resonance = len(bull_tfs) >= 3 or len(bear_tfs) >= 3

    # ★ BUG #4 修正：score 公式調整
    # 原：50 + raw_score*8 + (10 if resonance) → 最高 74（3×3=9）
    # 新：base=55, 每個共振時框+10, resonance+8, 額外動能加分
    n_tfs = len(bull_tfs) if direction == "buy" else len(bear_tfs)
    score = 55 + n_tfs * 10 + (8 if resonance else 0) + max(0, raw_score * 3)
    score = max(0, min(100, score))

    # 條件清單
    conds_met, conds_fail = [], []
    entry_ind = results.get("entry", {})
    rsi_bias  = entry_ind.get("rsi",  {}).get("bias", "")
    ema_bias  = entry_ind.get("ema",  {}).get("bias", "")
    macd_bias = entry_ind.get("macd", {}).get("bias", "")
    adx_val   = entry_ind.get("adx_value", 0)

    for label, val, expect, relevant in [
        ("EMA多頭排列",  ema_bias,  "bullish", direction == "buy"),
        ("EMA空頭排列",  ema_bias,  "bearish", direction == "sell"),
        ("RSI多頭區間",  rsi_bias,  "bullish", direction == "buy"),
        ("RSI空頭區間",  rsi_bias,  "bearish", direction == "sell"),
        ("MACD偏多",    macd_bias, "bullish", direction == "buy"),
        ("MACD偏空",    macd_bias, "bearish", direction == "sell"),
    ]:
        if not relevant: continue
        (conds_met if expect in str(val) else conds_fail).append(label)

    if resonance:
        conds_met.append("三時框共振（1H+15M+5M）✓")
    elif n_tfs == 2:
        conds_met.append("雙時框共振 ✓")

    if adx_val >= 25:
        conds_met.append(f"ADX {adx_val:.0f} 趨勢確認")
    elif adx_val > 0:
        conds_fail.append(f"ADX {adx_val:.0f} 趨勢偏弱")

    cp_data = entry_ind.get("candlestick", {})
    if cp_data.get("valid"):
        if cp_data.get("bullish") and direction == "buy":
            conds_met.append(f"K線：{cp_data.get('name','多頭形態')}")
        elif cp_data.get("bearish") and direction == "sell":
            conds_met.append(f"K線：{cp_data.get('name','空頭形態')}")

    macd_cross = entry_ind.get("macd", {}).get("cross", "")
    if macd_cross == "MACD金叉" and direction == "buy":
        conds_met.append("MACD金叉（5M）")
    elif macd_cross == "MACD死叉" and direction == "sell":
        conds_met.append("MACD死叉（5M）")

    logger.info(f"[MTF] direction={direction} score={score} "
                f"bull={bull_tfs} bear={bear_tfs} resonance={resonance}")

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

def _check_category_filters(symbol, direction, indicators, macro_data, cp):
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if cat == "外匯":
        adx = indicators.get("adx_value", 0)
        if adx < cp.get("min_adx", 18):
            return False, f"ADX {adx:.0f} < {cp.get('min_adx',18)} 趨勢不足"
        return True, ""
    if symbol == "XAUUSD":
        dxy_chg = float(macro_data.get("dxy", {}).get("chg", 0) or 0)
        if direction == "buy" and dxy_chg > 0.8:
            return False, f"黃金多頭：DXY上升 {dxy_chg:.1f}%"
        if direction == "sell" and dxy_chg < -0.8:
            return False, f"黃金空頭：DXY下降 {dxy_chg:.1f}%"
        return True, ""
    if cat == "加密":
        fg = float(macro_data.get("fear_greed", {}).get("score", 50) or 50)
        if direction == "buy" and fg < cp.get("fg_min_score", 15):
            return False, f"F&G {fg:.0f} 極度恐慌"
        return True, ""
    if cat == "指數":
        vix = float(macro_data.get("vix", {}).get("price", 20) or 20)
        if vix > cp.get("vix_max", 32) and direction == "buy":
            return False, f"VIX {vix:.0f} > {cp.get('vix_max',32)} 高恐慌"
        return True, ""
    return True, ""

def generate_signal(symbol: str, tf_data: dict, macro_data: dict) -> Optional[dict]:
    """超短線訊號生成 — 含完整診斷日誌"""
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
            logger.warning(f"[{symbol}] 無 entry 時框數據")
            return None

        indicators = calc_all_indicators(entry_data)
        if not indicators.get("valid"):
            logger.warning(f"[{symbol}] 指標無效：{indicators.get('reason','')}")
            return None

        mtf       = check_multi_timeframe(tf_data)
        direction = mtf.get("direction", "none")
        score     = mtf.get("score", 0)

        if direction == "none":
            logger.info(f"[{symbol}] MTF 無方向（bull={mtf['bull_timeframes']} bear={mtf['bear_timeframes']}）")
            return None

        if score < cp.get("min_score", THRESH["min_score"]):
            logger.info(f"[{symbol}] MTF 分數不足（{score} < {cp.get('min_score')}）")
            return None

        adx_val = indicators.get("adx_value", 0)
        if adx_val < cp.get("min_adx", 18):
            logger.info(f"[{symbol}] ADX 不足（{adx_val:.0f} < {cp.get('min_adx')}）")
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
            logger.warning(f"[{symbol}] ATR={atr} 或 price={price} 為 0")
            return None

        # ── 止損 ──
        sl = calc_stop_loss(symbol, direction, price, atr, indicators, cp)
        sl_pips = abs(price - sl) / pip_size

        # ★ BUG #3 修正：直接驗證而不是寬鬆 0.8 倍
        if sl_pips < cp.get("sl_min_pips", 10):
            logger.warning(f"[{symbol}] SL {sl_pips:.1f}pips < 最小要求 {cp.get('sl_min_pips')}pips")
            return None

        # ── 止盈 ──
        tp1, tp2, rr1, rr2 = calc_take_profits(direction, price, sl, cp)
        if rr1 < THRESH.get("min_rr", 1.3):
            return None

        # ── 手數 ──
        lot_info = calc_lot_size(symbol, price, sl)
        costs    = calc_trading_costs(symbol, price)

        if lot_info["lot"] <= 0:
            return None
        if lot_info.get("risk_pct", 100) > CB["risk_per_trade_pct"] * 1.5:
            logger.warning(f"[{symbol}] 風險 {lot_info['risk_pct']:.2f}% 過高")
            return None

        sr  = indicators.get("support_resistance", {})
        fib = indicators.get("fibonacci", {})

        if score >= 85:   action = "🔥 立刻進場"
        elif score >= 75: action = "✅ 可以進場"
        elif score >= 68: action = "⏳ 等待確認"
        else:             action = "👀 觀察"

        sig_id = f"{symbol}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        logger.info(
            f"[{symbol}] ✅ 訊號生成 {direction} score={score} "
            f"SL={sl_pips:.1f}pips RR=1:{rr1} lot={lot_info['lot']} risk={lot_info['risk_pct']:.2f}%"
        )

        return {
            "id":           sig_id,
            "symbol":       symbol,
            "name":         si.get("name", symbol),
            "emoji":        si.get("emoji", "📊"),
            "category":     cat,
            "direction":    direction,
            "score":        score,
            "action":       action,
            "current_price":  round(price, 6),
            "entry_price":    round(price, 6),
            "stop_loss":      sl,
            "tp1":            tp1,
            "tp2":            tp2,
            "rr1":            rr1,
            "rr2":            rr2,
            "sl_pips":        round(sl_pips, 1),
            "suggested_lot":  lot_info["lot"],
            "risk_usd":       lot_info["risk_usd"],
            "risk_pct":       lot_info["risk_pct"],
            "leverage_used":  lot_info.get("leverage_used", 0),
            "recommended_leverage": lot_info.get("recommended_leverage", 30),
            "margin_used":    lot_info.get("margin_used", 0),
            "margin_pct":     lot_info.get("margin_pct", 0),
            "trading_costs":  costs,
            "support_resistance": sr,
            "fibonacci":      fib,
            "nearest_support":    sr.get("nearest_support"),
            "nearest_resistance": sr.get("nearest_resistance"),
            "conditions_met":  mtf.get("conditions_met", []),
            "conditions_fail": mtf.get("conditions_fail", []),
            "macro_notes":    [],
            "timeframe":      entry_data.get("label", "5分鐘"),
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "adx_value":      indicators.get("adx_value", 0),
            "result":         "pending",
            "pnl":            0,
            "close_price":    None,
            "closed_at":      None,
        }

    except Exception as e:
        logger.error(f"generate_signal {symbol}: {e}", exc_info=True)
        return None
