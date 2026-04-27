"""
signal_engine.py v5.3 — 超短線完整修正版
根本問題修正：
  ★ sl_mult 外匯從 0.9 → 1.5（止損至少 1.5x ATR）
  ★ 加入 min_sl_pips 強制最小止損距離
  ★ calc_lot_size 完全重寫：使用 MITRADE_SPECS 正確合約規格
  ★ 超短線參數：小止損+快速進出+嚴格 RR
  ★ 移除所有美股
  ★ 黃金：外匯+黃金主力品種優先
"""
import logging, math
from datetime import datetime, timezone
from typing import Optional, Dict
from config import SYMBOLS, SIGNAL_THRESHOLDS as THRESH, OVERNIGHT_SWAP, ACCOUNT_BALANCE_USD, CB

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# Mitrade 合約規格（官方規格）
# (contract_size, pip_size, max_leverage, pip_value_per_lot_usd)
# pip_value = 每手每 pip 的盈虧（USD）
# ══════════════════════════════════════════════════════════════
MITRADE_SPECS = {
    # 外匯：1手=10萬，1pip=$10（報價幣為USD時）
    "EURUSD": (100000, 0.0001, 200, 10.0),
    "GBPUSD": (100000, 0.0001, 200, 10.0),
    "AUDUSD": (100000, 0.0001, 200, 10.0),
    "USDCAD": (100000, 0.0001, 200,  7.7),  # 需換算（約）
    "USDJPY": (100000, 0.01,   200,  9.1),  # 需換算（約）
    # 商品
    "XAUUSD": (100,    0.1,   100, 10.0),   # 1手=100oz，1pip=$10
    "WTI":    (1000,   0.01,   50, 10.0),   # 1手=1000桶
    # 加密
    "BTCUSD": (1,      1.0,     2,  1.0),   # ★ BTC 1手=1枚，1pip=$1
    "ETHUSD": (1,      0.1,     2,  0.1),
    # 指數
    "US500":  (1,      0.1,   100,  0.1),   # ★ 指數 pip_val=0.1
    "NAS100": (1,      0.1,   100,  0.1),
    "US30":   (1,      1.0,   100,  1.0),
    "HK50":   (1,      1.0,    50,  0.13),
    "GER40":  (1,      0.1,   100,  0.11),
}

def _get_specs(symbol: str) -> tuple:
    """回傳 (contract_size, pip_size, max_leverage, pip_value_per_lot)"""
    if symbol in MITRADE_SPECS:
        return MITRADE_SPECS[symbol]
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if cat == "外匯":   return (100000, 0.0001, 200, 10.0)
    if cat == "加密":   return (1,      1.0,      2,  1.0)
    if cat == "指數":   return (1,      0.1,    100,  0.1)
    if cat == "商品":   return (100,    0.1,    100, 10.0)
    return (100000, 0.0001, 100, 10.0)

# ══════════════════════════════════════════════════════════════
# 超短線品種參數
# ══════════════════════════════════════════════════════════════
CATEGORY_PARAMS = {
    # ★ 外匯超短線：sl_mult=1.5，min_sl_pips=10（5M K棒 ATR 通常 3-8 pips）
    "外匯": {
        "min_score":   65,
        "min_adx":     20,
        "sl_mult":     1.5,        # ★ 原本 0.9 → 1.5
        "sl_min_pips": 10,         # ★ 最小止損 10 pips
        "sl_max_pips": 30,         # ★ 最大止損 30 pips（超短線不需要大止損）
        "tp1_rr":      1.5,        # TP1 = SL × 1.5
        "tp2_rr":      2.5,
        "max_hold_h":  1,          # ★ 最長持倉 1 小時
    },
    # ★ 黃金超短線
    "商品_黃金": {
        "min_score":   62,
        "min_adx":     18,
        "sl_mult":     1.5,
        "sl_min_pips": 100,        # 黃金最小 $1（pip=0.1，100pips=$10）
        "sl_max_pips": 500,        # 黃金最大 $50
        "tp1_rr":      1.5,
        "tp2_rr":      3.0,
        "max_hold_h":  2,
    },
    "商品_WTI": {
        "min_score":   75,
        "min_adx":     25,
        "sl_mult":     1.5,
        "sl_min_pips": 30,
        "sl_max_pips": 150,
        "tp1_rr":      1.5,
        "tp2_rr":      3.0,
        "max_hold_h":  2,
    },
    # 加密：波動大，止損要更寬
    "加密": {
        "min_score":   55,
        "min_adx":     15,
        "sl_mult":     2.0,
        "sl_min_pips": 200,        # BTC 最小 $200
        "sl_max_pips": 1500,
        "tp1_rr":      2.0,
        "tp2_rr":      4.0,
        "max_hold_h":  4,
        "fg_min_score":15,
    },
    # 指數
    "指數": {
        "min_score":   60,
        "min_adx":     18,
        "sl_mult":     1.5,
        "sl_min_pips": 50,         # 指數最小 50 pips
        "sl_max_pips": 300,
        "tp1_rr":      1.5,
        "tp2_rr":      3.0,
        "max_hold_h":  2,
        "vix_max":     32,
    },
}

