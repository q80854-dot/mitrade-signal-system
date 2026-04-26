""" adaptive_weight_engine.py v5.2 — 修正版
修正：
  1. _weight_history 改用 SQLite 持久化（透過 state_store），重啟不消失
  2. 移除美股專屬 CATEGORY_BASE（已不需要）
"""
import logging
from datetime import datetime, timezone
from typing import Dict

logger = logging.getLogger(__name__)

DIMS = ["tech","macro","trump","fg","news","vol","trend"]

BASE_WEIGHTS = {
    "tech":25,"macro":20,"trump":18,"fg":10,"news":12,"vol":8,"trend":7,
}

# ★ 移除美股，只保留外匯/商品/加密/指數
CATEGORY_BASE = {
    "外匯": {"tech":30,"macro":25,"trump":15,"fg":5, "news":10,"vol":8,"trend":7},
    "商品": {"tech":25,"macro":20,"trump":20,"fg":8, "news":12,"vol":8,"trend":7},
    "加密": {"tech":20,"macro":10,"trump":22,"fg":20,"news":10,"vol":10,"trend":8},
    "指數": {"tech":25,"macro":20,"trump":18,"fg":10,"news":12,"vol":8,"trend":7},
}

def _detect_market_state(macro_data: dict, indicators: dict = None) -> dict:
    vix      = float(macro_data.get("vix",{}).get("price",20)         or 20)
    fg       = float(macro_data.get("fear_greed",{}).get("score",50)  or 50)
    dxy_chg  = float(macro_data.get("dxy",{}).get("chg",0)            or 0)
    sp500_c  = float(macro_data.get("sp500",{}).get("chg",0)          or 0)
    adx      = float((indicators or {}).get("adx_value",0)            or 0)
    trump_ev = macro_data.get("trump_event_type","none")
    trump_posts = []
    _td = macro_data.get("trump_data")
    if isinstance(_td, dict): trump_posts = _td.get("posts",[])
    high_trump = len([p for p in trump_posts if p.get("impact_level")=="high"])
    return {
        "panic":       min(1.0, max(0,(vix-15)/30)),
        "trending":    min(1.0, max(0,(adx-15)/25)),
        "ranging":     min(1.0, max(0,(25-adx)/20)),
        "bullish":     min(1.0, max(0,(fg-50)/50))  if fg>50 else 0,
        "bearish":     min(1.0, max(0,(50-fg)/50))  if fg<50 else 0,
        "trump_hot":   min(1.0, high_trump*0.5+(0.8 if trump_ev not in ["none","other"] else 0)),
        "dxy_strong":  min(1.0, max(0,dxy_chg/1.5)) if dxy_chg>0 else 0,
        "dxy_weak":    min(1.0, max(0,-dxy_chg/1.5)) if dxy_chg<0 else 0,
        "equity_bull": min(1.0, max(0,sp500_c/1.5)) if sp500_c>0 else 0,
        "equity_bear": min(1.0, max(0,-sp500_c/1.5)) if sp500_c<0 else 0,
    }

