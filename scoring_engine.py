"""
scoring_engine.py v5.2
機構級評分系統：連續評分矩陣 + Kelly + Sharpe/Drawdown + 滑點 + Regime
"""
import math, logging
from typing import List, Dict, Optional
from config import SYMBOLS, ACCOUNT_BALANCE_USD, CB

logger = logging.getLogger(__name__)

SCORE_WEIGHTS = {
    "ema_alignment":  {"weight":20,"desc":"EMA排列"},
    "rsi_zone":       {"weight":12,"desc":"RSI區間"},
    "macd_momentum":  {"weight":12,"desc":"MACD動能"},
    "adx_strength":   {"weight":15,"desc":"ADX趨勢強度"},
    "bb_position":    {"weight":8, "desc":"布林帶位置"},
    "volume_confirm": {"weight":8, "desc":"成交量確認"},
    "mtf_resonance":  {"weight":15,"desc":"多時框共振"},
    "candlestick":    {"weight":5, "desc":"K線形態"},
    "sr_proximity":   {"weight":5, "desc":"支撐壓力位"},
}

def score_ema(indicators, direction):
    ema = indicators.get("ema",{})
    if not ema.get("valid"): return 0.3
    bias = ema.get("bias","neutral")
    base = {"bullish":{"buy":0.8,"sell":0.2},"bearish":{"buy":0.2,"sell":0.8},"neutral":{"buy":0.4,"sell":0.4}}.get(bias,{"buy":0.4,"sell":0.4}).get(direction,0.4)
    if "完美" in ema.get("alignment",""): base = min(1.0, base+0.15)
    if direction=="buy"  and "金叉" in str(ema.get("cross","")): base = min(1.0, base+0.1)
    if direction=="sell" and "死叉" in str(ema.get("cross","")): base = min(1.0, base+0.1)
    return base

def score_rsi(indicators, direction):
    rsi = indicators.get("rsi",{})
    if not rsi.get("valid"): return 0.3
    val = rsi.get("value",50)
    if direction=="buy":
        if 45<=val<=65: return 0.9
        elif 35<=val<45: return 0.7
        elif 65<val<=70: return 0.6
        elif val>70: return 0.2
        else: return 0.5
    else:
        if 35<=val<=55: return 0.9
        elif 55<val<=65: return 0.7
        elif 30<=val<35: return 0.6
        elif val<30: return 0.2
        else: return 0.5

def score_macd(indicators, direction):
    macd = indicators.get("macd",{})
    if not macd.get("valid"): return 0.3
    bias = macd.get("bias","neutral"); growing = macd.get("hist_growing",False); cross = macd.get("cross","")
    base = 0.3
    if direction=="buy":
        if "bullish" in bias: base=0.75
        if growing and "bullish" in bias: base=0.85
        if cross=="MACD金叉": base=min(1.0,base+0.1)
    else:
        if "bearish" in bias: base=0.75
        if not growing and "bearish" in bias: base=0.85
        if cross=="MACD死叉": base=min(1.0,base+0.1)
    return base

def score_adx(indicators):
    v = indicators.get("adx_value",0)
    if v==0: return 0.1
    if v>=40: return 1.0
    elif v>=30: return 0.85
    elif v>=25: return 0.70
    elif v>=20: return 0.50
    elif v>=15: return 0.35
    else: return 0.15

def score_bb(indicators, direction):
    bb = indicators.get("bb",{})
    if not bb.get("valid"): return 0.4
    pos = bb.get("position","")
    if direction=="buy":
        if pos=="中軌上方": return 0.8
        elif pos=="突破上軌": return 0.6
        elif pos=="中軌下方": return 0.3
        else: return 0.2
    else:
        if pos=="中軌下方": return 0.8
        elif pos=="突破下軌": return 0.6
        elif pos=="中軌上方": return 0.3
        else: return 0.2

