import numpy as np
import pandas as pd

def rma(s, period):
    s = pd.Series(s, dtype="float64")
    out = pd.Series(np.nan, index=s.index)
    if len(s) < period:
        return out
    out.iloc[period-1] = s.iloc[:period].mean()
    a = 1.0 / period
    for i in range(period, len(s)):
        out.iloc[i] = out.iloc[i-1] + a * (s.iloc[i] - out.iloc[i-1])
    return out

def atr(df, period=10):
    pc = df["Close"].shift(1)
    tr = pd.concat([
        df["High"]-df["Low"],
        (df["High"]-pc).abs(),
        (df["Low"]-pc).abs()
    ], axis=1).max(axis=1)
    return rma(tr, period)

def supertrend(df, period=10, multiplier=3.0):
    h,l,c = df["High"],df["Low"],df["Close"]
    a = atr(df, period)
    mid = (h+l)/2
    bu, bl = mid+multiplier*a, mid-multiplier*a
    fu = pd.Series(np.nan,index=df.index)
    fl = pd.Series(np.nan,index=df.index)
    st = pd.Series(np.nan,index=df.index)
    for i in range(len(df)):
        if pd.isna(a.iloc[i]): continue
        if i == 0 or pd.isna(fu.iloc[i-1]):
            fu.iloc[i], fl.iloc[i], st.iloc[i] = bu.iloc[i], bl.iloc[i], bu.iloc[i]
            continue
        pc = c.iloc[i-1]
        fu.iloc[i] = bu.iloc[i] if bu.iloc[i] < fu.iloc[i-1] or pc > fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i] = bl.iloc[i] if bl.iloc[i] > fl.iloc[i-1] or pc < fl.iloc[i-1] else fl.iloc[i-1]
        if st.iloc[i-1] == fu.iloc[i-1]:
            st.iloc[i] = fl.iloc[i] if c.iloc[i] > fu.iloc[i] else fu.iloc[i]
        else:
            st.iloc[i] = fu.iloc[i] if c.iloc[i] < fl.iloc[i] else fl.iloc[i]
    return st

def rsi(s, period=14):
    d = pd.Series(s,dtype="float64").diff()
    gain, loss = d.clip(lower=0), -d.clip(upper=0)
    ag, al = rma(gain,period), rma(loss,period)
    rs = ag/al.replace(0,np.nan)
    out = 100-(100/(1+rs))
    return out.where(~((al==0)&(ag>0)),100)

def bollinger_upper(s, period=20, mult=2.0):
    s=pd.Series(s,dtype="float64")
    return s.rolling(period).mean()+mult*s.rolling(period).std(ddof=0)

def sma(s, period=20):
    return pd.Series(s,dtype="float64").rolling(period).mean()