def calc_adaptive_weights(macro_data: dict, indicators: dict = None,
                          symbol: str = "", category: str = "") -> dict:
    from config import SYMBOLS
    if not category and symbol: category = SYMBOLS.get(symbol,{}).get("cat","")
    base  = dict(CATEGORY_BASE.get(category, BASE_WEIGHTS))
    state = _detect_market_state(macro_data, indicators)
    adjustments = []; deltas = {d: 0.0 for d in DIMS}

    panic = state["panic"]
    if panic > 0.3:
        deltas["tech"] -= panic*12; deltas["fg"] += panic*8
        deltas["trump"] += panic*5; deltas["macro"] += panic*4
        adjustments.append({"dim":"tech","delta":round(-panic*12,1),
            "reason":f"VIX={float(macro_data.get('vix',{}).get('price',20) or 20):.1f} 恐慌偏高，技術指標可靠度下降"})
        adjustments.append({"dim":"fg","delta":round(panic*8,1),
            "reason":"恐慌市場情緒主導，F&G 權重提升"})

    trending = state["trending"]
    if trending > 0.3:
        deltas["tech"] += trending*8; deltas["trend"] += trending*6
        deltas["news"] -= trending*5; deltas["fg"] -= trending*4
        adjustments.append({"dim":"tech","delta":round(trending*8,1),
            "reason":f"ADX={float((indicators or {}).get('adx_value',0) or 0):.0f} 趨勢強勁，技術面可靠度提升"})
        adjustments.append({"dim":"trend","delta":round(trending*6,1),
            "reason":"強趨勢環境，動能延續性高"})

    ranging = state["ranging"]
    if ranging > 0.4:
        deltas["macro"] += ranging*8; deltas["vol"] += ranging*5
        deltas["tech"] -= ranging*6; deltas["trend"] -= ranging*5
        adjustments.append({"dim":"macro","delta":round(ranging*8,1),
            "reason":"震盪市場，宏觀基本面更重要"})

    trump_hot = state["trump_hot"]
    if trump_hot > 0.3:
        deltas["trump"] += trump_hot*15; deltas["news"] += trump_hot*5
        deltas["tech"] -= trump_hot*8; deltas["macro"] -= trump_hot*6
        adjustments.append({"dim":"trump","delta":round(trump_hot*15,1),
            "reason":"川普重大發文，政策風險主導市場"})

    if category == "加密":
        fg_extreme = abs(float(macro_data.get("fear_greed",{}).get("score",50) or 50)-50)/50
        if fg_extreme > 0.4:
            deltas["fg"] += fg_extreme*10; deltas["trump"] += fg_extreme*5
            deltas["macro"] -= fg_extreme*8
            adjustments.append({"dim":"fg","delta":round(fg_extreme*10,1),
                "reason":"加密極端情緒，F&G 信號可靠度高"})

    if category == "外匯":
        dxy_move = max(state["dxy_strong"], state["dxy_weak"])
        if dxy_move > 0.3:
            deltas["macro"] += dxy_move*8; deltas["tech"] -= dxy_move*5
            adjustments.append({"dim":"macro","delta":round(dxy_move*8,1),
                "reason":"DXY 大幅波動，宏觀驅動明顯"})

    raw = {d: max(2, base[d]+deltas[d]) for d in DIMS}
    total = sum(raw.values())
    normalized = {d: round(raw[d]/total*100, 1) for d in DIMS}
    diff = round(100-sum(normalized.values()), 1)
    max_dim = max(normalized, key=normalized.get)
    normalized[max_dim] = round(normalized[max_dim]+diff, 1)

    dominant = max(state, key=lambda k: state[k])
    dominant_zh = {
        "panic":"恐慌市場","trending":"趨勢行情","ranging":"震盪整理",
        "trump_hot":"川普主導","bullish":"偏多環境","bearish":"偏空環境",
        "dxy_strong":"美元強勢","dxy_weak":"美元弱勢",
        "equity_bull":"美股多頭","equity_bear":"美股空頭",
    }.get(dominant,"正常市場")

    logger.info(f"[Weight] {symbol or category} 主導：{dominant_zh} "
                f"tech={normalized['tech']} trump={normalized['trump']} fg={normalized['fg']}")
    return {
        "weights":      normalized,
        "adjustments":  adjustments,
        "state":        {k: round(v,3) for k,v in state.items()},
        "dominant_state": dominant,
        "dominant_zh":  dominant_zh,
        "confidence":   round(min(1.0, max(state.values())+0.3), 2),
        "category":     category,
        "symbol":       symbol,
        "calculated_at":datetime.now(timezone.utc).isoformat(),
    }

