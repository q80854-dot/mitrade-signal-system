"""
signal_engine.py — 訊號產生引擎
整合多時框分析、計算進場點/止損/止盈
這裡是系統的策略核心
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from config import SYMBOLS, SIGNAL_THRESHOLDS as THRESH, ACCOUNT_BALANCE_USD, MAX_RISK_PER_TRADE
from indicators import calc_all_indicators

logger = logging.getLogger(__name__)


def calc_stop_loss(symbol: str, direction: str, entry_price: float,
                   atr_value: float, atr_mult: float) -> float:
    """
    依 ATR 計算止損價格
    多單：進場價 - ATR * 倍數
    空單：進場價 + ATR * 倍數
    """
    sl_distance = atr_value * atr_mult
    if direction == "buy":
        return round(entry_price - sl_distance, 6)
    else:
        return round(entry_price + sl_distance, 6)


def calc_take_profits(direction: str, entry: float, stop_loss: float) -> Dict:
    """
    計算兩個止盈目標
    TP1 = 風報比 1:1.3（先獲利一半）
    TP2 = 風報比 1:2.5（讓利潤跑）
    """
    risk = abs(entry - stop_loss)
    if direction == "buy":
        tp1 = round(entry + risk * 1.3, 6)
        tp2 = round(entry + risk * 2.5, 6)
    else:
        tp1 = round(entry - risk * 1.3, 6)
        tp2 = round(entry - risk * 2.5, 6)

    rr1 = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0
    rr2 = round(abs(tp2 - entry) / risk, 2) if risk > 0 else 0

    return {
        "tp1": tp1,
        "tp2": tp2,
        "rr1": rr1,
        "rr2": rr2,
        "risk_amount": round(risk, 6),
    }


def calc_lot_size(entry: float, stop_loss: float,
                  symbol: str, account_usd: float = None) -> Dict:
    """
    根據帳戶餘額和風險比例計算建議手數
    確保每筆交易風險不超過帳戶的 MAX_RISK_PER_TRADE
    """
    if account_usd is None:
        account_usd = ACCOUNT_BALANCE_USD

    sym_info = SYMBOLS.get(symbol, {})
    min_lot  = sym_info.get("min_lot", 0.01)

    risk_usd     = account_usd * MAX_RISK_PER_TRADE
    sl_distance  = abs(entry - stop_loss)

    if sl_distance <= 0:
        return {"lot": min_lot, "risk_usd": 0, "risk_pct": 0}

    # 根據品種類別計算每手每點盈虧
    category = sym_info.get("category", "外匯")

    if category == "外匯":
        # 外匯：1手 = 100,000 基礎貨幣
        # 簡化計算：每點約 $10（以主要貨幣對估算）
        pip_value_per_lot = 10.0
        pips = sl_distance / sym_info.get("pip", 0.0001)
        risk_per_lot = pips * pip_value_per_lot / 10000 * 100
        # 更直接的方法
        risk_per_lot = sl_distance * 100000  # 1手的USD風險

    elif category == "商品" and "XAU" in symbol:
        # 黃金：1手 = 100盎司
        risk_per_lot = sl_distance * 100

    elif category == "商品":
        # 石油：1手 = 1000桶（Mitrade）
        risk_per_lot = sl_distance * 1000

    elif category == "加密":
        # 加密：依平台設定，這裡用近似值
        risk_per_lot = sl_distance * 1

    else:
        risk_per_lot = sl_distance * 100

    if risk_per_lot <= 0:
        return {"lot": min_lot, "risk_usd": 0, "risk_pct": 0}

    suggested_lot = risk_usd / risk_per_lot

    # 向下取整到最小手數的倍數
    steps = max(1, int(suggested_lot / min_lot))
    final_lot = round(steps * min_lot, 2)
    final_lot = max(min_lot, min(final_lot, 1.0))  # 限制最大 1 手

    actual_risk_usd = final_lot * risk_per_lot
    actual_risk_pct = round((actual_risk_usd / account_usd) * 100, 2)

    return {
        "lot":      final_lot,
        "risk_usd": round(actual_risk_usd, 2),
        "risk_pct": actual_risk_pct,
    }


def check_multi_timeframe(tf_data: Dict) -> Dict:
    """
    多時框共振分析
    日線定大方向 → 4H 定中方向 → 1H 找進場
    三個時框必須方向一致才考慮進場
    """
    trend_ind = calc_all_indicators(tf_data.get("trend", {}))
    mid_ind   = calc_all_indicators(tf_data.get("mid",   {}))
    entry_ind = calc_all_indicators(tf_data.get("entry", {}))

    if not all([trend_ind.get("valid"), mid_ind.get("valid"), entry_ind.get("valid")]):
        return {
            "valid": False,
            "reason": "指標計算失敗",
            "direction": "none",
        }

    # 各時框偏向
    trend_bias = trend_ind.get("overall_bias", "neutral")
    mid_bias   = mid_ind.get("overall_bias",   "neutral")
    entry_bias = entry_ind.get("overall_bias",  "neutral")

    # 偏向轉換為數字
    def bias_to_num(b):
        mapping = {
            "strong_bullish": 2,
            "bullish":        1,
            "neutral":        0,
            "bearish":       -1,
            "strong_bearish":-2,
        }
        return mapping.get(b, 0)

    trend_num = bias_to_num(trend_bias)
    mid_num   = bias_to_num(mid_bias)
    entry_num = bias_to_num(entry_bias)

    # 共振判斷
    # 全部偏多 → 做多訊號
    # 全部偏空 → 做空訊號
    # 混亂    → 等待

    if trend_num > 0 and mid_num > 0 and entry_num > 0:
        direction = "buy"
        resonance = True
        strength  = trend_num + mid_num + entry_num
    elif trend_num < 0 and mid_num < 0 and entry_num < 0:
        direction = "sell"
        resonance = True
        strength  = abs(trend_num + mid_num + entry_num)
    elif trend_num > 0 and mid_num > 0 and entry_num == 0:
        direction = "buy"
        resonance = False  # 部分共振，等待進場確認
        strength  = trend_num + mid_num
    elif trend_num < 0 and mid_num < 0 and entry_num == 0:
        direction = "sell"
        resonance = False
        strength  = abs(trend_num + mid_num)
    else:
        direction = "none"
        resonance = False
        strength  = 0

    # 計算綜合訊號分數（0-100）
    score = 0
    conditions_met = []
    conditions_fail = []

    # 條件 1：EMA 排列（日線）
    if trend_ind.get("ema", {}).get("valid"):
        ema_bias = trend_ind["ema"].get("bias", "neutral")
        if direction == "buy" and ema_bias in ["bullish", "strong_bullish"]:
            score += 20
            conditions_met.append("日線 EMA 多頭排列")
        elif direction == "sell" and ema_bias in ["bearish", "strong_bearish"]:
            score += 20
            conditions_met.append("日線 EMA 空頭排列")
        else:
            conditions_fail.append("日線 EMA 方向不符")

    # 條件 2：RSI 健康區（4H）
    if mid_ind.get("rsi", {}).get("valid"):
        rsi_val  = mid_ind["rsi"].get("value", 50)
        rsi_bias = mid_ind["rsi"].get("bias",  "neutral")
        if direction == "buy" and rsi_bias in ["bullish", "bullish_warning"] and rsi_val < 70:
            score += 15
            conditions_met.append(f"4H RSI {rsi_val:.1f} 多頭健康區")
        elif direction == "sell" and rsi_bias in ["bearish", "bearish_warning"] and rsi_val > 30:
            score += 15
            conditions_met.append(f"4H RSI {rsi_val:.1f} 空頭健康區")
        elif rsi_val > 70 or rsi_val < 30:
            conditions_fail.append(f"4H RSI {rsi_val:.1f} 極端值，不追")
            score -= 10
        else:
            conditions_fail.append(f"4H RSI {rsi_val:.1f} 中性")

    # 條件 3：MACD（4H）
    if mid_ind.get("macd", {}).get("valid"):
        macd_bias = mid_ind["macd"].get("bias", "neutral")
        if direction == "buy" and macd_bias in ["bullish", "bullish_weak"]:
            score += 15
            conditions_met.append("4H MACD 偏多")
        elif direction == "sell" and macd_bias in ["bearish", "bearish_weak"]:
            score += 15
            conditions_met.append("4H MACD 偏空")
        else:
            conditions_fail.append("4H MACD 方向不符")

    # 條件 4：布林帶（1H）
    if entry_ind.get("bb", {}).get("valid"):
        bb_pos   = entry_ind["bb"].get("position", "")
        bb_score = entry_ind["bb"].get("score", 0)
        if direction == "buy" and bb_score > 0:
            score += 15
            conditions_met.append(f"1H 布林帶 {bb_pos}")
        elif direction == "sell" and bb_score < 0:
            score += 15
            conditions_met.append(f"1H 布林帶 {bb_pos}")
        else:
            conditions_fail.append(f"1H 布林帶 {bb_pos}")

    # 條件 5：1H EMA 趨勢確認
    if entry_ind.get("ema", {}).get("valid"):
        entry_ema_bias = entry_ind["ema"].get("bias", "neutral")
        if direction == "buy" and entry_ema_bias in ["bullish", "strong_bullish"]:
            score += 15
            conditions_met.append("1H EMA 多頭")
        elif direction == "sell" and entry_ema_bias in ["bearish", "strong_bearish"]:
            score += 15
            conditions_met.append("1H EMA 空頭")
        else:
            conditions_fail.append("1H EMA 方向不符")

    # 條件 6：成交量確認
    if entry_ind.get("volume", {}).get("valid"):
        vol_bias = entry_ind["volume"].get("bias", "neutral")
        if vol_bias in ["confirm"]:
            score += 10
            conditions_met.append("量能確認")
        elif vol_bias in ["weak"]:
            conditions_fail.append("量能萎縮")
            score -= 5

    # 共振加分
    if resonance:
        score += 10

    score = max(0, min(100, score))  # 限制在 0-100

    return {
        "valid":           True,
        "direction":       direction,
        "resonance":       resonance,
        "strength":        strength,
        "score":           score,
        "conditions_met":  conditions_met,
        "conditions_fail": conditions_fail,
        "trend_bias":      trend_bias,
        "mid_bias":        mid_bias,
        "entry_bias":      entry_bias,
        "indicators": {
            "trend": trend_ind,
            "mid":   mid_ind,
            "entry": entry_ind,
        },
    }


def generate_signal(symbol: str, tf_data: Dict, macro_data: Dict) -> Optional[Dict]:
    """
    核心訊號產生函數
    整合技術分析 + 宏觀背景，產生完整訊號卡
    """
    if not tf_data or not tf_data.get("entry"):
        return None

    sym_info = SYMBOLS.get(symbol, {})
    if not sym_info:
        return None

    # 不發進場訊號的品種
    if sym_info.get("monitor_only"):
        return None

    # 多時框共振分析
    mtf = check_multi_timeframe(tf_data)
    if not mtf.get("valid"):
        return None

    direction = mtf.get("direction", "none")
    score     = mtf.get("score", 0)

    # 分數不足 → 不發訊號
    if score < THRESH["min_score"] or direction == "none":
        return None

    # 取得當前價格和 ATR
    entry_data = tf_data["entry"]
    current_price = entry_data.get("current_price", 0)
    if not current_price:
        return None

    atr_info = mtf["indicators"]["entry"].get("atr", {})
    if not atr_info.get("valid"):
        return None

    atr_value = atr_info["value"]
    atr_mult  = sym_info.get("atr_mult", 1.5)

    # 計算進場點（待定單：等回測）
    # 多單：等回調到 EMA21 附近
    # 空單：等反彈到 EMA21 附近
    entry_ind = mtf["indicators"]["entry"]
    ema21 = entry_ind.get("ema", {}).get("e21", current_price)

    if direction == "buy":
        # 等回調，進場點在 EMA21 和當前價之間
        if ema21 and ema21 < current_price:
            entry_price = round((current_price + ema21) / 2, 6)
        else:
            entry_price = current_price
    else:
        if ema21 and ema21 > current_price:
            entry_price = round((current_price + ema21) / 2, 6)
        else:
            entry_price = current_price

    # 計算止損
    stop_loss = calc_stop_loss(symbol, direction, entry_price, atr_value, atr_mult)

    # 驗證止損合理性（不能超過帳戶 5%）
    sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
    if sl_distance_pct > 5:
        logger.warning(f"{symbol} stop loss too wide: {sl_distance_pct:.1f}%")
        return None

    # 計算止盈
    tp_info  = calc_take_profits(direction, entry_price, stop_loss)

    # 風報比驗證
    if tp_info["rr1"] < THRESH["min_rr_ratio"]:
        logger.info(f"{symbol} RR ratio too low: {tp_info['rr1']}")
        return None

    # 計算建議手數
    lot_info = calc_lot_size(entry_price, stop_loss, symbol)

    # 判斷訊號狀態
    # 如果當前價格接近進場點（在 0.1 ATR 範圍內）→ 可以進場
    # 否則 → 等待進場點
    distance_to_entry = abs(current_price - entry_price)
    action_threshold  = atr_value * 0.1

    if distance_to_entry <= action_threshold:
        action = "立刻進場"
        action_color = "green"
    elif direction == "buy" and current_price > entry_price:
        action = "等待回調至進場點"
        action_color = "yellow"
    elif direction == "sell" and current_price < entry_price:
        action = "等待反彈至進場點"
        action_color = "yellow"
    else:
        action = "等待進場點確認"
        action_color = "yellow"

    # 高信心等級
    if score >= THRESH["high_confidence"]:
        confidence_label = "高信心"
    elif score >= THRESH["min_score"]:
        confidence_label = "中等信心"
    else:
        confidence_label = "低信心"

    # 宏觀背景簡評
    macro_notes = []
    vix_data = macro_data.get("vix") if macro_data else None
    if vix_data:
        vix_val = vix_data.get("price", 0)
        if vix_val > 25:
            macro_notes.append(f"⚠️ VIX {vix_val:.0f} 偏高，波動加大")
        else:
            macro_notes.append(f"VIX {vix_val:.0f} 正常")

    dxy_data = macro_data.get("dxy") if macro_data else None
    if dxy_data:
        dxy_chg = dxy_data.get("chg", 0)
        if direction == "buy" and sym_info["category"] == "外匯":
            if dxy_chg > 0.3:
                macro_notes.append(f"⚠️ 美元走強 ({dxy_chg:+.1f}%)，留意外匯多單")
            elif dxy_chg < -0.3:
                macro_notes.append(f"✅ 美元走弱 ({dxy_chg:+.1f}%)，有利外匯多單")

    if sym_info["category"] == "商品" and "XAU" in symbol:
        if dxy_data and dxy_data.get("chg", 0) < -0.2:
            macro_notes.append("✅ 美元走弱，有利黃金")
        elif dxy_data and dxy_data.get("chg", 0) > 0.3:
            macro_notes.append("⚠️ 美元走強，黃金承壓")

    # 組合訊號
    signal = {
        "id":               f"{symbol}_{direction}_{int(datetime.now(timezone.utc).timestamp())}",
        "symbol":           symbol,
        "name":             sym_info["name"],
        "category":         sym_info["category"],
        "emoji":            sym_info.get("emoji", "📊"),
        "direction":        direction,
        "direction_zh":     "做多 (Buy)" if direction == "buy" else "做空 (Sell)",
        "current_price":    current_price,
        "entry_price":      entry_price,
        "stop_loss":        stop_loss,
        "tp1":              tp_info["tp1"],
        "tp2":              tp_info["tp2"],
        "rr1":              tp_info["rr1"],
        "rr2":              tp_info["rr2"],
        "risk_amount":      tp_info["risk_amount"],
        "suggested_lot":    lot_info["lot"],
        "risk_usd":         lot_info["risk_usd"],
        "risk_pct":         lot_info["risk_pct"],
        "score":            score,
        "confidence_label": confidence_label,
        "action":           action,
        "action_color":     action_color,
        "conditions_met":   mtf["conditions_met"],
        "conditions_fail":  mtf["conditions_fail"],
        "timeframe":        "日線+4H+1H 共振",
        "macro_notes":      macro_notes,
        "atr":              atr_value,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "expires_at":       None,  # 由 risk_manager 設定
        "valid":            True,
    }

    return signal
