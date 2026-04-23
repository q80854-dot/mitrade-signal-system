"""
signal_engine.py v4.0 — 訊號產生引擎
根據10年回測分析改進：
1. 外匯：ADX>30強趨勢才進場，門檻提高至72
2. 加密：加入熊市過濾（200日MA下降停止做多）
3. 指數：200日MA下降時禁止做多
4. 美股個股：財報前後7天暫停，板塊確認
5. 黃金：加入DXY反向確認
6. WTI：提高門檻至72，極端波動過濾
7. 整體門檻從65提高到68
8. TP1到達後移止損到本金（Trailing Stop邏輯）
"""
import logging, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from config import (SYMBOLS, TIMEFRAMES, SIGNAL_THRESHOLDS as THRESH,
                    OVERNIGHT_SWAP, ACCOUNT_BALANCE_USD, CB)

logger = logging.getLogger(__name__)

# ── 各類別策略參數（根據10年回測優化）──
# ── v7.0 機構分層策略參數 ──
# Tier A：積極型（NVDA/BTC/ETH/NAS100）
# Tier B：穩健型（US500/AAPL/MSFT/XAUUSD等）
# Tier C：謹慎型（外匯）
# Tier D：停止（TSLA/WTI/HK50）
CATEGORY_PARAMS = {
    "外匯": {
        "min_score":      65,    # Tier C：嚴格門檻，確保品質
        "min_adx":        25,    # 高ADX才進場
        "sl_mult":        0.9,
        "tp1_mult":       1.6,
        "tp2_mult":       2.8,
        "max_hold_days":  7,
        "require_weekly_trend": True,
        "require_dxy_confirm":  False,  # DXY為加分項
        "dxy_bonus":      8,
    },
    "商品_黃金": {
        "min_score":      60,
        "min_adx":        18,
        "sl_mult":        1.3,
        "tp1_mult":       1.8,
        "tp2_mult":       4.0,    # v7：提高TP2，配合移動止損
        "max_hold_days":  15,
    },
    "商品_WTI": {
        "min_score":      80,    # Tier D：極高門檻，幾乎不觸發
        "min_adx":        35,
        "sl_mult":        1.2,
        "tp1_mult":       1.8,
        "tp2_mult":       3.0,
        "max_hold_days":  8,
        "eia_blackout":   3,
    },
    "加密": {
        "min_score":      55,    # Tier A：積極，但有熊市保護
        "min_adx":        15,
        "sl_mult":        1.8,
        "tp1_mult":       2.2,
        "tp2_mult":       4.0,
        "max_hold_days":  20,
        "require_bull_market":   True,
        "fg_min_score":   15,
    },
    "指數": {
        "min_score":      60,    # Tier A/B 混合
        "min_adx":        18,
        "sl_mult":        1.1,
        "tp1_mult":       1.8,
        "tp2_mult":       3.2,
        "max_hold_days":  14,
        "require_bull_market":   True,
        "vix_max":        32,
    },
    "美股": {
        "min_score":      62,    # Tier B：穩健型
        "min_adx":        18,
        "sl_mult":        1.2,
        "tp1_mult":       2.0,
        "tp2_mult":       3.8,
        "max_hold_days":  12,
        "earnings_blackout": 7,
        "require_sector_confirm": False,
    },
}

# 停止交易品種（Tier D）
TIER_D_SYMBOLS = {"TSLA", "WTI", "HK50", "AUDUSD", "USDCAD"}

def _get_cat_params(symbol: str) -> dict:
    """取得品種對應的策略參數"""
    cat = SYMBOLS.get(symbol, {}).get("cat", "")
    if symbol == "XAUUSD": return CATEGORY_PARAMS["商品_黃金"]
    if symbol == "WTI":    return CATEGORY_PARAMS["商品_WTI"]
    if cat in CATEGORY_PARAMS: return CATEGORY_PARAMS[cat]
    return {"min_score":68,"min_adx":20,"sl_mult":1.3,"tp1_mult":2.0,"tp2_mult":3.5,"max_hold_days":12}

