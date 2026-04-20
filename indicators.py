"""
indicators.py — 技術指標計算模組
所有指標都從原始 K 線數據計算，不依賴 AI 猜測
計算結果會附上可靠性標記
"""

import math
import logging
from typing import List, Optional, Dict
from config import INDICATOR_PARAMS as P

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 基礎計算函數
# ═══════════════════════════════════════════

def ema(prices: List[float], period: int) -> Optional[float]:
    """指數移動平均線"""
    if not prices or len(prices) < period:
        return None
    k = 2 / (period + 1)
    result = prices[0]
    for p in prices[1:]:
        result = p * k + result * (1 - k)
    return round(result, 6)

def ema_series(prices: List[float], period: int) -> List[Optional[float]]:
    """返回完整 EMA 序列"""
    if not prices or len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    result = [None] * len(prices)
    result[0] = prices[0]
    for i in range(1, len(prices)):
        result[i] = prices[i] * k + result[i-1] * (1 - k)
    return result

def sma(prices: List[float], period: int) -> Optional[float]:
    """簡單移動平均線"""
    if not prices or len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 6)

def stdev(prices: List[float], period: int) -> Optional[float]:
    """標準差"""
    if not prices or len(prices) < period:
        return None
    subset = prices[-period:]
    mean = sum(subset) / period
    variance = sum((x - mean) ** 2 for x in subset) / period
    return round(math.sqrt(variance), 6)


# ═══════════════════════════════════════════
# 主要指標計算
# ═══════════════════════════════════════════

def calc_ema_system(closes: List[float]) -> Dict:
    """
    EMA 系統：計算四條均線與排列狀態
    判斷：多頭排列、空頭排列、還是混亂
    """
    if len(closes) < 210:  # 需要至少 200 根
        return {"valid": False, "reason": "數據不足"}

    e9   = ema(closes, P["ema_fast"])
    e21  = ema(closes, P["ema_mid"])
    e50  = ema(closes, P["ema_slow"])
    e200 = ema(closes, P["ema_trend"])

    if None in [e9, e21, e50, e200]:
        return {"valid": False, "reason": "計算失敗"}

    price = closes[-1]

    # 判斷均線排列
    if e9 > e21 > e50 > e200:
        alignment = "完美多頭排列"
        bias = "bullish"
        score = 3  # 最強
    elif e9 > e21 > e50:
        alignment = "多頭排列"
        bias = "bullish"
        score = 2
    elif e9 < e21 < e50 < e200:
        alignment = "完美空頭排列"
        bias = "bearish"
        score = -3
    elif e9 < e21 < e50:
        alignment = "空頭排列"
        bias = "bearish"
        score = -2
    else:
        alignment = "混亂（無明確趨勢）"
        bias = "neutral"
        score = 0

    # 價格相對均線位置
    above_e21  = price > e21
    above_e50  = price > e50
    above_e200 = price > e200

    # 均線交叉（最近兩根）
    if len(closes) >= 2:
        e9_prev  = ema(closes[:-1], P["ema_fast"])
        e21_prev = ema(closes[:-1], P["ema_mid"])
        if e9_prev and e21_prev:
            if e9_prev < e21_prev and e9 > e21:
                cross = "金叉（EMA9 穿越 EMA21）"
            elif e9_prev > e21_prev and e9 < e21:
                cross = "死叉（EMA9 跌破 EMA21）"
            else:
                cross = "無交叉"
        else:
            cross = "無交叉"
    else:
        cross = "無交叉"

    return {
        "valid":       True,
        "e9":          e9,
        "e21":         e21,
        "e50":         e50,
        "e200":        e200,
        "alignment":   alignment,
        "bias":        bias,
        "score":       score,
        "cross":       cross,
        "above_e21":   above_e21,
        "above_e50":   above_e50,
        "above_e200":  above_e200,
    }