# 只封鎖真正不適合短線的品種
TIER_D_SYMBOLS = {"TSLA", "WTI"}  # WTI 超短線風險太高，暫時封鎖


def _get_cat_params(symbol: str) -> dict:
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if symbol == "XAUUSD": return CATEGORY_PARAMS["商品_黃金"]
    if symbol == "WTI":    return CATEGORY_PARAMS["商品_WTI"]
    return CATEGORY_PARAMS.get(cat, CATEGORY_PARAMS["外匯"])


def calc_lot_size(symbol: str, entry: float, sl: float,
                  balance: float = None, risk_pct: float = None) -> dict:
    """
    ★ 完整重寫：根據 Mitrade 真實合約規格計算手數
    公式：lot = 風險金額($) ÷ (止損距離pips × pip_value_per_lot)
    限制：保證金不超過帳戶 20%
    """
    balance   = balance  or ACCOUNT_BALANCE_USD
    risk_pct  = risk_pct or CB["risk_per_trade_pct"]

    contract_size, pip_size, max_leverage, pip_val = _get_specs(symbol)
    cat = SYMBOLS.get(symbol, {}).get("cat", "")

    # 1. 允許最大虧損
    max_risk_usd = balance * risk_pct / 100

    # 2. 止損距離（pips）
    sl_dist = abs(entry - sl)
    sl_pips = sl_dist / pip_size if pip_size > 0 else 1.0
    if sl_pips < 0.5:
        logger.warning(f"[{symbol}] sl_pips={sl_pips:.3f} 過小，使用 1")
        sl_pips = 1.0

    # 3. 每手每 pip 的盈虧（使用官方規格）
    risk_per_lot = sl_pips * pip_val
    if risk_per_lot <= 0:
        return {"lot":0.01,"risk_usd":max_risk_usd,"risk_pct":risk_pct,
                "margin_pct":0,"recommended_leverage":max_leverage}

    # 4. 原始手數
    raw_lot = max_risk_usd / risk_per_lot

    # 5. AI 智能槓桿（超短線建議較低槓桿）
    recommended_leverage = {
        "外匯": 30,    # 超短線外匯用 30x
        "加密":  1,    # 加密 1x（Mitrade 最高 2x，保守用 1x）
        "指數": 20,
        "商品": 30,
    }.get(cat, 30)
    recommended_leverage = min(recommended_leverage, max_leverage)

    # 6. 保證金不超過帳戶 20%
    pos_val       = raw_lot * contract_size * entry
    margin_needed = pos_val / recommended_leverage
    max_margin    = balance * 0.20

    if margin_needed > max_margin:
        raw_lot = (max_margin * recommended_leverage) / (contract_size * entry)
        logger.info(f"[{symbol}] 保證金超限，手數縮減至 {raw_lot:.4f}")

    # 7. 取整
    min_lot = 0.1 if cat == "指數" else 0.01
    lot = max(min_lot, math.floor(raw_lot / min_lot) * min_lot)
    lot = min(lot, CB["max_lot"])

    # 8. 重算實際值
    actual_risk  = lot * risk_per_lot
    pos_val_act  = lot * contract_size * entry
    margin_used  = pos_val_act / recommended_leverage
    lev_used     = round(pos_val_act / balance, 1) if balance > 0 else 0

    logger.info(
        f"  [{symbol}] lot={lot} sl_pips={sl_pips:.1f} pip_val={pip_val} "
        f"risk=${actual_risk:.2f}({actual_risk/balance*100:.2f}%) "
        f"margin=${margin_used:.2f}({margin_used/balance*100:.1f}%)"
    )

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
    """
    ★ 修正版止損：保證最小 pips，避免被正常波動掃掉
    """
    _, pip_size, _, _ = _get_specs(symbol)

    # ATR 倍數
    sl_dist = atr * cp.get("sl_mult", 1.5)

    # ★ 強制最小止損距離
    min_sl = cp.get("sl_min_pips", 10) * pip_size
    max_sl = cp.get("sl_max_pips", 30) * pip_size
    sl_dist = max(sl_dist, min_sl)
    sl_dist = min(sl_dist, max_sl)

    # 支撐阻力微調
    sr = indicators.get("support_resistance", {})
    if direction == "buy":
        sl = entry - sl_dist
        sup = sr.get("nearest_support")
        if sup and (entry - sl_dist * 1.3) < sup < entry:
            sl = min(sl, sup * 0.998)
    else:
        sl = entry + sl_dist
        res = sr.get("nearest_resistance")
        if res and entry < res < (entry + sl_dist * 1.3):
            sl = max(sl, res * 1.002)

    # 再次確認最小距離
    if abs(entry - sl) < min_sl:
        sl = (entry - min_sl) if direction == "buy" else (entry + min_sl)

    pips = abs(entry - sl) / pip_size
    logger.info(f"  [{symbol}] SL={round(sl,5)} dist={pips:.1f}pips")
    return round(sl, 6)