def calc_dim_scores(macro_data: dict, indicators: dict, trump_data: dict,
                    symbol: str = "") -> dict:
    vix     = float(macro_data.get("vix",{}).get("price",20)        or 20)
    fg      = float(macro_data.get("fear_greed",{}).get("score",50) or 50)
    sp500_c = float(macro_data.get("sp500",{}).get("chg",0)         or 0)
    adx     = float((indicators or {}).get("adx_value",0)           or 0)
    ema_bias= (indicators or {}).get("ema",{}).get("bias","neutral")
    rsi_val = float((indicators or {}).get("rsi",{}).get("value",50) or 50)
    macd_b  = (indicators or {}).get("macd",{}).get("bias","neutral")
    trump_posts = (trump_data or {}).get("posts",[])
    trump_mood  = (trump_data or {}).get("overall_market_mood","neutral")
    high_posts  = [p for p in trump_posts if p.get("impact_level")=="high"]

    tech = 0.5
    if "bullish" in str(ema_bias): tech += 0.15
    elif "bearish" in str(ema_bias): tech -= 0.15
    if "bullish" in str(macd_b): tech += 0.1
    elif "bearish" in str(macd_b): tech -= 0.1
    if adx >= 25: tech += 0.1
    if 40 <= rsi_val <= 65: tech += 0.05

    macro = 0.5
    if vix < 15:   macro += 0.2
    elif vix < 20: macro += 0.1
    elif vix > 30: macro -= 0.2
    elif vix > 25: macro -= 0.1
    if sp500_c > 0.3: macro += 0.1
    elif sp500_c < -0.3: macro -= 0.1
    fred     = macro_data.get("fred",{})
    fed_val  = (fred.get("fed_rate",{}) or {}).get("value")
    fed_rate = float(fed_val) if fed_val is not None else 5.0
    if fed_rate < 3: macro += 0.1

    trump_sc = 0.5
    if trump_mood == "risk-on":  trump_sc += 0.3
    elif trump_mood == "risk-off": trump_sc -= 0.3
    if high_posts:
        impact = high_posts[0].get("market_impact","neutral")
        if impact == "bullish":  trump_sc += 0.15
        elif impact == "bearish": trump_sc -= 0.15

    vol_data  = (indicators or {}).get("volume",{})
    vol_ratio = float(vol_data.get("ratio",1.0) or 1.0)
    vol_score = min(1.0, 0.3+vol_ratio*0.25)

    trend_sc = 0.5
    if adx >= 35:   trend_sc = 0.9
    elif adx >= 25: trend_sc = 0.75
    elif adx >= 20: trend_sc = 0.6
    elif adx < 15:  trend_sc = 0.3

    news_sc = max(0.0, min(1.0, 1-(vix-10)/40))
    if trump_mood == "risk-on":   news_sc = min(1.0, news_sc+0.1)
    elif trump_mood == "risk-off": news_sc = max(0.0, news_sc-0.1)

    return {
        "tech":  round(min(1.0, max(0.0, tech)),     3),
        "macro": round(min(1.0, max(0.0, macro)),    3),
        "trump": round(min(1.0, max(0.0, trump_sc)), 3),
        "fg":    round(fg/100,                       3),
        "news":  round(news_sc,                      3),
        "vol":   round(vol_score,                    3),
        "trend": round(trend_sc,                     3),
    }

def auto_composite_score(macro_data: dict, indicators: dict, trump_data: dict,
                         symbol: str = "", category: str = "") -> dict:
    from config import SYMBOLS
    if not category and symbol: category = SYMBOLS.get(symbol,{}).get("cat","")
    weight_result = calc_adaptive_weights(macro_data, indicators, symbol, category)
    weights    = weight_result["weights"]
    dim_scores = calc_dim_scores(macro_data, indicators, trump_data, symbol)
    composite  = sum(dim_scores[d]*weights[d]/100 for d in DIMS)
    if composite >= 0.65:  action, action_zh = "BUY",  "做多"
    elif composite <= 0.35: action, action_zh = "SELL", "做空"
    else:                   action, action_zh = "HOLD", "觀望"
    if dim_scores["tech"]>0.6  and dim_scores["macro"]>0.6: composite = min(1.0, composite+0.05)
    if dim_scores["trump"]>0.6 and dim_scores["tech"]>0.6:  composite = min(1.0, composite+0.03)
    return {
        "composite":      round(composite,3),
        "composite_100":  round(composite*100,1),
        "action":         action,
        "action_zh":      action_zh,
        "dim_scores":     dim_scores,
        "weights":        weights,
        "weight_reason":  weight_result["adjustments"],
        "dominant_state": weight_result["dominant_zh"],
        "confidence":     weight_result["confidence"],
        "state":          weight_result["state"],
        "symbol":         symbol,
        "category":       category,
        "calculated_at":  datetime.now(timezone.utc).isoformat(),
    }

# ★ 修正：七維度歷史改用 SQLite 持久化，重啟不消失
_WEIGHT_HISTORY_KEY = "weight_history"
_MAX_HISTORY = 100

def record_weight_history(result: dict):
    try:
        from state_store import store
        history = store.get_meta(_WEIGHT_HISTORY_KEY, default=[])
        history.append({
            "ts":        result["calculated_at"],
            "dominant":  result["dominant_state"],
            "composite": result["composite_100"],
            "action":    result["action"],
            "weights":   result["weights"],
        })
        if len(history) > _MAX_HISTORY:
            history = history[-_MAX_HISTORY:]
        store.set_meta(_WEIGHT_HISTORY_KEY, history)
    except Exception as e:
        logger.warning(f"record_weight_history: {e}")

def get_weight_history() -> list:
    try:
        from state_store import store
        return store.get_meta(_WEIGHT_HISTORY_KEY, default=[])
    except Exception as e:
        logger.warning(f"get_weight_history: {e}")
        return []