def score_volume(indicators):
    vol = indicators.get("volume",{})
    if not vol.get("valid"): return 0.4
    r = vol.get("ratio",1.0)
    if r>=2.0: return 1.0
    elif r>=1.5: return 0.85
    elif r>=1.0: return 0.60
    elif r>=0.7: return 0.40
    else: return 0.20

def score_mtf(bull_tfs, bear_tfs, direction):
    n = len(bull_tfs) if direction=="buy" else len(bear_tfs)
    if n>=3: return 1.0
    elif n==2: return 0.7
    elif n==1: return 0.35
    else: return 0.0

def score_candlestick(indicators, direction):
    cp = indicators.get("candlestick",{})
    if not cp.get("valid"): return 0.5
    strongest = cp.get("strongest")
    if not strongest: return 0.5
    strength = strongest.get("strength",1); t = strongest.get("type","neutral")
    if (direction=="buy" and t=="bullish") or (direction=="sell" and t=="bearish"):
        return min(1.0, 0.5+strength*0.15)
    elif t=="neutral": return 0.5
    else: return 0.3

def score_sr(indicators, direction):
    sr = indicators.get("support_resistance",{})
    if not sr.get("valid"): return 0.5
    if direction=="buy":
        dist = sr.get("distance_to_sup")
        if dist is None: return 0.5
        if dist<=0.3: return 0.95
        elif dist<=0.8: return 0.80
        elif dist<=1.5: return 0.60
        else: return 0.40
    else:
        dist = sr.get("distance_to_res")
        if dist is None: return 0.5
        if dist<=0.3: return 0.95
        elif dist<=0.8: return 0.80
        elif dist<=1.5: return 0.60
        else: return 0.40

def calc_composite_score(indicators, direction, bull_tfs, bear_tfs, auto_result=None):
    scores = {
        "ema_alignment":  score_ema(indicators, direction),
        "rsi_zone":       score_rsi(indicators, direction),
        "macd_momentum":  score_macd(indicators, direction),
        "adx_strength":   score_adx(indicators),
        "bb_position":    score_bb(indicators, direction),
        "volume_confirm": score_volume(indicators),
        "mtf_resonance":  score_mtf(bull_tfs, bear_tfs, direction),
        "candlestick":    score_candlestick(indicators, direction),
        "sr_proximity":   score_sr(indicators, direction),
    }
    total_w   = sum(v["weight"] for v in SCORE_WEIGHTS.values())
    weighted  = sum(scores[k]*SCORE_WEIGHTS[k]["weight"] for k in scores)
    composite = round(weighted/total_w*100, 1)
    breakdown = {k:{"score":round(scores[k]*100,1),"weight":SCORE_WEIGHTS[k]["weight"],
                    "desc":SCORE_WEIGHTS[k]["desc"],
                    "contribution":round(scores[k]*SCORE_WEIGHTS[k]["weight"]/total_w*100,1)} for k in scores}
    return {
        "composite": composite,
        "breakdown": breakdown,
        "raw_scores":scores,
        "grade":"A" if composite>=80 else "B" if composite>=65 else "C" if composite>=50 else "D",
    }

def kelly_position_size(win_rate, avg_rr, balance, half_kelly=True):
    if win_rate<=0 or avg_rr<=0:
        return {"kelly_pct":2.0,"risk_usd":balance*0.02,"method":"default"}
    loss_rate = 1-win_rate
    kelly_f   = (win_rate*avg_rr-loss_rate)/avg_rr
    if kelly_f<=0:
        return {"kelly_pct":0.0,"risk_usd":0,"method":"kelly_negative","warning":"策略期望值為負，建議暫停"}
    if half_kelly: kelly_f*=0.5
    kelly_f = min(kelly_f,0.05); kelly_f = max(kelly_f,0.005)
    return {
        "kelly_pct":   round(kelly_f*100,2),
        "risk_usd":    round(balance*kelly_f,2),
        "method":      "half_kelly" if half_kelly else "full_kelly",
        "kelly_raw":   round((win_rate*avg_rr-loss_rate)/avg_rr*100,2),
        "edge":        round(win_rate*avg_rr-loss_rate,4),
    }