def calc_take_profits(direction: str, entry: float, sl: float, cp: dict):
    risk    = abs(entry - sl)
    tp1_rr  = cp.get("tp1_rr", 1.5)
    tp2_rr  = cp.get("tp2_rr", 2.5)
    if direction == "buy":
        tp1, tp2 = entry + risk * tp1_rr, entry + risk * tp2_rr
    else:
        tp1, tp2 = entry - risk * tp1_rr, entry - risk * tp2_rr
    return round(tp1, 6), round(tp2, 6), round(tp1_rr, 1), round(tp2_rr, 1)


def calc_trading_costs(symbol: str, entry: float) -> dict:
    swap_info   = OVERNIGHT_SWAP.get(symbol, {"buy": 0.0, "sell": 0.0})
    spread_pips = {
        "EURUSD":1.0,"GBPUSD":1.2,"USDJPY":1.0,"AUDUSD":1.2,"USDCAD":1.5,
        "XAUUSD":3.0,"WTI":4.0,"BTCUSD":50.0,"ETHUSD":10.0,
        "US500":0.4,"NAS100":1.0,"US30":2.0,"HK50":5.0,"GER40":1.0,
    }.get(symbol, 2.0)
    _, pip_size, _, _ = _get_specs(symbol)
    return {
        "spread":      round(spread_pips * pip_size, 6),
        "spread_pips": spread_pips,
        "swap_buy":    swap_info.get("buy", 0.0),
        "swap_sell":   swap_info.get("sell", 0.0),
    }


