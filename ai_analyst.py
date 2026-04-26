"""
ai_analyst.py v5.2 — Grok AI 分析模組
"""
import requests, json, re, logging
from datetime import datetime, timezone
from typing import Optional, Dict
from config import GROK_API_KEY

logger     = logging.getLogger(__name__)
GROK_URL   = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3-latest"


def _call_grok(prompt: str, max_tokens: int = 800) -> Optional[str]:
    if not GROK_API_KEY:
        logger.warning("No Grok API key")
        return None
    payload = {
        "model":      GROK_MODEL,
        "messages":   [{"role":"user","content":prompt}],
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(
            GROK_URL,
            headers={"Authorization":f"Bearer {GROK_API_KEY}","Content-Type":"application/json"},
            json=payload, timeout=30,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        logger.error(f"Grok API {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Grok call failed: {e}")
        return None


def _parse_json(text: str) -> Optional[Dict]:
    if not text: return None
    try:
        clean = re.sub(r"```json|```","",text).strip()
        return json.loads(clean)
    except: return None


# ══════════════════════════════════════════
# 川普事件分類系統
# ══════════════════════════════════════════

TRUMP_EVENT_KEYWORDS = {
    "tariff_broad":    ["tariff","trade war","liberation day","reciprocal","import tax","universal tariff"],
    "tariff_china":    ["china tariff","chinese goods","trade deficit china","fentanyl","beijing"],
    "crypto_friendly": ["bitcoin","crypto","digital asset","blockchain","btc","strategic reserve","defi"],
    "crypto_hostile":  ["ban crypto","regulate bitcoin","crypto crackdown","digital currency ban"],
    "tax_cut":         ["tax cut","tax reform","corporate tax","tcja","extend tax","no tax","tax break"],
    "fed_pressure":    ["federal reserve","fed chair","powell","interest rate","cut rates","monetary"],
    "geopolitical":    ["sanction","military","war","nato","ukraine","middle east","iran","taiwan","china military"],
}

def classify_trump_event(text: str) -> str:
    if not text: return "other"
    text_lower = text.lower()
    for event_type, keywords in TRUMP_EVENT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return event_type
    return "other"

def get_trump_market_impact(event_type: str) -> dict:
    impacts = {
        "tariff_broad":    {"XAUUSD":"↑強多","US500":"↓警戒","NAS100":"↓警戒","HK50":"↓空","BTCUSD":"↓觀望"},
        "tariff_china":    {"XAUUSD":"↑多","HK50":"↓空","NAS100":"↓觀望","USDJPY":"↑多"},
        "crypto_friendly": {"BTCUSD":"↑強多","ETHUSD":"↑強多"},
        "crypto_hostile":  {"BTCUSD":"↓止損","ETHUSD":"↓止損"},
        "tax_cut":         {"US500":"↑多","NAS100":"↑多","NVDA":"↑強多","AAPL":"↑多"},
        "fed_pressure":    {"XAUUSD":"↑多","EURUSD":"↑觀望","USDJPY":"↓觀望"},
        "geopolitical":    {"XAUUSD":"↑強多","WTI":"↑多","US500":"↓警戒"},
    }
    return impacts.get(event_type, {})


# ══════════════════════════════════════════
# 川普發文監控
# ══════════════════════════════════════════

def monitor_trump_posts() -> Dict:
    prompt = """Based on your knowledge of recent events and Donald Trump's posts on X and Truth Social 
in the last 12-24 hours, identify any significant posts related to:
- Tariffs, trade war, China, EU, Taiwan
- Federal Reserve, interest rates, Jerome Powell
- Geopolitical events, military actions
- Cryptocurrency, Bitcoin
- Economic policies

If you don't have recent information or no relevant posts, say so clearly.

Respond ONLY in this JSON format:
{
  "has_impact_posts": true or false,
  "posts": [
    {
      "original_text": "summary of Trump's post content",
      "source": "Truth Social or X",
      "topic": "tariffs/fed/geo/crypto/other",
      "market_impact": "bullish/bearish/neutral",
      "affected_assets": ["XAUUSD", "EURUSD"],
      "impact_level": "high/medium/low",
      "ai_interpretation": "brief interpretation in Traditional Chinese (max 50 chars)",
      "confidence": 0-100
    }
  ],
  "overall_market_mood": "risk-on/risk-off/neutral",
  "key_warning": "most important warning in Traditional Chinese (max 80 chars)"
}"""

    result = _call_grok(prompt, max_tokens=1000)
    data   = _parse_json(result)

    if not data:
        return {
            "has_impact_posts":    False,
            "posts":               [],
            "overall_market_mood": "neutral",
            "key_warning":         "",
            "ai_available":        False,
            "fetched_at":          datetime.now(timezone.utc).isoformat(),
        }

    data["ai_available"] = True
    data["fetched_at"]   = datetime.now(timezone.utc).isoformat()

    main_event_type = "none"
    for post in data.get("posts",[]):
        post["disclaimer"] = "⚠️ 此為 AI 解讀，非官方確認。請自行判斷。"
        et = classify_trump_event(
            post.get("original_text","") + " " + post.get("ai_interpretation","")
        )
        post["event_type"] = et
        post["market_impact_detail"] = get_trump_market_impact(et)
        if et != "other" and main_event_type == "none":
            main_event_type = et

    data["main_event_type"] = main_event_type
    data["market_impacts"]  = get_trump_market_impact(main_event_type)

    return data


# ══════════════════════════════════════════
# 訊號 AI 研判
# ══════════════════════════════════════════

def analyze_signal(signal: dict, macro_data: dict, trump_data: dict) -> Dict:
    if not signal: return {"ai_available":False}

    symbol    = signal.get("symbol","")
    direction = signal.get("direction","")
    score     = signal.get("score",0)

    vix_val  = macro_data.get("vix",{}).get("price","N/A") if macro_data.get("vix") else "N/A"
    dxy_chg  = macro_data.get("dxy",{}).get("chg","N/A")   if macro_data.get("dxy") else "N/A"
    fg_score = macro_data.get("fear_greed",{}).get("score",50)
    fred     = macro_data.get("fred",{})
    fed_rate = fred.get("fed_rate",{}).get("value","N/A") if fred.get("fed_rate") else "N/A"
    trump_w  = trump_data.get("key_warning","無重大發文") if trump_data else "無重大發文"

    prompt = f"""You are a professional financial analyst reviewing a CFD trading signal.

SIGNAL:
- Asset: {signal.get('name',symbol)}
- Direction: {direction} ({"Buy/Long" if direction=='buy' else "Sell/Short"})
- Technical Score: {score}/100
- Entry: {signal.get('entry_price')} | SL: {signal.get('stop_loss')} | TP1: {signal.get('tp1')} (RR {signal.get('rr1')})
- Conditions Met: {', '.join(signal.get('conditions_met',[]))}

MACRO CONTEXT:
- VIX: {vix_val}
- DXY Change: {dxy_chg}%
- Fear & Greed: {fg_score}/100
- Fed Rate: {fed_rate}%
- Trump/News: {trump_w}

Evaluate if macro conditions SUPPORT or CONTRADICT this technical signal.

Respond ONLY in JSON:
{{
  "macro_supports_signal": true or false,
  "macro_score_adjustment": -20 to +20,
  "key_supporting_factors": ["factor1"],
  "key_risk_factors": ["risk1"],
  "final_recommendation": "進場" or "等待" or "跳過",
  "recommendation_reason": "reason in Traditional Chinese (max 60 chars)",
  "confidence_adjustment": -10 to +10
}}"""

    result = _call_grok(prompt, max_tokens=600)
    data   = _parse_json(result)

    if not data:
        return {"ai_available":False,"macro_supports_signal":True,
                "macro_score_adjustment":0,"final_recommendation":"等待",
                "recommendation_reason":"AI 分析暫不可用"}

    data["ai_available"] = True
    data["analyzed_at"]  = datetime.now(timezone.utc).isoformat()
    data["disclaimer"]   = "⚠️ AI 宏觀分析僅供參考，不構成投資建議"
    return data


# ══════════════════════════════════════════
# 每日市場簡報
# ══════════════════════════════════════════

def generate_daily_briefing(macro_data: dict, trump_data: dict) -> Dict:
    vix_val  = macro_data.get("vix",{}).get("price","N/A")  if macro_data and macro_data.get("vix")  else "N/A"
    dxy_val  = macro_data.get("dxy",{}).get("price","N/A")  if macro_data and macro_data.get("dxy")  else "N/A"
    dxy_chg  = macro_data.get("dxy",{}).get("chg","N/A")    if macro_data and macro_data.get("dxy")  else "N/A"
    fg_score = macro_data.get("fear_greed",{}).get("score",50) if macro_data else 50
    fg_label = macro_data.get("fear_greed",{}).get("label_zh","中性") if macro_data else "中性"
    gold_chg = macro_data.get("gold",{}).get("chg","N/A")   if macro_data and macro_data.get("gold") else "N/A"
    sp500_chg= macro_data.get("sp500",{}).get("chg","N/A")  if macro_data and macro_data.get("sp500") else "N/A"
    btc_chg  = macro_data.get("btc",{}).get("chg","N/A")    if macro_data and macro_data.get("btc")  else "N/A"
    fred     = macro_data.get("fred",{}) if macro_data else {}
    fed_rate = fred.get("fed_rate",{}).get("value","N/A") if fred.get("fed_rate") else "N/A"
    yc_label = fred.get("yield_curve",{}).get("label_zh","") if fred.get("yield_curve") else ""

    trump_posts   = trump_data.get("posts",[]) if trump_data else []
    trump_summary = trump_data.get("key_warning","過去 24 小時無重大發文") if trump_data else "無數據"
    futures = macro_data.get("us_futures",{}) if macro_data else {}
    futures_label = futures.get("overall",{}).get("label","") if futures.get("overall") else ""

    prompt = f"""You are a professional trading desk analyst.
Generate a concise daily market briefing in Traditional Chinese for a retail CFD trader using Mitrade.

MARKET DATA:
- VIX: {vix_val} | DXY: {dxy_val} ({dxy_chg}%)
- Fear & Greed: {fg_score}/100 ({fg_label})
- Gold: {gold_chg}% | S&P 500: {sp500_chg}% | Bitcoin: {btc_chg}%
- Fed Rate: {fed_rate}% | Yield Curve: {yc_label}
- US Futures: {futures_label}
- Trump Factor: {trump_summary} (posts: {len(trump_posts)})

Provide practical briefing for EUR/USD, GBP/USD, USD/JPY, XAUUSD, WTI, BTC/USD, US500, NVDA.

Respond ONLY in JSON:
{{
  "overall_environment": "適合交易" or "謹慎交易" or "今日觀望",
  "environment_reason": "reason in Traditional Chinese (max 80 chars)",
  "best_opportunities": ["EURUSD 偏多", "XAUUSD 觀察"],
  "avoid_today": ["WTI 避開"],
  "key_levels_watch": ["XAUUSD 支撐 XXXX"],
  "trump_impact": "brief in Traditional Chinese (max 60 chars)",
  "top_risk_today": "biggest risk in Traditional Chinese (max 60 chars)",
  "session_advice": {{
    "asia": "advice",
    "london": "advice",
    "newyork": "advice"
  }}
}}"""

    result = _call_grok(prompt, max_tokens=1000)
    data   = _parse_json(result)

    if not data:
        return {"ai_available":False,"overall_environment":"謹慎交易",
                "environment_reason":"AI 分析暫不可用，請自行判斷","best_opportunities":[],"avoid_today":[]}

    data["ai_available"] = True
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["disclaimer"]   = "⚠️ AI 每日簡報僅供參考，不構成投資建議"
    return data


# ══════════════════════════════════════════
# 宏觀週期分析
# ══════════════════════════════════════════

def macro_cycle_analysis(fred_data: dict, macro_data: dict) -> Dict:
    fed_rate = fred_data.get("fed_rate",{}).get("value",0) if fred_data.get("fed_rate") else 0
    cpi      = fred_data.get("cpi",{}).get("value",0)      if fred_data.get("cpi")      else 0
    unemp    = fred_data.get("unemployment",{}).get("value",0) if fred_data.get("unemployment") else 0
    yc       = fred_data.get("yield_curve",{}).get("inverted",False) if fred_data.get("yield_curve") else False

    prompt = f"""Based on the following economic data, determine the current macroeconomic cycle phase.

DATA:
- Fed Funds Rate: {fed_rate}%
- CPI (YoY): {cpi}%
- Unemployment: {unemp}%
- Yield Curve Inverted: {yc}

Respond ONLY in JSON:
{{
  "cycle_phase": "升息週期" or "降息週期" or "衰退期" or "復甦期" or "滯脹期",
  "phase_en": "tightening/easing/recession/recovery/stagflation",
  "implication_zh": "brief implication in Traditional Chinese (max 80 chars)",
  "favored_assets": ["XAUUSD", "EURUSD"],
  "avoid_assets": ["US500"],
  "confidence": 0-100
}}"""

    result = _call_grok(prompt, max_tokens=400)
    data   = _parse_json(result)
    if not data: return {"ai_available":False}
    data["ai_available"] = True
    return data