def calc_trading_costs(symbol: str, entry: float) -> dict:
    """計算交易成本（點差 + Overnight Swap）"""
    si = SYMBOLS.get(symbol, {})
    pip = si.get("pip", 0.0001)
    swap_info = OVERNIGHT_SWAP.get(symbol, {"buy": 0, "sell": 0})
    spread_pips = {"EURUSD":1.0,"GBPUSD":1.2,"USDJPY":1.0,"AUDUSD":1.2,"USDCAD":1.5,
                   "XAUUSD":3.0,"WTI":4.0,"BTCUSD":50.0,"ETHUSD":10.0,
                   "US500":0.4,"NAS100":1.0,"US30":2.0,"HK50":5.0,"GER40":1.0,
                   "AAPL":0.02,"NVDA":0.05,"TSLA":0.10,"MSFT":0.02,"AMZN":0.05,"GOOGL":0.05
                   }.get(symbol, 2.0)
    return {
        "spread": round(spread_pips * pip, 6),
        "spread_pips": spread_pips,
        "swap_buy":     swap_info.get("buy", 0),
        "swap_sell":    swap_info.get("sell", 0),
        "swap_per_day": abs(swap_info.get("buy", 0)),
    }

def calc_lot_size(symbol: str, entry: float, sl: float,
                  balance: float = None, risk_pct: float = None) -> dict:
    """計算建議手數"""
    balance  = balance  or ACCOUNT_BALANCE_USD
    risk_pct = risk_pct or CB["risk_per_trade_pct"]
    cat      = SYMBOLS.get(symbol, {}).get("cat", "")
    si       = SYMBOLS.get(symbol, {})
    pip      = si.get("pip", 0.0001)
    risk_usd = balance * risk_pct / 100
    sl_dist  = abs(entry - sl)
    if sl_dist < 1e-10:
        return {"lot": 0.01, "risk_usd": risk_usd, "risk_pct": risk_pct}

    if cat == "外匯":      pip_val = 10.0
    elif symbol == "XAUUSD": pip_val = 1.0
    elif symbol == "WTI":    pip_val = 10.0
    elif cat == "加密":    pip_val = 1.0
    elif cat == "指數":    pip_val = 1.0
    else:                  pip_val = 1.0

    sl_pips     = sl_dist / pip
    risk_per_lot= sl_pips * pip_val
    raw_lot     = risk_usd / risk_per_lot if risk_per_lot > 0 else 0.01
    min_lot     = 0.1 if cat in ["指數","美股"] else 0.01
    lot         = max(min_lot, round(raw_lot / min_lot) * min_lot)
    lot         = min(lot, CB.get("max_lot", 2.0))
    actual_risk = lot * risk_per_lot
    return {
        "lot":      round(lot, 2),
        "risk_usd": round(actual_risk, 2),
        "risk_pct": round(actual_risk / balance * 100, 2),
    }

def calc_stop_loss(symbol: str, direction: str, entry: float,
                   atr: float, indicators: dict, cp: dict) -> float:
    """計算止損（根據品種特性）"""
    sl_mult = cp.get("sl_mult", 1.3)
    sl_dist = atr * sl_mult
    # 確保止損不超過5%
    max_sl_pct = 0.05
    sl_dist = min(sl_dist, entry * max_sl_pct)
    # 支撐壓力位微調
    sr = indicators.get("support_resistance", {})
    if direction == "buy":
        sl = entry - sl_dist
        sup = sr.get("nearest_support")
        if sup and sup < entry and sup > sl:
            sl = sup * 0.998  # 支撐位下方0.2%
    else:
        sl = entry + sl_dist
        res = sr.get("nearest_resistance")
        if res and res > entry and res < sl:
            sl = res * 1.002
    return round(sl, 6)

def calc_take_profits(direction: str, entry: float, sl: float, cp: dict) -> tuple:
    """計算止盈"""
    risk    = abs(entry - sl)
    tp1_mul = cp.get("tp1_mult", 1.8)
    tp2_mul = cp.get("tp2_mult", 3.0)
    if direction == "buy":
        tp1 = entry + risk * tp1_mul
        tp2 = entry + risk * tp2_mul
    else:
        tp1 = entry - risk * tp1_mul
        tp2 = entry - risk * tp2_mul
    rr1 = round(tp1_mul, 1)
    rr2 = round(tp2_mul, 1)
    return round(tp1, 6), round(tp2, 6), rr1, rr2