def check_multi_timeframe(tf_data: dict) -> dict:
    """超短線：entry=5M，mid=15M，trend=1H"""
    from indicators import calc_all_indicators
    results = {}
    for tf_key in ["trend", "mid", "entry"]:
        d = tf_data.get(tf_key)
        if d:
            results[tf_key] = calc_all_indicators(d)
    if not results:
        return {"direction":"none","score":0,"resonance":False,
                "conditions_met":[],"conditions_fail":[]}

    directions, scores = {}, {}
    for k, ind in results.items():
        if not ind.get("valid"): continue
        directions[k] = ind.get("overall_bias", "neutral")
        scores[k]     = ind.get("total_score", 0)

    bull_tfs = [k for k, v in directions.items() if "bullish" in v]
    bear_tfs = [k for k, v in directions.items() if "bearish" in v]

    # 超短線：entry(5M) 必須和至少一個更高時框對齊
    entry_bias = directions.get("entry", "neutral")
    entry_bullish = "bullish" in entry_bias
    entry_bearish = "bearish" in entry_bias

    if len(bull_tfs) >= 2 and entry_bullish:
        direction = "buy"
        raw_score = sum(scores.get(k, 0) for k in bull_tfs) // max(len(bull_tfs), 1)
    elif len(bear_tfs) >= 2 and entry_bearish:
        direction = "sell"
        raw_score = sum(scores.get(k, 0) for k in bear_tfs) // max(len(bear_tfs), 1)
    else:
        direction, raw_score = "none", 0

    resonance = (len(bull_tfs) >= 3 or len(bear_tfs) >= 3)
    score     = max(0, min(100, 50 + raw_score * 8 + (10 if resonance else 0)))

    conds_met, conds_fail = [], []
    entry_ind  = results.get("entry", {})
    rsi_bias   = entry_ind.get("rsi",  {}).get("bias", "")
    ema_bias   = entry_ind.get("ema",  {}).get("bias", "")
    macd_bias  = entry_ind.get("macd", {}).get("bias", "")

    for label, val, expect, relevant in [
        ("EMA多頭排列",  ema_bias,  "bullish", direction == "buy"),
        ("EMA空頭排列",  ema_bias,  "bearish", direction == "sell"),
        ("RSI多頭區間",  rsi_bias,  "bullish", direction == "buy"),
        ("RSI空頭區間",  rsi_bias,  "bearish", direction == "sell"),
        ("MACD偏多",     macd_bias, "bullish", direction == "buy"),
        ("MACD偏空",     macd_bias, "bearish", direction == "sell"),
    ]:
        if not relevant: continue
        (conds_met if expect in str(val) else conds_fail).append(label)

    if len(bull_tfs) == 3:   conds_met.append("三時框多頭共振（1H+15M+5M）")
    elif len(bear_tfs) == 3: conds_met.append("三時框空頭共振（1H+15M+5M）")
    elif len(bull_tfs) == 2: conds_met.append("雙時框多頭共振")
    elif len(bear_tfs) == 2: conds_met.append("雙時框空頭共振")

    adx = entry_ind.get("adx_value", 0)
    if adx >= 25:  conds_met.append(f"ADX {adx:.0f} 趨勢確認")
    elif adx > 0:  conds_fail.append(f"ADX {adx:.0f} 趨勢偏弱")

    cp_data = entry_ind.get("candlestick", {})
    if cp_data.get("valid"):
        if cp_data.get("bullish") and direction == "buy":
            conds_met.append(f"K線形態：{cp_data.get('name','多頭形態')}")
        elif cp_data.get("bearish") and direction == "sell":
            conds_met.append(f"K線形態：{cp_data.get('name','空頭形態')}")

    # 超短線額外條件
    macd_cross = entry_ind.get("macd", {}).get("cross", "")
    if macd_cross == "MACD金叉" and direction == "buy":
        conds_met.append("MACD金叉（5M）")
    elif macd_cross == "MACD死叉" and direction == "sell":
        conds_met.append("MACD死叉（5M）")

    return {
        "direction":        direction,
        "score":            score,
        "resonance":        resonance,
        "bull_timeframes":  bull_tfs,
        "bear_timeframes":  bear_tfs,
        "conditions_met":   conds_met,
        "conditions_fail":  conds_fail,
        "entry_indicators": entry_ind,
    }