def calc_performance_metrics(equity_curve, trades=None):
    if not equity_curve or len(equity_curve)<2: return {"valid":False}
    returns = [(equity_curve[i]-equity_curve[i-1])/equity_curve[i-1] for i in range(1,len(equity_curve))]
    peak=equity_curve[0]; max_dd=0.0
    for v in equity_curve:
        if v>peak: peak=v
        dd=(peak-v)/peak
        if dd>max_dd: max_dd=dd
    if len(returns)>1:
        rf=0.05/252; excess=[r-rf for r in returns]
        mean_e=sum(excess)/len(excess)
        std_e=math.sqrt(sum((r-mean_e)**2 for r in excess)/max(len(excess)-1,1))
        sharpe=round(mean_e/std_e*math.sqrt(252),3) if std_e>0 else 0
    else: sharpe=0
    total_ret=(equity_curve[-1]-equity_curve[0])/equity_curve[0]
    n_days=max(len(equity_curve),1)
    annual_ret=round((1+total_ret)**(252/n_days)-1,4) if n_days>0 else 0
    calmar=round(annual_ret/max_dd,3) if max_dd>0 else 0
    max_consec=0; cur=0
    if trades:
        for t in trades:
            if t.get("pnl",0)<0: cur+=1; max_consec=max(max_consec,cur)
            else: cur=0
    wins=[t for t in (trades or []) if t.get("pnl",0)>0]
    losses=[t for t in (trades or []) if t.get("pnl",0)<0]
    avg_win=sum(t["pnl"] for t in wins)/max(len(wins),1)
    avg_loss=sum(abs(t["pnl"]) for t in losses)/max(len(losses),1)
    wr=len(wins)/max(len(wins)+len(losses),1)
    return {
        "valid":True,"sharpe":sharpe,
        "sharpe_grade":"優" if sharpe>=2 else "良" if sharpe>=1.5 else "普" if sharpe>=1 else "差",
        "max_drawdown":round(max_dd*100,2),
        "dd_grade":"優" if max_dd<0.05 else "良" if max_dd<0.10 else "普" if max_dd<0.20 else "危險",
        "calmar":calmar,"annual_return":round(annual_ret*100,2),
        "total_return":round(total_ret*100,2),"win_rate":round(wr*100,1),
        "actual_rr":round(avg_win/avg_loss,2) if avg_loss>0 else 0,
        "max_consec_loss":max_consec,"n_trades":len(trades or []),
        "equity_start":equity_curve[0],"equity_end":equity_curve[-1],
    }

SLIPPAGE_MODEL = {
    "EURUSD":0.0001,"GBPUSD":0.00015,"USDJPY":0.01,"AUDUSD":0.00015,"USDCAD":0.00015,
    "XAUUSD":0.15,"WTI":0.03,"BTCUSD":25.0,"ETHUSD":3.0,
    "US500":0.3,"NAS100":0.8,"US30":3.0,"HK50":3.0,"GER40":0.8,
    "AAPL":0.03,"NVDA":0.08,"TSLA":0.12,"MSFT":0.03,"AMZN":0.05,"GOOGL":0.05,
}

def apply_slippage(symbol, price, direction, volatility_mult=1.0):
    slip = SLIPPAGE_MODEL.get(symbol,0.001)*volatility_mult
    fill = price+slip if direction=="buy" else price-slip
    return {
        "quote_price":round(price,6),"fill_price":round(fill,6),
        "slippage":round(slip,6),"slippage_pct":round(slip/price*100,4),
        "slippage_usd":round(slip*10000,2),
    }