def check_multi_timeframe(tf_data: dict) -> dict:
    """多時框共振分析"""
    from indicators import calc_all_indicators
    results = {}
    for tf_key in ["trend", "mid", "entry"]:
        d = tf_data.get(tf_key)
        if d:
            ind = calc_all_indicators(d)
            results[tf_key] = ind

    if not results:
        return {"direction": "none", "score": 0, "resonance": False,
                "conditions_met": [], "conditions_fail": []}

    # 各時框方向投票
    directions = {}
    scores     = {}
    for tf_key, ind in results.items():
        if not ind.get("valid"): continue
        directions[tf_key] = ind.get("overall_bias", "neutral")
        scores[tf_key]     = ind.get("total_score", 0)

    bull_tfs = [k for k,v in directions.items() if "bullish" in v]
    bear_tfs = [k for k,v in directions.items() if "bearish" in v]

    # 主方向判斷
    if len(bull_tfs) >= 2:
        direction = "buy"
        score = sum(scores.get(k,0) for k in bull_tfs) // len(bull_tfs)
    elif len(bear_tfs) >= 2:
        direction = "sell"
        score = sum(scores.get(k,0) for k in bear_tfs) // len(bear_tfs)
    else:
        direction = "none"
        score = 0

    resonance = (len(bull_tfs) >= 3 or len(bear_tfs) >= 3)
    if resonance: score = min(100, score + 10)

    # 條件清單
    conds_met  = []
    conds_fail = []
    entry_ind  = results.get("entry", {})

    checks = [
        ("EMA多頭排列",    "ema", "bias", "bullish",  direction=="buy"),
        ("EMA空頭排列",    "ema", "bias", "bearish",  direction=="sell"),
        ("RSI多頭區間",    "rsi", "zone", "bullish",  direction=="buy"),
        ("RSI空頭區間",    "rsi", "zone", "bearish",  direction=="sell"),
        ("MACD偏多",       "macd","bias", "bullish",  direction=="buy"),
        ("MACD偏空",       "macd","bias", "bearish",  direction=="sell"),
    ]
    for label, ind_k, sub_k, expect, is_relevant in checks:
        if not is_relevant: continue
        val = entry_ind.get(ind_k, {}).get(sub_k, "")
        if expect in str(val): conds_met.append(label)
        else:                  conds_fail.append(label)

    # 加入共振條件
    if len(bull_tfs) == 3: conds_met.append("三時框多頭共振")
    elif len(bear_tfs) == 3: conds_met.append("三時框空頭共振")
    elif len(bull_tfs) == 2: conds_met.append("雙時框多頭共振")
    elif len(bear_tfs) == 2: conds_met.append("雙時框空頭共振")

    # K線形態
    pattern = entry_ind.get("candlestick_pattern", {})
    if pattern.get("bullish") and direction == "buy":
        conds_met.append(f"K線形態：{pattern.get('name','')}")
    elif pattern.get("bearish") and direction == "sell":
        conds_met.append(f"K線形態：{pattern.get('name','')}")

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