def calc_rsi(closes: List[float]) -> Dict:
    """
    RSI 14 計算
    判斷：超買、超賣、多頭健康區、空頭健康區
    """
    period = P["rsi_period"]
    if len(closes) < period + 2:
        return {"valid": False, "reason": "數據不足"}

    gains  = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[-(period + 1 - i + 1)] - closes[-(period + 1 - i)]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs  = avg_gain / avg_loss
        rsi = round(100 - 100 / (1 + rs), 2)

    # 判斷狀態
    if rsi >= P["rsi_overbought"]:
        status = "超買"
        bias   = "bearish_warning"
        score  = -1
    elif rsi <= P["rsi_oversold"]:
        status = "超賣"
        bias   = "bullish_warning"
        score  = 1
    elif rsi >= P["rsi_bull_zone"]:
        status = "多頭健康區"
        bias   = "bullish"
        score  = 1
    elif rsi <= P["rsi_bear_zone"]:
        status = "空頭健康區"
        bias   = "bearish"
        score  = -1
    else:
        status = "中性"
        bias   = "neutral"
        score  = 0

    return {
        "valid":  True,
        "value":  rsi,
        "status": status,
        "bias":   bias,
        "score":  score,
    }


def calc_macd(closes: List[float]) -> Dict:
    """
    MACD 計算
    判斷：柱狀圖方向、信號線交叉
    """
    if len(closes) < P["macd_slow"] + P["macd_signal"] + 5:
        return {"valid": False, "reason": "數據不足"}

    ema_fast_series = ema_series(closes, P["macd_fast"])
    ema_slow_series = ema_series(closes, P["macd_slow"])

    # MACD 線
    macd_line = []
    for i in range(len(closes)):
        if ema_fast_series[i] is not None and ema_slow_series[i] is not None:
            macd_line.append(ema_fast_series[i] - ema_slow_series[i])
        else:
            macd_line.append(None)

    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < P["macd_signal"]:
        return {"valid": False, "reason": "MACD 數據不足"}

    # 信號線（MACD 的 EMA）
    signal_line = ema(valid_macd, P["macd_signal"])
    if signal_line is None:
        return {"valid": False, "reason": "信號線計算失敗"}

    current_macd = valid_macd[-1]
    histogram    = round(current_macd - signal_line, 6)

    # 前一根柱狀圖（判斷方向）
    if len(valid_macd) >= 2:
        prev_signal = ema(valid_macd[:-1], P["macd_signal"])
        prev_histogram = valid_macd[-2] - prev_signal if prev_signal else 0
    else:
        prev_histogram = 0

    # 判斷狀態
    hist_growing = histogram > prev_histogram
    hist_positive = histogram > 0

    if hist_positive and hist_growing:
        status = "多頭動能增強"
        bias   = "bullish"
        score  = 2
    elif hist_positive and not hist_growing:
        status = "多頭動能減弱"
        bias   = "bullish_weak"
        score  = 1
    elif not hist_positive and not hist_growing:
        status = "空頭動能增強"
        bias   = "bearish"
        score  = -2
    else:
        status = "空頭動能減弱"
        bias   = "bearish_weak"
        score  = -1

    # 交叉判斷
    if len(valid_macd) >= 2 and prev_signal:
        prev_hist = valid_macd[-2] - prev_signal
        if prev_hist < 0 and histogram > 0:
            cross = "MACD 金叉"
        elif prev_hist > 0 and histogram < 0:
            cross = "MACD 死叉"
        else:
            cross = "無交叉"
    else:
        cross = "無交叉"

    return {
        "valid":     True,
        "macd":      round(current_macd, 6),
        "signal":    round(signal_line, 6),
        "histogram": histogram,
        "status":    status,
        "bias":      bias,
        "score":     score,
        "cross":     cross,
        "hist_growing": hist_growing,
    }


