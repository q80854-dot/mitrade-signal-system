"""
backtester.py v5.2 — Walk-Forward 回測引擎
"""
import logging, time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from config import SYMBOLS, ACCOUNT_BALANCE_USD, CB, SIGNAL_THRESHOLDS as THRESH
from state_store import store

logger = logging.getLogger(__name__)

def backtest_symbol(symbol, initial_balance=None, min_score=65.0, use_slippage=True, verbose=False):
    from data_fetcher import fetch_ohlcv
    from indicators import calc_all_indicators
    from scoring_engine import calc_composite_score, apply_slippage, calc_performance_metrics

    balance = initial_balance or ACCOUNT_BALANCE_USD
    si = SYMBOLS.get(symbol,{})
    if not si: return {"error":f"品種 {symbol} 不在監控列表"}
    data = fetch_ohlcv(symbol,"trend")
    if not data or len(data.get("closes",[]))<50: return {"error":f"{symbol} 歷史數據不足"}

    closes=data["closes"]; highs=data["highs"]; lows=data["lows"]
    opens=data["opens"]; volumes=data["volumes"]; n=len(closes)
    logger.info(f"[BT] {symbol} 開始回測，共 {n} 根K線")

    trades=[]; equity=[balance]; open_trade=None; LOOKBACK=50

    for i in range(LOOKBACK, n):
        wd = {"closes":closes[:i],"highs":highs[:i],"lows":lows[:i],"opens":opens[:i],"volumes":volumes[:i]}
        ind = calc_all_indicators(wd)
        if not ind.get("valid"): continue
        price = closes[i]

        if open_trade:
            d=open_trade["direction"]; sl=open_trade["sl"]; tp1=open_trade["tp1"]
            hit_sl=(d=="buy" and lows[i]<=sl) or (d=="sell" and highs[i]>=sl)
            hit_tp=(d=="buy" and highs[i]>=tp1) or (d=="sell" and lows[i]<=tp1)
            if hit_sl or hit_tp:
                res="tp1" if hit_tp else "sl"; cp=tp1 if hit_tp else sl
                if use_slippage:
                    slip=apply_slippage(symbol,cp,"sell" if d=="buy" else "buy")
                    cp=slip["fill_price"]
                pnl=(cp-open_trade["fill_price"])*(1 if d=="buy" else -1)
                pnl_u=pnl*open_trade.get("lot",0.01)*10000
                balance+=pnl_u; equity.append(balance)
                trades.append({"symbol":symbol,"direction":d,"entry":open_trade["fill_price"],
                    "close":cp,"sl":sl,"tp1":tp1,"result":res,"pnl":round(pnl_u,2),
                    "pnl_pct":round(pnl/open_trade["fill_price"]*100,3),"score":open_trade.get("score",0),
                    "bar_in":open_trade["bar"],"bar_out":i,"hold_bars":i-open_trade["bar"]})
                if verbose: logger.info(f"  [{symbol}] {d} {res}: {pnl_u:+.2f}USD")
                open_trade=None; continue

        if open_trade: continue
        direction="buy" if ind.get("overall_bias","").startswith("bullish") else \
                  "sell" if ind.get("overall_bias","").startswith("bearish") else None
        if not direction: continue

        sc=calc_composite_score(ind,direction,
            bull_tfs=["entry"] if "bullish" in ind.get("overall_bias","") else [],
            bear_tfs=["entry"] if "bearish" in ind.get("overall_bias","") else [])
        score=sc["composite"]
        if score<min_score: continue
        adx=ind.get("adx_value",0)
        if adx<15: continue
        atr=ind.get("atr",{}).get("value",0)
        if not atr: continue
        sl=price-atr*1.3 if direction=="buy" else price+atr*1.3
        tp1=price+atr*1.8 if direction=="buy" else price-atr*1.8
        if (tp1-price if direction=="buy" else price-tp1)/(price-sl if direction=="buy" else sl-price)<1.3: continue
        fill=price
        if use_slippage: fill=apply_slippage(symbol,price,direction)["fill_price"]
        risk_u=balance*0.02; sl_dist=abs(fill-sl); pip=si.get("pip",0.0001)
        lot=max(0.01,min(2.0,risk_u/(sl_dist/pip*10))) if sl_dist>0 else 0.01
        open_trade={"direction":direction,"fill_price":fill,"sl":sl,"tp1":tp1,"score":score,"lot":lot,"bar":i}

    if open_trade:
        d=open_trade["direction"]; cp=closes[-1]
        pnl=(cp-open_trade["fill_price"])*(1 if d=="buy" else -1)
        pnl_u=pnl*open_trade["lot"]*10000; balance+=pnl_u; equity.append(balance)
        trades.append({"symbol":symbol,"direction":d,"entry":open_trade["fill_price"],"close":cp,
            "result":"forced_close","pnl":round(pnl_u,2),"pnl_pct":round(pnl/open_trade["fill_price"]*100,3),
            "score":open_trade["score"],"bar_in":open_trade["bar"],"bar_out":n-1,"hold_bars":n-1-open_trade["bar"]})

    metrics=calc_performance_metrics(equity,trades)
    wins=[t for t in trades if t["result"]=="tp1"]
    losses=[t for t in trades if t["result"]=="sl"]
    init_bal=initial_balance or ACCOUNT_BALANCE_USD
    result={
        "symbol":symbol,"n_bars":n,"n_trades":len(trades),"n_wins":len(wins),"n_losses":len(losses),
        "win_rate":round(len(wins)/max(len(wins)+len(losses),1)*100,1),
        "total_pnl":round(sum(t["pnl"] for t in trades),2),
        "initial_balance":init_bal,"final_balance":round(balance,2),
        "return_pct":round((balance-init_bal)/init_bal*100,2),
        "sharpe":metrics.get("sharpe",0),"max_drawdown":metrics.get("max_drawdown",0),
        "calmar":metrics.get("calmar",0),"annual_return":metrics.get("annual_return",0),
        "max_consec_loss":metrics.get("max_consec_loss",0),
        "equity_curve":equity,"trades":trades[-50:],
        "min_score_used":min_score,"slippage_used":use_slippage,
        "grade":metrics.get("sharpe_grade","—"),"dd_grade":metrics.get("dd_grade","—"),
        "completed_at":datetime.now(timezone.utc).isoformat(),
    }
    store.save_backtest(symbol,f"composite_score_{min_score}",result)
    logger.info(f"[BT] {symbol} 完成：勝率{result['win_rate']}% Sharpe={result['sharpe']} MaxDD={result['max_drawdown']}%")
    return result