def _check_category_filters(symbol: str, direction: str,
                              indicators: dict, macro_data: dict, cp: dict) -> tuple:
    """
    類別專屬過濾器（根據10年回測改進）
    回傳 (pass: bool, reason: str)
    """
    cat   = SYMBOLS.get(symbol, {}).get("cat", "")
    entry = indicators.get("current_price", 0)

    # ── 外匯：強趨勢過濾 ────────────────
    if cat == "外匯":
        adx = indicators.get("adx_value", 0)
        if adx < cp.get("min_adx", 20):
            return False, f"外匯ADX{adx:.0f}<{cp.get('min_adx',20)}，趨勢不足"
        # DXY：加分項（不再硬性阻擋）
        if cp.get("require_dxy_confirm", False):
            dxy_chg = macro_data.get("dxy", {}).get("chg", 0)
            if symbol in ["EURUSD","GBPUSD","AUDUSD"]:
                if direction=="buy" and dxy_chg > 0.5:
                    return False, f"DXY大幅上升{dxy_chg:.1f}%，外匯做多風險高"
                if direction=="sell" and dxy_chg < -0.5:
                    return False, f"DXY大幅下降{dxy_chg:.1f}%，外匯做空風險高"
        return True, ""

    # ── 黃金：通膨/DXY 確認 ─────────────
    if symbol == "XAUUSD":
        dxy_chg = macro_data.get("dxy", {}).get("chg", 0)
        if direction == "buy" and dxy_chg > 0.5:
            return False, f"黃金多頭：DXY上升{dxy_chg:.1f}%，黃金承壓"
        if direction == "sell" and dxy_chg < -0.5:
            return False, f"黃金空頭：DXY下降{dxy_chg:.1f}%，黃金支撐強"
        return True, ""

    # ── 加密：熊市過濾 ──────────────────
    if cat == "加密":
        ema200 = indicators.get("ema", {}).get("ema200")
        price  = indicators.get("current_price", 0)
        if ema200 and price and price < ema200 * 0.95:
            return False, f"加密熊市：價格低於200MA 5%以上，停止做多"
        fg = macro_data.get("fear_greed", {})
        fg_score = fg.get("score", 50)
        if direction == "buy" and fg_score < cp.get("fg_min_score", 25):
            return False, f"恐懼貪婪指數{fg_score:.0f}<25，市場極度恐慌"
        return True, ""

    # ── 指數：牛熊過濾 ──────────────────
    if cat == "指數":
        ema200 = indicators.get("ema", {}).get("ema200")
        price  = indicators.get("current_price", 0)
        if ema200 and price and price < ema200 and direction == "buy":
            return False, f"指數熊市：價格低於200MA，禁止做多"
        vix = macro_data.get("vix", {}).get("price", 20)
        if float(vix) > cp.get("vix_max", 30) and direction == "buy":
            return False, f"VIX={vix:.0f}>30，高恐慌不做多"
        return True, ""

    # ── 美股：財報/板塊確認 ─────────────
    if cat == "美股":
        earnings = macro_data.get("earnings_calendar", [])
        if earnings:
            today = datetime.now(timezone.utc)
            for e in earnings:
                if e.get("symbol") == symbol:
                    try:
                        e_date = datetime.fromisoformat(e.get("date",""))
                        days_away = abs((e_date - today).days)
                        blackout = cp.get("earnings_blackout", 7)
                        if days_away <= blackout:
                            return False, f"財報前後{blackout}天禁止交易（{days_away}天後）"
                    except: pass
        return True, ""

    return True, ""

