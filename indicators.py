import numpy as np
import pandas as pd

def rma(s, n):
    s=pd.Series(s,dtype="float64"); out=pd.Series(np.nan,index=s.index)
    if len(s)<n:return out
    out.iloc[n-1]=s.iloc[:n].mean(); a=1/n
    for i in range(n,len(s)): out.iloc[i]=out.iloc[i-1]+a*(s.iloc[i]-out.iloc[i-1])
    return out

def atr(df,n=10):
    pc=df.Close.shift(1)
    tr=pd.concat([df.High-df.Low,(df.High-pc).abs(),(df.Low-pc).abs()],axis=1).max(axis=1)
    return rma(tr,n)

def supertrend(df,n=10,m=3):
    a=atr(df,n); mid=(df.High+df.Low)/2
    bu,bl=mid+m*a,mid-m*a
    fu=pd.Series(np.nan,index=df.index); fl=fu.copy(); st=fu.copy()
    for i in range(len(df)):
        if pd.isna(a.iloc[i]):continue
        if i==0 or pd.isna(fu.iloc[i-1]):
            fu.iloc[i],fl.iloc[i],st.iloc[i]=bu.iloc[i],bl.iloc[i],bu.iloc[i]; continue
        pc=df.Close.iloc[i-1]
        fu.iloc[i]=bu.iloc[i] if bu.iloc[i]<fu.iloc[i-1] or pc>fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i]=bl.iloc[i] if bl.iloc[i]>fl.iloc[i-1] or pc<fl.iloc[i-1] else fl.iloc[i-1]
        st.iloc[i]=fl.iloc[i] if st.iloc[i-1]==fu.iloc[i-1] and df.Close.iloc[i]>fu.iloc[i] else (
            fu.iloc[i] if st.iloc[i-1]==fu.iloc[i-1] else (
                fu.iloc[i] if df.Close.iloc[i]<fl.iloc[i] else fl.iloc[i]))
    return st

def rsi(s,n=14):
    d=pd.Series(s,dtype="float64").diff()
    g,l=d.clip(lower=0),-d.clip(upper=0)
    ag,al=rma(g,n),rma(l,n); rs=ag/al.replace(0,np.nan)
    x=100-100/(1+rs)
    return x.where(~((al==0)&(ag>0)),100)

def bb_upper(s,n=20,m=2):
    s=pd.Series(s,dtype="float64"); return s.rolling(n).mean()+m*s.rolling(n).std(ddof=0)

def sma(s,n=20): return pd.Series(s,dtype="float64").rolling(n).mean()