def walk_forward_backtest(symbol, train_bars=120, test_bars=30, min_score=65.0):
    from data_fetcher import fetch_ohlcv
    from indicators import calc_all_indicators
    from scoring_engine import calc_composite_score, apply_slippage, calc_performance_metrics

    data=fetch_ohlcv(symbol,"trend")
    if not data or len(data.get("closes",[]))<train_bars+test_bars:
        return {"error":"數據不足以進行 Walk-Forward"}

    closes=data["closes"]; highs=data["highs"]; lows=data["lows"]
    opens=data["opens"]; volumes=data["volumes"]; n=len(closes)
    si=SYMBOLS.get(symbol,{})

    windows=[]; start=train_bars
    while start+test_bars<=n:
        windows.append((start-train_bars,start,start+test_bars)); start+=test_bars

    logger.info(f"[WF] {symbol} Walk-Forward：{len(windows)} 個窗口")
    all_test_trades=[]; window_results=[]

    for win_idx,(tr_start,tr_end,te_end) in enumerate(windows):
        test_trades=[]; open_trade=None; balance=ACCOUNT_BALANCE_USD

        for i in range(tr_end,te_end):
            wd={"closes":closes[tr_start:i],"highs":highs[tr_start:i],"lows":lows[tr_start:i],
                "opens":opens[tr_start:i],"volumes":volumes[tr_start:i]}
            if len(wd["closes"])<30: continue
            ind=calc_all_indicators(wd)
            if not ind.get("valid"): continue
            price=closes[i]

            if open_trade:
                sl=open_trade["sl"]; tp1=open_trade["tp1"]; d=open_trade["direction"]
                hit_sl=(d=="buy" and lows[i]<=sl) or (d=="sell" and highs[i]>=sl)
                hit_tp=(d=="buy" and highs[i]>=tp1) or (d=="sell" and lows[i]<=tp1)
                if hit_sl or hit_tp:
                    res="tp1" if hit_tp else "sl"; cp=tp1 if hit_tp else sl
                    pnl=(cp-open_trade["fill_price"])*(1 if d=="buy" else -1)
                    pnl_u=pnl*open_trade.get("lot",0.01)*10000; balance+=pnl_u
                    t={"result":res,"pnl":round(pnl_u,2),"score":open_trade["score"]}
                    test_trades.append(t); all_test_trades.append(t); open_trade=None; continue

            if open_trade: continue
            direction="buy" if ind.get("overall_bias","").startswith("bullish") else \
                      "sell" if ind.get("overall_bias","").startswith("bearish") else None
            if not direction: continue
            sc=calc_composite_score(ind,direction,
                bull_tfs=["entry"] if "bullish" in ind.get("overall_bias","") else [],
                bear_tfs=["entry"] if "bearish" in ind.get("overall_bias","") else [])
            if sc["composite"]<min_score: continue
            atr=ind.get("atr",{}).get("value",0)
            if not atr: continue
            sl=price-atr*1.3 if direction=="buy" else price+atr*1.3
            tp1=price+atr*1.8 if direction=="buy" else price-atr*1.8
            slip=apply_slippage(symbol,price,direction)
            open_trade={"direction":direction,"fill_price":slip["fill_price"],
                        "sl":sl,"tp1":tp1,"score":sc["composite"],"lot":0.01,"bar":i}

        wins_w=len([t for t in test_trades if t["result"]=="tp1"])
        window_results.append({"window":win_idx+1,"train_bars":tr_end-tr_start,"test_bars":te_end-tr_end,
            "n_trades":len(test_trades),"win_rate":round(wins_w/max(len(test_trades),1)*100,1),
            "total_pnl":round(sum(t["pnl"] for t in test_trades),2)})

    all_wins=len([t for t in all_test_trades if t["result"]=="tp1"])
    result={
        "symbol":symbol,"method":"walk_forward","n_windows":len(windows),
        "train_bars":train_bars,"test_bars":test_bars,"total_trades":len(all_test_trades),
        "overall_win_rate":round(all_wins/max(len(all_test_trades),1)*100,1),
        "total_pnl":round(sum(t["pnl"] for t in all_test_trades),2),
        "window_results":window_results,
        "stability":round(sum(1 for w in window_results if w["win_rate"]>=50)/max(len(window_results),1)*100,1),
        "completed_at":datetime.now(timezone.utc).isoformat(),
    }
    store.save_backtest(symbol,"walk_forward",result)
    logger.info(f"[WF] {symbol} 完成：整體勝率{result['overall_win_rate']}% 穩定性{result['stability']}%")
    return result

def run_full_backtest(symbols=None, min_score=65.0):
    from config import SYMBOLS as ALL_SYMS
    tier_d={"TSLA","WTI","HK50","AUDUSD","USDCAD"}
    targets=[s for s in (symbols or ALL_SYMS) if not ALL_SYMS.get(s,{}).get("monitor_only") and s not in tier_d]
    results=[]; logger.info(f"[BT] 開始批量回測 {len(targets)} 個品種")
    for sym in targets:
        try:
            r=backtest_symbol(sym,min_score=min_score)
            if "error" not in r: results.append(r)
            time.sleep(0.5)
        except Exception as e: logger.error(f"[BT] {sym} 失敗: {e}")
    results.sort(key=lambda x:x.get("sharpe",0),reverse=True)
    return {
        "total":len(results),"completed_at":datetime.now(timezone.utc).isoformat(),
        "leaderboard":[{"symbol":r["symbol"],"win_rate":r["win_rate"],"sharpe":r["sharpe"],
            "max_drawdown":r["max_drawdown"],"total_pnl":r["total_pnl"],"calmar":r["calmar"],
            "grade":r["grade"],"n_trades":r["n_trades"]} for r in results],
        "details":results,
    }
