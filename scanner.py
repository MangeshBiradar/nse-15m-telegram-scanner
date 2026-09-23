
import json, os, time
from pathlib import Path
import pandas as pd
import requests, yfinance as yf
from dotenv import load_dotenv
from indicators import wilder_rma, supertrend, rsi_wilder, bollinger

load_dotenv()
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","").strip()
STATE=Path("state.json")
ATR_PERIOD=int(os.getenv("ATR_PERIOD","10"))
ATR_MULT=float(os.getenv("ATR_MULTIPLIER","3"))
RSI_PERIOD=int(os.getenv("RSI_PERIOD","14"))
BB_PERIOD=int(os.getenv("BB_PERIOD","20"))
BB_STD=float(os.getenv("BB_STD","2"))

def telegram(msg):
    if not TOKEN or not CHAT_ID: raise RuntimeError("Telegram secrets missing")
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    data={"chat_id":CHAT_ID,"text":msg},timeout=20)
    r.raise_for_status()

def symbols():
    d=pd.read_csv("symbols.csv")
    return sorted(set(d.symbol.dropna().astype(str).str.strip().str.upper()))

def load_state():
    try: return json.loads(STATE.read_text())
    except: return {}

def save_state(s): STATE.write_text(json.dumps(s,indent=2))

def frame(symbol):
    d=yf.download(symbol+".NS", period="5y", interval="1d",
                  auto_adjust=False, progress=False, threads=False)
    if d is None or d.empty: return None
    if isinstance(d.columns,pd.MultiIndex): d=d.xs(symbol+".NS",axis=1,level=-1)
    return d[["Open","High","Low","Close","Volume"]].dropna()

def evaluate(symbol,d):
    if d is None or len(d)<100: return None
    # Daily indicators are calculated first. Weekly values are formed from
    # completed daily OHLC bars, then resampled to Friday-ending weekly bars.
    w=d.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    if len(w)<60: return None
    st=supertrend(w,ATR_PERIOD,ATR_MULT)
    rsi=rsi_wilder(w.Close,RSI_PERIOD)
    _,bb_up,_=bollinger(w.Close,BB_PERIOD,BB_STD)
    # User's "1-day-ago Close < 20 SMA" is a DAILY condition.
    daily_sma20=d.Close.rolling(20).mean()
    last_day=d.iloc[-1]
    prev_day=d.iloc[-2]
    daily_condition=prev_day.Close < daily_sma20.iloc[-2]

    cur=w.iloc[-1]
    # Avoid using a partially formed weekly candle during the trading week.
    # For a true completed-week scan, use the previous weekly bar.
    # We therefore evaluate the latest COMPLETED weekly bar.
    idx=len(w)-2
    if idx<1: return None
    wc=w.iloc[idx]
    wst=float(st.supertrend.iloc[idx])
    wrsi=float(rsi.iloc[idx])
    wbb=float(bb_up.iloc[idx])
    conditions={
      "weekly_close_ge_supertrend": float(wc.Close)>=wst,
      "weekly_close_ge_bb": float(wc.Close)>=wbb,
      "weekly_rsi_ge_60": wrsi>=60,
      "1d_ago_close_lt_daily_sma20": daily_condition
    }
    all_now=all(conditions.values())
    # Previous completed week, with the same daily 1-day-ago test relative
    # to that week ending date, to identify a fresh weekly signal.
    prev_idx=idx-1
    prev_date=w.index[prev_idx]
    prev_days=d.loc[d.index<=prev_date]
    if len(prev_days)>=2:
        pd1=prev_days.iloc[-2]
        psma=pd1.Close >= prev_days.Close.rolling(20).mean().iloc[-2] if len(prev_days)>=20 else True
        prev_daily=(pd1.Close < prev_days.Close.rolling(20).mean().iloc[-2]) if len(prev_days)>=20 else False
    else: prev_daily=False
    prev_cond=[
      float(w.iloc[prev_idx].Close)>=float(st.supertrend.iloc[prev_idx]),
      float(w.iloc[prev_idx].Close)>=float(bb_up.iloc[prev_idx]),
      float(rsi.iloc[prev_idx])>=60,
      prev_daily
    ]
    fresh=all_now and not all(prev_cond)
    return {"symbol":symbol,"week":str(w.index[idx].date()),
            "close":float(wc.Close),"supertrend":wst,"bb":wbb,"rsi":wrsi,
            "daily_prev_close":float(prev_day.Close),
            "daily_sma20":float(daily_sma20.iloc[-2]),
            "all":all_now,"fresh":fresh}

def main():
    st=load_state(); hits=[]
    for i,sym in enumerate(symbols(),1):
        try:
            r=evaluate(sym,frame(sym))
            if r and r["fresh"] and st.get(sym)!=r["week"]:
                hits.append(r); st[sym]=r["week"]
        except Exception as e: print(sym,e)
        if i%25==0: print(i)
        time.sleep(.15)
    save_state(st)
    if hits:
        msg=["🚨 WEEKLY MOMENTUM SIGNALS",""]
        for x in hits:
            msg += [f"📈 {x['symbol']}",f"Week: {x['week']}",
                    f"Weekly Close: ₹{x['close']:.2f}",
                    f"Weekly Supertrend: ₹{x['supertrend']:.2f}",
                    f"Weekly BB Upper: ₹{x['bb']:.2f}",
                    f"Weekly RSI: {x['rsi']:.2f}",
                    f"1D-ago Close: ₹{x['daily_prev_close']:.2f}",
                    f"20 SMA: ₹{x['daily_sma20']:.2f}","",
                    "✅ W Close >= W Supertrend","✅ W Close >= W BB",
                    "✅ W RSI >= 60","✅ 1D-ago Close < 20 SMA",""]
        telegram("\n".join(msg))
    print("Fresh signals:",len(hits))

if __name__=="__main__":
    main()