def _check_category_filters(symbol, direction, indicators, macro_data, cp):
    cat = SYMBOLS.get(symbol, {}).get("cat", "")

    if cat == "外匯":
        adx = indicators.get("adx_value", 0)
        if adx < cp.get("min_adx", 20):
            return False, f"ADX {adx:.0f} < {cp.get('min_adx',20)} 趨勢不足"
        return True, ""

    if symbol == "XAUUSD":
        dxy_chg = float(macro_data.get("dxy", {}).get("chg", 0) or 0)
        if direction == "buy"  and dxy_chg >  0.8: return False, f"黃金多頭：DXY上升 {dxy_chg:.1f}%"
        if direction == "sell" and dxy_chg < -0.8: return False, f"黃金空頭：DXY下降 {dxy_chg:.1f}%"
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
    """★ 超短線版 generate_signal"""
    try:
        from indicators import calc_all_indicators
        if symbol in TIER_D_SYMBOLS: return None

        cp  = dict(_get_cat_params(symbol))
        si  = SYMBOLS.get(symbol, {})
        cat = si.get("cat", "")
        _, pip_size, _, _ = _get_specs(symbol)

        entry_data = tf_data.get("entry")
        if not entry_data: return None
        indicators = calc_all_indicators(entry_data)
        if not indicators.get("valid"): return None

        mtf       = check_multi_timeframe(tf_data)
        direction = mtf.get("direction", "none")
        score     = mtf.get("score", 0)
        if direction == "none": return None

        if score < cp.get("min_score", THRESH["min_score"]): return None
        if indicators.get("adx_value", 0) < cp.get("min_adx", 20): return None

        ok, reason = _check_category_filters(symbol, direction, indicators, macro_data, cp)
        if not ok:
            logger.info(f"  [{symbol}] 過濾：{reason}")
            return None

        # 川普事件
        trump_event = macro_data.get("trump_event_type", "")
        if trump_event == "crypto_hostile" and cat == "加密": return None

        atr   = indicators.get("atr", {}).get("value", 0)
        price = entry_data.get("current_price", 0)
        if not atr or not price: return None

        # ── 止損計算 ────────────────────────────────
        sl = calc_stop_loss(symbol, direction, price, atr, indicators, cp)
        sl_pips = abs(price - sl) / pip_size

        # 驗證
        if sl_pips < cp.get("sl_min_pips", 10) * 0.8:
            logger.warning(f"  [{symbol}] ❌ SL {sl_pips:.1f}pips 太小")
            return None

        # ── 止盈計算 ────────────────────────────────
        tp1, tp2, rr1, rr2 = calc_take_profits(direction, price, sl, cp)
        if rr1 < THRESH.get("min_rr", 1.3): return None

        # ── 手數計算 ────────────────────────────────
        lot_info = calc_lot_size(symbol, price, sl)
        costs    = calc_trading_costs(symbol, price)

        if lot_info["lot"] <= 0: return None
        if lot_info.get("risk_pct", 100) > CB["risk_per_trade_pct"] * 1.5:
            logger.warning(f"  [{symbol}] ❌ 風險 {lot_info['risk_pct']:.2f}% 過高")
            return None

        sr  = indicators.get("support_resistance", {})
        fib = indicators.get("fibonacci", {})

        if score >= 85:   action = "🔥 立刻進場"
        elif score >= 75: action = "✅ 可以進場"
        elif score >= 68: action = "⏳ 等待確認"
        else:             action = "👀 觀察"

        sig_id = f"{symbol}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        logger.info(
            f"  [{symbol}] ✅ {direction} score={score} "
            f"SL={sl_pips:.1f}pips RR=1:{rr1} "
            f"lot={lot_info['lot']} risk={lot_info['risk_pct']:.2f}%"
        )

        return {
            "id":                   sig_id,
            "symbol":               symbol,
            "name":                 si.get("name", symbol),
            "emoji":                si.get("emoji", "📊"),
            "category":             cat,
            "direction":            direction,
            "score":                score,
            "action":               action,
            "current_price":        round(price, 6),
            "entry_price":          round(price, 6),
            "stop_loss":            sl,
            "tp1":                  tp1,
            "tp2":                  tp2,
            "rr1":                  rr1,
            "rr2":                  rr2,
            "sl_pips":              round(sl_pips, 1),
            "suggested_lot":        lot_info["lot"],
            "risk_usd":             lot_info["risk_usd"],
            "risk_pct":             lot_info["risk_pct"],
            "leverage_used":        lot_info.get("leverage_used", 0),
            "recommended_leverage": lot_info.get("recommended_leverage", 30),
            "margin_used":          lot_info.get("margin_used", 0),
            "margin_pct":           lot_info.get("margin_pct", 0),
            "trading_costs":        costs,
            "support_resistance":   sr,
            "fibonacci":            fib,
            "nearest_support":      sr.get("nearest_support"),
            "nearest_resistance":   sr.get("nearest_resistance"),
            "conditions_met":       mtf.get("conditions_met", []),
            "conditions_fail":      mtf.get("conditions_fail", []),
            "macro_notes":          [],
            "timeframe":            entry_data.get("label", "5分鐘"),
            "generated_at":         datetime.now(timezone.utc).isoformat(),
            "adx_value":            indicators.get("adx_value", 0),
            "result":               "pending",
            "pnl":                  0,
            "close_price":          None,
            "closed_at":            None,
        }

    except Exception as e:
        logger.error(f"generate_signal {symbol}: {e}")
        return None
