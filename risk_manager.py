"""
risk_manager.py — 風控與熔斷機制
這是系統的安全層，確保不在危險環境發訊號
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List
from config import CIRCUIT_BREAKER as CB, SYMBOLS

logger = logging.getLogger(__name__)

# 記錄暫停狀態
_paused_symbols: Dict[str, datetime] = {}   # 品種 → 暫停到幾點
_system_paused: Optional[datetime] = None   # 整個系統暫停到幾點
_pause_reasons: List[str] = []              # 暫停原因紀錄

# EIA 數據發布時間（UTC）— 每週三 14:30 UTC
EIA_SCHEDULE = {
    "weekday": 2,    # 週三（0=週一）
    "hour":    14,
    "minute":  30,
}


def check_vix_circuit_breaker(macro_data: Dict) -> Dict:
    """
    VIX 熔斷檢查
    VIX > 40 → 極端恐慌，停止所有訊號
    VIX > 30 → 高恐慌，降低訊號等級
    """
    vix_data = macro_data.get("vix")
    if not vix_data:
        return {"triggered": False, "level": "normal"}

    vix_val = vix_data.get("price", 0)

    if vix_val >= CB["vix_extreme"]:
        return {
            "triggered": True,
            "level":     "extreme",
            "value":     vix_val,
            "message":   f"🚨 VIX {vix_val:.1f} 極度恐慌！所有訊號暫停",
            "action":    "stop_all",
        }
    elif vix_val >= CB["vix_threshold"]:
        return {
            "triggered": True,
            "level":     "high",
            "value":     vix_val,
            "message":   f"⚠️ VIX {vix_val:.1f} 偏高，今日謹慎交易",
            "action":    "reduce_confidence",
        }
    else:
        return {
            "triggered": False,
            "level":     "normal",
            "value":     vix_val,
        }


def check_eia_schedule() -> Dict:
    """
    檢查是否接近 EIA 原油庫存報告時間（每週三 14:30 UTC）
    前後 2 小時暫停石油訊號
    """
    now = datetime.now(timezone.utc)

    if now.weekday() != EIA_SCHEDULE["weekday"]:
        return {"near_eia": False}

    eia_time = now.replace(
        hour   = EIA_SCHEDULE["hour"],
        minute = EIA_SCHEDULE["minute"],
        second = 0, microsecond=0
    )

    pause_minutes = CB["eia_pause_minutes"]
    window_start  = eia_time - timedelta(minutes=pause_minutes)
    window_end    = eia_time + timedelta(minutes=pause_minutes)

    if window_start <= now <= window_end:
        return {
            "near_eia": True,
            "eia_time": eia_time.strftime("%H:%M UTC"),
            "message":  f"⚠️ EIA 原油庫存報告時間前後，石油訊號暫停",
        }

    return {"near_eia": False}


def check_price_spike(symbol: str, tf_data: Dict) -> Dict:
    """
    檢查品種是否有異常短期暴漲暴跌
    1H 內變動超過 3% → 暫停該品種
    """
    entry_data = tf_data.get("entry", {})
    closes     = entry_data.get("closes", [])

    if len(closes) < 3:
        return {"spike": False}

    recent_high = max(closes[-3:])
    recent_low  = min(closes[-3:])
    recent_range_pct = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0

    if recent_range_pct >= CB["price_spike_pct"]:
        return {
            "spike":   True,
            "range":   round(recent_range_pct, 2),
            "message": f"⚠️ {symbol} 近期波動 {recent_range_pct:.1f}%，訊號暫停",
        }

    return {"spike": False}


def validate_signal_freshness(signal: Dict) -> Dict:
    """
    檢查訊號是否仍在有效期內
    超過 4 小時的訊號標記為過期
    """
    if not signal:
        return {"valid": False, "reason": "空訊號"}

    generated_at = signal.get("generated_at")
    if not generated_at:
        return {"valid": True}  # 沒有時間戳就不過期

    try:
        gen_time = datetime.fromisoformat(generated_at)
        age_hours = (datetime.now(timezone.utc) - gen_time).total_seconds() / 3600

        if age_hours > CB["signal_expire_hours"]:
            return {
                "valid":   False,
                "reason":  f"訊號已過期（產生於 {age_hours:.1f} 小時前）",
                "age_hrs": round(age_hours, 1),
            }

        return {"valid": True, "age_hrs": round(age_hours, 1)}
    except Exception:
        return {"valid": True}


def check_signal_price_drift(signal: Dict, current_price: float) -> Dict:
    """
    檢查自訊號產生後，進場點是否已大幅偏移
    如果當前價格距離進場點超過 2 ATR → 訊號失效
    """
    entry_price = signal.get("entry_price", 0)
    atr         = signal.get("atr", 0)
    direction   = signal.get("direction", "")

    if not entry_price or not atr:
        return {"drifted": False}

    distance = abs(current_price - entry_price)

    if distance > atr * 2:
        return {
            "drifted": True,
            "message": f"⚠️ 進場點已偏移 {distance:.4f}（超過 2 ATR），此訊號已失效，請勿追價",
        }

    # 多單訊號，但價格已大幅下跌 → 提醒
    if direction == "buy" and current_price < entry_price - atr:
        return {
            "drifted":  False,
            "warning": "⚠️ 價格已跌破進場點，等待穩定後再確認",
        }

    return {"drifted": False}


def run_all_checks(symbol: str, tf_data: Dict, macro_data: Dict) -> Dict:
    """
    執行所有風控檢查
    回傳整體狀態：可發訊號 / 降級 / 暫停
    """
    checks = {
        "vix":       check_vix_circuit_breaker(macro_data),
        "eia":       check_eia_schedule() if SYMBOLS.get(symbol, {}).get("special_conditions") else {"near_eia": False},
        "spike":     check_price_spike(symbol, tf_data),
    }

    warnings  = []
    blockers  = []

    # VIX 極端 → 阻止所有訊號
    if checks["vix"].get("level") == "extreme":
        blockers.append(checks["vix"]["message"])

    # VIX 高 → 警告
    elif checks["vix"].get("level") == "high":
        warnings.append(checks["vix"]["message"])

    # EIA 時間 → 阻止石油訊號
    if checks["eia"].get("near_eia"):
        blockers.append(checks["eia"]["message"])

    # 價格異常波動 → 阻止
    if checks["spike"].get("spike"):
        blockers.append(checks["spike"]["message"])

    # 最終判決
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "clear"

    return {
        "status":   status,
        "warnings": warnings,
        "blockers": blockers,
        "checks":   checks,
        "can_signal": len(blockers) == 0,
    }


def get_system_status(macro_data: Dict) -> Dict:
    """
    取得整體系統狀態摘要
    用於 Web 儀表板顯示
    """
    vix_check = check_vix_circuit_breaker(macro_data)
    eia_check = check_eia_schedule()
    fg_data   = macro_data.get("fear_greed", {})
    session   = macro_data.get("session", {})

    vix_val   = macro_data.get("vix", {}).get("price", 0) if macro_data.get("vix") else 0
    fg_score  = fg_data.get("score", 50)

    # 整體環境評分
    env_score = 100

    if vix_val >= 40:
        env_score -= 60
        env_status = "極度危險"
        env_color  = "red"
    elif vix_val >= 30:
        env_score -= 30
        env_status = "高風險"
        env_color  = "orange"
    elif vix_val >= 20:
        env_score -= 10
        env_status = "正常偏高"
        env_color  = "yellow"
    else:
        env_status = "適合交易"
        env_color  = "green"

    if fg_score < 20 or fg_score > 80:
        env_score -= 10

    return {
        "env_score":  max(0, env_score),
        "env_status": env_status,
        "env_color":  env_color,
        "vix":        vix_val,
        "vix_status": vix_check.get("level", "normal"),
        "fg_score":   fg_score,
        "fg_label":   fg_data.get("label_zh", "中性"),
        "eia_today":  eia_check.get("near_eia", False),
        "can_trade":  env_score >= 50,
    }