def generate_signal(symbol: str, tf_data: dict,
                    macro_data: dict) -> Optional[Dict]:
    """
    完整訊號產生流程
    Step 5: 策略評分 + 類別過濾器 → 輸出訊號
    """
    try:
        from indicators import calc_all_indicators
        # Tier D：停止自動訊號
        if symbol in TIER_D_SYMBOLS:
            logger.debug(f"  [{symbol}] Tier D 停止自動訊號")
            return None
        cp = _get_cat_params(symbol)
        si = SYMBOLS.get(symbol, {})

        # 取得Entry時框指標
        entry_data = tf_data.get("entry")
        if not entry_data: return None
        indicators = calc_all_indicators(entry_data)
        if not indicators.get("valid"): return None

        # 多時框共振
        mtf = check_multi_timeframe(tf_data)
        direction = mtf.get("direction", "none")
        score     = mtf.get("score", 0)

        if direction == "none": return None

        # 門檻檢查（依類別）
        min_score = cp.get("min_score", 68)
        if score < min_score:
            return None

        # ADX 檢查
        adx = indicators.get("adx_value", 0)
        min_adx = cp.get("min_adx", 20)
        if adx < min_adx:
            return None

        # 類別專屬過濾器
        pass_filter, filter_reason = _check_category_filters(
            symbol, direction, indicators, macro_data, cp)
        if not pass_filter:
            logger.info(f"  [{symbol}] 類別過濾：{filter_reason}")
            return None

        # 川普事件應對（根據最新 trump_data）
        trump_event = macro_data.get("trump_event_type", "")
        trump_sensitivity = {
            "外匯":0.4,"商品":0.8,"加密":1.0,"指數":0.8,"美股":0.7
        }.get(cat, 0.5)

        # 廣泛關稅事件：縮小倉位50%
        if trump_event == "tariff_broad":
            cp = dict(cp)  # 複製，不修改原始
            cp["sl_mult"]  = cp.get("sl_mult",1.0) * 0.8
            cp["tp1_mult"] = cp.get("tp1_mult",1.8) * 0.9
            logger.info(f"  [{symbol}] 川普廣泛關稅事件，縮小止損和止盈")

        # 加密打壓：加密倉位縮減
        if trump_event == "crypto_hostile" and cat == "加密":
            logger.info(f"  [{symbol}] 川普加密打壓，跳過加密訊號")
            return None

        # 計算進場/止損/止盈
        atr   = indicators.get("atr", {}).get("value", 0)
        price = entry_data.get("current_price", 0)
        if not atr or not price: return None

        # ADX動態倍數：趨勢越強，止損可以給更多空間
        adx_val = indicators.get("adx_value", adx)
        if adx_val >= 40:   adx_mult = 1.2
        elif adx_val >= 30: adx_mult = 1.1
        else:               adx_mult = 1.0

        # 動態調整 cp 的止損/止盈倍數
        cp = dict(cp)  # 複製，不影響原始
        cp["sl_mult"]      = cp.get("sl_mult", 1.0) * adx_mult
        cp["tp1_mult"]     = cp.get("tp1_mult", 1.8) * (1.0 if adx_val < 30 else 1.05)
        cp["max_hold_days"]= int(cp.get("max_hold_days", 10) * (1.2 if adx_val >= 35 else 1.0))

        sl      = calc_stop_loss(symbol, direction, price, atr, indicators, cp)
        sl_pct  = abs(price - sl) / price * 100
        if sl_pct > 5:
            return None  # 止損太大，不進場

        tp1, tp2, rr1, rr2 = calc_take_profits(direction, price, sl, cp)
        if rr1 < THRESH.get("min_rr", 1.3): return None

        # 倉位計算
        lot_info = calc_lot_size(symbol, price, sl)
        costs    = calc_trading_costs(symbol, price)
        sr       = indicators.get("support_resistance", {})

        # 動作說明
        if score >= 85:   action="🔥 立刻進場"; action_color="green"
        elif score >= 75: action="✅ 可以進場"; action_color="green"
        elif score >= 68: action="⏳ 等待確認"; action_color="yellow"
        else:             action="👀 觀察"; action_color="gray"

        sig_id = f"{symbol}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"

        return {
            "id":                sig_id,
            "symbol":            symbol,
            "name":              si.get("name", symbol),
            "emoji":             si.get("emoji", "📊"),
            "category":          si.get("cat", ""),
            "direction":         direction,
            "score":             score,
            "action":            action,
            "action_color":      action_color,
            "current_price":     round(price, 6),
            "entry_price":       round(price, 6),
            "stop_loss":         sl,
            "tp1":               tp1,
            "tp2":               tp2,
            "rr1":               rr1,
            "rr2":               rr2,
            "suggested_lot":     lot_info["lot"],
            "risk_usd":          lot_info["risk_usd"],
            "risk_pct":          lot_info["risk_pct"],
            "trading_costs":     costs,
            "support_resistance": sr,
            "nearest_support":   sr.get("nearest_support"),
            "nearest_resistance":sr.get("nearest_resistance"),
            "conditions_met":    mtf.get("conditions_met", []),
            "conditions_fail":   mtf.get("conditions_fail", []),
            "macro_notes":       [],
            "timeframe":         entry_data.get("label", ""),
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "category_filter_applied": True,
        }
    except Exception as e:
        logger.error(f"generate_signal {symbol}: {e}")
        return None
