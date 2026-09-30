import math
import time
import pandas as pd
import numpy as np
import yfinance as yf
from indicators import supertrend, rsi, bb_upper, sma

IST = "Asia/Kolkata"
END = pd.Timestamp.now(tz=IST).normalize().tz_localize(None)
START = END - pd.DateOffset(months=3)
BATCH = 30

def clean(d):
    if d is None or d.empty:
        return None
    d = d.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] if isinstance(x, tuple) else x for x in d.columns]
    need = ["Open","High","Low","Close"]
    if not all(x in d.columns for x in need):
        return None
    d.index = pd.to_datetime(d.index)
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert(IST).tz_localize(None)
    return d.dropna(subset=need).sort_index()

def tf(d, rule):
    x = d.resample(rule).agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"})
    return x.dropna(subset=["Open","High","Low","Close"])

def asof_signal(d, day):
    # Critical non-repaint rule: ONLY data with timestamp <= signal day is used.
    d = d.loc[d.index <= day].copy()
    if len(d) < 120:
        return False
    w, m = tf(d, "W-FRI"), tf(d, "ME")
    if len(w) < 60 or len(m) < 20:
        return False

    st = supertrend(w, 7, 3)
    wr = rsi(w["Close"], 14)
    wb = bb_upper(w["Close"], 20, 2)
    mr = rsi(m["Close"], 14)
    ds = sma(d["Close"], 20)

    vals = [
        w["Close"].iloc[-1], st.iloc[-1], wr.iloc[-1], wb.iloc[-1],
        mr.iloc[-1], d["Close"].iloc[-2], ds.iloc[-2],
        d["Close"].iloc[-1], ds.iloc[-1],
    ]
    if not all(math.isfinite(float(x)) for x in vals):
        return False

    return (
        vals[0] >= vals[1] and
        vals[2] > 60 and
        vals[0] >= vals[3] and
        vals[4] > 60 and
        vals[5] < vals[6] and
        vals[7] > vals[8]
    )

def forward_metrics(d, signal_day, entry):
    future = d.loc[d.index > signal_day]
    if future.empty:
        return {}
    out = {}
    for n in (1,3,5,10):
        x = future.iloc[:n]
        if len(x) < n:
            out[f"close_{n}d"] = np.nan
            out[f"high_{n}d"] = np.nan
        else:
            out[f"close_{n}d"] = 100*(x.Close.iloc[-1]/entry-1)
            out[f"high_{n}d"] = 100*(x.High.max()/entry-1)
    x = future.iloc[:10]
    out["max_gain_10d"] = 100*(x.High.max()/entry-1) if len(x) else np.nan
    out["max_dd_10d"] = 100*(x.Low.min()/entry-1) if len(x) else np.nan
    return out

def summarize(df, name):
    if df.empty:
        return {"test":name,"signals":0}
    z = {"test":name,"signals":len(df),"unique_stocks":df.ticker.nunique()}
    for n in (1,3,5,10):
        for kind in ("close","high"):
            c=f"{kind}_{n}d"
            if c in df:
                a=df[c].dropna()
                z[f"avg_{c}"]=a.mean()
                z[f"win_{c}_positive_pct"]=(a>0).mean()*100
                if kind=="high":
                    for target in (5,7.5,10):
                        z[f"hit_{target:g}_pct_{n}d"]=(a>=target).mean()*100
    a=df.get("max_dd_10d",pd.Series(dtype=float)).dropna()
    z["avg_max_dd_10d"]=a.mean() if len(a) else np.nan
    return z

u=pd.read_csv("symbols.csv")
tickers=(u.YF_TICKER.dropna().astype(str).str.strip()
         .loc[lambda x:x.ne("") & x.ne("nan")].unique().tolist())

rows=[]
failed=[]
print(f"Universe={len(tickers)} start={START.date()} end={END.date()}")

for i in range(0,len(tickers),BATCH):
    batch=tickers[i:i+BATCH]
    try:
        data=yf.download(
            batch,
            start=(START-pd.Timedelta(days=420)).strftime("%Y-%m-%d"),
            end=(END+pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d", group_by="ticker", auto_adjust=False,
            progress=False, threads=True,
        )
    except Exception as e:
        failed.extend((t,"daily_download",str(e)) for t in batch)
        continue

    for t in batch:
        try:
            d=clean(data if len(batch)==1 else (data[t] if t in data.columns.get_level_values(0) else None))
            if d is None:
                continue
            days=d.index[(d.index>=START)&(d.index<=END)]
            for day in days:
                if asof_signal(d, day):
                    entry=float(d.loc[day,"Close"])
                    row={"ticker":t,"signal_date":day.date(),"entry":entry}
                    row.update(forward_metrics(d,day,entry))
                    rows.append(row)
        except Exception as e:
            failed.append((t,"daily",str(e)))
    time.sleep(.15)

result=pd.DataFrame(rows)
result.to_csv("backtest_3m_non_repaint.csv",index=False)
pd.DataFrame([summarize(result,"3M_NON_REPAINT")]).to_csv("backtest_summary.csv",index=False)
pd.DataFrame(failed,columns=["ticker","stage","error"]).to_csv("backtest_failures.csv",index=False)

print("\nSUMMARY")
print(pd.DataFrame([summarize(result,"3M_NON_REPAINT")]).to_string(index=False))
print("\nNon-repaint signals:",len(result),"Failures:",len(failed))