def detect_regime(macro_data, indicators=None):
    vix    = float(macro_data.get("vix",{}).get("price",20) or 20)
    fg     = float(macro_data.get("fear_greed",{}).get("score",50) or 50)
    sp500_c= float(macro_data.get("sp500",{}).get("chg",0) or 0)
    adx    = float((indicators or {}).get("adx_value",0) or 0)
    if vix>=40:
        return {"regime":"crisis","regime_zh":"極端危機","action":"stop_all","confidence":0.95,
                "strategy":"停止所有交易","allowed_directions":[]}
    if vix>=30:
        return {"regime":"high_vol","regime_zh":"高波動","action":"reduce_size","confidence":0.80,
                "strategy":"縮倉50%，只交易高確信度訊號","allowed_directions":["buy","sell"],"size_multiplier":0.5}
    if adx>=25 and fg>=60 and sp500_c>0:
        return {"regime":"trending_bull","regime_zh":"強多趨勢","action":"trend_follow","confidence":0.75,
                "strategy":"順勢做多，避免逆勢空單","allowed_directions":["buy"],"size_multiplier":1.0}
    if adx>=25 and fg<=40 and sp500_c<0:
        return {"regime":"trending_bear","regime_zh":"強空趨勢","action":"trend_follow","confidence":0.75,
                "strategy":"順勢做空，避免逆勢多單","allowed_directions":["sell"],"size_multiplier":0.8}
    if adx<20 and 40<=fg<=60:
        return {"regime":"ranging","regime_zh":"震盪整理","action":"mean_revert","confidence":0.65,
                "strategy":"縮小止損，快進快出","allowed_directions":["buy","sell"],"size_multiplier":0.7}
    return {"regime":"normal","regime_zh":"正常市場","action":"normal","confidence":0.60,
            "strategy":"正常策略，遵循訊號門檻","allowed_directions":["buy","sell"],"size_multiplier":1.0}

def calc_rolling_correlation(a, b, window=20):
    if len(a)<window or len(b)<window: return 0.0
    a=a[-window:]; b=b[-window:]
    ra=[(a[i]-a[i-1])/a[i-1] for i in range(1,len(a))]
    rb=[(b[i]-b[i-1])/b[i-1] for i in range(1,len(b))]
    n=len(ra); ma=sum(ra)/n; mb=sum(rb)/n
    cov=sum((ra[i]-ma)*(rb[i]-mb) for i in range(n))
    va=sum((r-ma)**2 for r in ra); vb=sum((r-mb)**2 for r in rb)
    denom=math.sqrt(va*vb)
    return round(cov/denom,3) if denom>0 else 0.0

def check_portfolio_correlation(new_symbol, new_direction, active_signals, price_data=None, threshold=0.7):
    if not active_signals: return {"correlated":False,"max_corr":0,"details":[]}
    details=[]; max_corr=0
    for sig in active_signals:
        sym_b=sig.get("symbol","")
        if sym_b==new_symbol: continue
        if price_data and new_symbol in price_data and sym_b in price_data:
            corr=calc_rolling_correlation(price_data[new_symbol],price_data[sym_b])
        else:
            from config import CORRELATION_GROUPS
            corr=0.0
            for group in CORRELATION_GROUPS:
                if new_symbol in group and sym_b in group: corr=0.75; break
        if abs(corr)>=threshold and sig.get("direction","")==new_direction:
            details.append({"symbol":sym_b,"correlation":corr,"direction":sig.get("direction",""),
                            "penalty":-15 if abs(corr)>=0.85 else -8})
            max_corr=max(max_corr,abs(corr))
    total_penalty=sum(d["penalty"] for d in details)
    return {
        "correlated":len(details)>0,"max_corr":max_corr,"details":details,
        "score_penalty":total_penalty,
        "message":f"與 {', '.join(d['symbol'] for d in details)} 高度相關（{max_corr:.2f}），降分 {total_penalty}" if details else ""
    }