def calc_atr(highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """
    ATR 14 — 真實波動幅度
    用於計算止損距離
    """
    period = P["atr_period"]
    if len(closes) < period + 2:
        return {"valid": False, "reason": "數據不足"}

    true_ranges = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return {"valid": False, "reason": "TR 數據不足"}

    # 使用 Wilder 平滑法
    atr_val = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period

    current_price = closes[-1]
    atr_pct = round((atr_val / current_price) * 100, 2)

    return {
        "valid":   True,
        "value":   round(atr_val, 6),
        "pct":     atr_pct,          # ATR 佔當前價格的百分比
        "price":   current_price,
    }


def calc_bollinger(closes: List[float]) -> Dict:
    """
    布林帶（20, 2）
    判斷：突破上軌、突破下軌、回測中軌
    """
    period = P["bb_period"]
    std_mult = P["bb_std"]

    if len(closes) < period:
        return {"valid": False, "reason": "數據不足"}

    ma20   = sma(closes, period)
    std20  = stdev(closes, period)

    if ma20 is None or std20 is None:
        return {"valid": False, "reason": "計算失敗"}

    upper = round(ma20 + std_mult * std20, 6)
    lower = round(ma20 - std_mult * std20, 6)
    price = closes[-1]

    # 帶寬（波動度指標）
    bandwidth = round((upper - lower) / ma20 * 100, 2)

    # 位置判斷
    if price >= upper:
        position = "突破上軌"
        bias      = "overbought"
        score     = -1
    elif price <= lower:
        position = "突破下軌"
        bias      = "oversold"
        score     = 1
    elif price > ma20:
        position = "中軌上方"
        bias      = "bullish"
        score     = 1
    else:
        position = "中軌下方"
        bias      = "bearish"
        score     = -1

    return {
        "valid":     True,
        "upper":     upper,
        "mid":       ma20,
        "lower":     lower,
        "price":     price,
        "position":  position,
        "bias":      bias,
        "score":     score,
        "bandwidth": bandwidth,
    }


def calc_volume_ratio(volumes: List[float]) -> Dict:
    """
    成交量比（當前量 / 20日均量）
    > 1.5 = 量增，< 0.7 = 量縮
    """
    period = P["vol_period"]
    if not volumes or len(volumes) < period + 1:
        return {"valid": False, "reason": "數據不足"}

    avg_vol  = sum(volumes[-period-1:-1]) / period
    curr_vol = volumes[-1]

    if avg_vol == 0:
        return {"valid": False, "reason": "均量為零"}

    ratio = round(curr_vol / avg_vol, 2)

    if ratio >= 2.0:
        status = "爆量"
        bias   = "confirm"
        score  = 2
    elif ratio >= 1.5:
        status = "量增"
        bias   = "confirm"
        score  = 1
    elif ratio <= 0.5:
        status = "極度萎縮"
        bias   = "weak"
        score  = -1
    elif ratio <= 0.7:
        status = "量縮"
        bias   = "weak"
        score  = 0
    else:
        status = "正常量"
        bias   = "neutral"
        score  = 0

    return {
        "valid":    True,
        "ratio":    ratio,
        "current":  int(curr_vol),
        "average":  int(avg_vol),
        "status":   status,
        "bias":     bias,
        "score":    score,
    }


# ═══════════════════════════════════════════
# 綜合計算（一次計算全部指標）
# ═══════════════════════════════════════════

def calc_all_indicators(data: dict) -> dict:
    """
    輸入一個時框的 K 線數據
    回傳所有技術指標結果
    """
    closes  = data.get("closes",  [])
    opens   = data.get("opens",   [])
    highs   = data.get("highs",   [])
    lows    = data.get("lows",    [])
    volumes = data.get("volumes", [])

    if len(closes) < 30:
        return {"valid": False, "reason": "K 線數量不足"}

    ema_sys = calc_ema_system(closes)
    rsi     = calc_rsi(closes)
    macd    = calc_macd(closes)
    atr     = calc_atr(highs, lows, closes)
    bb      = calc_bollinger(closes)
    vol     = calc_volume_ratio(volumes)

    # 計算綜合偏向分數
    total_score = 0
    valid_count = 0

    for ind in [ema_sys, rsi, macd, bb]:
        if ind.get("valid") and ind.get("score") is not None:
            total_score += ind["score"]
            valid_count += 1

    if valid_count == 0:
        overall_bias = "neutral"
    elif total_score >= 3:
        overall_bias = "strong_bullish"
    elif total_score >= 1:
        overall_bias = "bullish"
    elif total_score <= -3:
        overall_bias = "strong_bearish"
    elif total_score <= -1:
        overall_bias = "bearish"
    else:
        overall_bias = "neutral"

    return {
        "valid":        True,
        "ema":          ema_sys,
        "rsi":          rsi,
        "macd":         macd,
        "atr":          atr,
        "bb":           bb,
        "volume":       vol,
        "total_score":  total_score,
        "overall_bias": overall_bias,
        "current_price": closes[-1] if closes else None,
    }
