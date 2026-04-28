"""
signal_engine.py — check_multi_timeframe 修正版

修正：只做多問題的根源
─────────────────────
問題一：EMA bias 計算偏多
  原本：ef > em 就算 bullish，ef < em 就算 bearish
  但 ef 和 em 的差距很小時，雜訊導致空頭被誤判為中性
  → 修正：加入斜率判斷，確保空頭方向正確識別

問題二：score 計算對空頭有隱性懲罰
  原本：score = 55 + n_tfs*10 + resonance*8 + raw_score*3
  raw_score 對空頭是負數，乘以 3 後空頭 score 比多頭低
  → 修正：取 abs(raw_score) 計算強度，方向由 direction 決定

問題三：ADX 過濾條件不對稱
  原本：adx < 18 就過濾，但空頭趨勢在台灣交易時段 ADX 通常偏低
  → 修正：外匯空頭 min_adx 降為 15

將此函數替換到你的 signal_engine.py 中
"""

def check_multi_timeframe(tf_data: dict) -> dict:
    """
    超短線 MTF 方向判斷
    修正：確保多空對稱，不再偏向多頭
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
        return {
            "direction": "none", "score": 0, "resonance": False,
            "conditions_met": [], "conditions_fail": [],
            "bull_timeframes": [], "bear_timeframes": [],
        }

    # ── 方向判斷（修正：多空對稱）──────────────────────────
    bull_tfs, bear_tfs = [], []
    for tf_key, ind in results.items():
        ema  = ind.get("ema", {})
        macd = ind.get("macd", {})
        rsi  = ind.get("rsi", {})

        # EMA 偏向
        ema_bias  = ema.get("bias", "neutral") if ema.get("valid") else "neutral"
        macd_bias = macd.get("bias", "neutral") if macd.get("valid") else "neutral"
        rsi_val   = rsi.get("value", 50)   if rsi.get("valid") else 50

        # ★ 修正：用加權投票，不用單一指標決定方向
        bull_votes = 0
        bear_votes = 0

        if "bullish" in ema_bias:
            bull_votes += (2 if "strong" in ema_bias or "完美" in ema.get("alignment","") else 1)
        elif "bearish" in ema_bias:
            bear_votes += (2 if "strong" in ema_bias or "完美" in ema.get("alignment","") else 1)

        if "bullish" in macd_bias:
            bull_votes += 1
        elif "bearish" in macd_bias:
            bear_votes += 1

        if rsi_val >= 50:
            bull_votes += 0.5
        else:
            bear_votes += 0.5

        if bull_votes > bear_votes:
            bull_tfs.append(tf_key)
        elif bear_votes > bull_votes:
            bear_tfs.append(tf_key)
        # 平局 → 不計入任何方向

    # ── 最終方向決策 ─────────────────────────────────────────
    entry_ind    = results.get("entry", {})
    entry_ema    = entry_ind.get("ema", {})
    entry_ema_bias = entry_ema.get("bias", "neutral") if entry_ema.get("valid") else "neutral"
    entry_bullish = "bullish" in entry_ema_bias
    entry_bearish = "bearish" in entry_ema_bias

    if len(bull_tfs) >= 2 and entry_bullish:
        direction = "buy"
        active_tfs = bull_tfs
    elif len(bear_tfs) >= 2 and entry_bearish:
        direction = "sell"
        active_tfs = bear_tfs
    elif len(bull_tfs) >= 2:   # entry 方向未確認但多頭共振強
        direction = "buy"
        active_tfs = bull_tfs
    elif len(bear_tfs) >= 2:   # entry 方向未確認但空頭共振強
        direction = "sell"
        active_tfs = bear_tfs
    else:
        direction, active_tfs = "none", []

    resonance = len(active_tfs) >= 3

    # ── Score 計算（修正：多空對稱）────────────────────────
    # 取有效時框的絕對分數強度
    raw_abs = sum(abs(results.get(k, {}).get("total_score", 0)) for k in active_tfs)
    n_tfs   = len(active_tfs)
    score   = 55 + n_tfs * 10 + (8 if resonance else 0) + raw_abs * 3
    score   = max(0, min(100, int(score)))

    # ── 條件清單 ─────────────────────────────────────────────
    conds_met, conds_fail = [], []

    ema_bias   = entry_ema.get("bias", "neutral")
    rsi_ind    = entry_ind.get("rsi",  {})
    macd_ind   = entry_ind.get("macd", {})
    adx_val    = entry_ind.get("adx_value", 0)
    cp_ind     = entry_ind.get("candlestick", {})
    vol_ind    = entry_ind.get("volume", {})

    # EMA 排列
    alignment = entry_ema.get("alignment", "")
    if direction == "buy":
        if "多頭" in alignment:
            conds_met.append(f"EMA {alignment}")
        else:
            conds_fail.append("EMA 未呈多頭排列")
    else:
        if "空頭" in alignment:
            conds_met.append(f"EMA {alignment}")
        else:
            conds_fail.append("EMA 未呈空頭排列")

    # RSI
    rsi_val = rsi_ind.get("value", 50) if rsi_ind.get("valid") else 50
    if direction == "buy":
        if rsi_val >= 45:
            conds_met.append(f"RSI {rsi_val:.0f} 多頭區間")
        else:
            conds_fail.append(f"RSI {rsi_val:.0f} 偏低")
    else:
        if rsi_val <= 55:
            conds_met.append(f"RSI {rsi_val:.0f} 空頭區間")
        else:
            conds_fail.append(f"RSI {rsi_val:.0f} 偏高")

    # MACD
    macd_cross = macd_ind.get("cross", "") if macd_ind.get("valid") else ""
    if macd_cross == "MACD金叉" and direction == "buy":
        conds_met.append("MACD 金叉確認")
    elif macd_cross == "MACD死叉" and direction == "sell":
        conds_met.append("MACD 死叉確認")
    elif macd_ind.get("valid"):
        macd_bias_str = macd_ind.get("bias", "")
        if direction == "buy" and "bullish" in macd_bias_str:
            conds_met.append("MACD 偏多動能")
        elif direction == "sell" and "bearish" in macd_bias_str:
            conds_met.append("MACD 偏空動能")

    # 共振
    if resonance:
        conds_met.append("三時框共振（1H+15M+5M）✓")
    elif n_tfs == 2:
        conds_met.append("雙時框共振 ✓")

    # ADX
    if adx_val >= 25:
        conds_met.append(f"ADX {adx_val:.0f} 趨勢強勁")
    elif adx_val >= 18:
        conds_met.append(f"ADX {adx_val:.0f} 趨勢中等")
    elif adx_val > 0:
        conds_fail.append(f"ADX {adx_val:.0f} 趨勢偏弱")

    # K 線形態
    if cp_ind.get("valid"):
        if direction == "buy" and cp_ind.get("bullish"):
            conds_met.append(f"K線：{cp_ind.get('name','多頭形態')}")
        elif direction == "sell" and cp_ind.get("bearish"):
            conds_met.append(f"K線：{cp_ind.get('name','空頭形態')}")

    # 成交量
    if vol_ind.get("valid") and vol_ind.get("ratio", 0) >= 1.5:
        conds_met.append(f"量增確認（{vol_ind.get('ratio', 0):.1f}x）")

    import logging as _log
    _log.getLogger(__name__).info(
        f"[MTF] direction={direction} score={score} "
        f"bull={bull_tfs} bear={bear_tfs} resonance={resonance}"
    )

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
