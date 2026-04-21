"""
ai_analyst.py — Grok AI 分析模組
負責：川普發文監控、市場情緒解讀、訊號綜合研判
所有 AI 分析都會標注來源，確保透明度
"""

import requests
import json
import re
import logging
from datetime import datetime, timezone
from typing import Optional, Dict
from config import GROK_API_KEY

logger = logging.getLogger(__name__)

GROK_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3-latest"


def _call_grok(prompt: str, max_tokens: int = 800, use_search: bool = False) -> Optional[str]:
    """
    呼叫 Grok API
    use_search=True 時啟用 X/網路即時搜尋
    """
    if not GROK_API_KEY:
        logger.warning("No Grok API key configured")
        return None

    payload = {
        "model": GROK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }

    # 啟用即時搜尋（用於川普發文監控）
    if use_search:
        payload["tools"] = [{"type": "web_search"}]

    try:
        r = requests.post(
            GROK_URL,
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        else:
            logger.error(f"Grok API error: {r.status_code} — {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Grok call failed: {e}")
        return None


def _parse_json_response(text: str) -> Optional[Dict]:
    """安全地解析 AI 回傳的 JSON"""
    if not text:
        return None
    try:
        clean = re.sub(r"```json|```", "", text).strip()
        return json.loads(clean)
    except Exception:
        return None


# ═══════════════════════════════════════════
# 川普發文監控
# ═══════════════════════════════════════════

def monitor_trump_posts() -> Dict:
    """
    使用 Grok 的 X 即時數據監控川普最新發文
    重要：附上原文，AI 解讀只作參考
    """
    prompt = """Search X (Twitter/Truth Social) for Donald Trump's most recent posts 
from the last 12 hours. Look for posts about:
- Tariffs, trade war, China, EU, Taiwan
- Federal Reserve, interest rates, Jerome Powell  
- Geopolitical events, military actions
- Cryptocurrency, Bitcoin
- Economic policies

If no relevant posts found, say so clearly.

Respond ONLY in this JSON format (no other text):
{
  "has_impact_posts": true or false,
  "posts": [
    {
      "original_text": "exact quote from Trump's post",
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
  "key_warning": "most important warning for traders in Traditional Chinese (max 80 chars)"
}"""

    result = _call_grok(prompt, max_tokens=1000, use_search=True)
    data   = _parse_json_response(result)

    if not data:
        return {
            "has_impact_posts":   False,
            "posts":              [],
            "overall_market_mood": "neutral",
            "key_warning":        "",
            "ai_available":       False,
            "fetched_at":         datetime.now(timezone.utc).isoformat(),
        }

    data["ai_available"] = True
    data["fetched_at"]   = datetime.now(timezone.utc).isoformat()

    # 安全標注：確保每條解讀都有 AI 標記
    for post in data.get("posts", []):
        post["disclaimer"] = "⚠️ 此為 AI 解讀，非官方確認。請自行判斷是否採信。"

    return data


# ═══════════════════════════════════════════
# 訊號 AI 研判
# ═══════════════════════════════════════════

def analyze_signal(signal: Dict, macro_data: Dict, trump_data: Dict) -> Dict:
    """
    對技術面已生成的訊號進行 AI 宏觀面驗證
    AI 不負責技術面（那是 indicators.py 的工作）
    AI 只負責判斷宏觀背景是否支持這個訊號
    """
    if not signal:
        return {"ai_available": False}

    symbol    = signal.get("symbol", "")
    direction = signal.get("direction", "")
    score     = signal.get("score", 0)

    # 整理輸入給 AI 的數據
    vix_val  = macro_data.get("vix",  {}).get("price", "N/A") if macro_data.get("vix") else "N/A"
    dxy_chg  = macro_data.get("dxy",  {}).get("chg",   "N/A") if macro_data.get("dxy") else "N/A"
    fg_score = macro_data.get("fear_greed", {}).get("score", 50)
    trump_warning = trump_data.get("key_warning", "無重大發文")

    prompt = f"""You are a professional financial analyst reviewing a trading signal.

SIGNAL:
- Asset: {signal.get('name', symbol)}
- Direction: {direction} ({"Buy/Long" if direction == "buy" else "Sell/Short"})
- Technical Score: {score}/100
- Entry: {signal.get('entry_price')}
- Stop Loss: {signal.get('stop_loss')}
- TP1: {signal.get('tp1')} (RR {signal.get('rr1')})
- TP2: {signal.get('tp2')} (RR {signal.get('rr2')})
- Conditions Met: {', '.join(signal.get('conditions_met', []))}

MACRO CONTEXT:
- VIX: {vix_val}
- DXY Change: {dxy_chg}%
- Fear & Greed: {fg_score}/100
- Trump/News Warning: {trump_warning}

TASK: Evaluate if macro conditions SUPPORT or CONTRADICT this technical signal.
Be brief and direct. Focus on facts, not speculation.

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
    data   = _parse_json_response(result)

    if not data:
        return {
            "ai_available":          False,
            "macro_supports_signal": True,  # 預設不阻擋
            "macro_score_adjustment": 0,
            "final_recommendation":  "等待",
            "recommendation_reason": "AI 分析暫不可用",
        }

    data["ai_available"] = True
    data["analyzed_at"]  = datetime.now(timezone.utc).isoformat()
    data["disclaimer"]   = "⚠️ AI 宏觀分析僅供參考，不構成投資建議"

    return data


# ═══════════════════════════════════════════
# 每日市場摘要
# ═══════════════════════════════════════════

def generate_daily_briefing(macro_data: Dict, trump_data: Dict) -> Dict:
    """
    每日開盤前的市場摘要
    整合宏觀數據 + 川普動態，給出今日交易建議
    """
    vix_val  = macro_data.get("vix",  {}).get("price",  "N/A") if macro_data.get("vix") else "N/A"
    dxy_val  = macro_data.get("dxy",  {}).get("price",  "N/A") if macro_data.get("dxy") else "N/A"
    dxy_chg  = macro_data.get("dxy",  {}).get("chg",    "N/A") if macro_data.get("dxy") else "N/A"
    fg_score = macro_data.get("fear_greed", {}).get("score",   50)
    fg_label = macro_data.get("fear_greed", {}).get("label_zh", "中性")
    gold_chg = macro_data.get("gold", {}).get("chg",    "N/A") if macro_data.get("gold") else "N/A"
    sp500_chg= macro_data.get("sp500",{}).get("chg",    "N/A") if macro_data.get("sp500") else "N/A"
    btc_chg  = macro_data.get("btc",  {}).get("chg",    "N/A") if macro_data.get("btc") else "N/A"

    trump_posts = trump_data.get("posts", [])
    trump_summary = trump_data.get("key_warning", "過去 12 小時無重大發文")

    prompt = f"""You are a professional trading desk analyst. 
Generate a concise daily market briefing in Traditional Chinese.

MARKET DATA:
- VIX: {vix_val}
- DXY (USD Index): {dxy_val} ({dxy_chg}%)
- Fear & Greed: {fg_score}/100 ({fg_label})
- Gold: {gold_chg}%
- S&P 500: {sp500_chg}%
- Bitcoin: {btc_chg}%

TRUMP/NEWS FACTOR: {trump_summary}
Trump posts count (12h): {len(trump_posts)}

Provide a practical briefing for a retail CFD trader using Mitrade.
Focus on: EUR/USD, GBP/USD, USD/JPY, XAUUSD, WTI Oil, BTC/USD

Respond ONLY in JSON:
{{
  "overall_environment": "適合交易" or "謹慎交易" or "今日觀望",
  "environment_reason": "reason in Traditional Chinese (max 80 chars)",
  "best_opportunities": ["EURUSD 偏多", "XAUUSD 觀察"],
  "avoid_today": ["WTI 今日避開"],
  "key_levels_watch": ["XAUUSD 支撐 XXXX", "EURUSD 壓力 X.XXXX"],
  "trump_impact": "brief assessment in Traditional Chinese (max 60 chars)",
  "top_risk_today": "biggest risk in Traditional Chinese (max 60 chars)",
  "session_advice": {{
    "asia":    "advice in Traditional Chinese",
    "london":  "advice in Traditional Chinese",
    "newyork": "advice in Traditional Chinese"
  }}
}}"""

    result = _call_grok(prompt, max_tokens=1000)
    data   = _parse_json_response(result)

    if not data:
        return {
            "ai_available":      False,
            "overall_environment": "謹慎交易",
            "environment_reason": "AI 分析暫不可用，請自行判斷",
            "best_opportunities": [],
            "avoid_today":        [],
        }

    data["ai_available"] = True
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["disclaimer"]   = "⚠️ AI 每日簡報僅供參考，不構成投資建議"

    return data
