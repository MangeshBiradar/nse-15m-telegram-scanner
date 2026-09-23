
import numpy as np, pandas as pd

def wilder_rma(s,p):
    s=pd.Series(s,dtype=float); o=pd.Series(np.nan,index=s.index)
    if len(s)<p:return o
    o.iloc[p-1]=s.iloc[:p].mean(); a=1/p
    for i in range(p,len(s)): o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
    return o

def supertrend(d,period=10,multiplier=3):
    a=wilder_rma(pd.concat([d.High-d.Low,(d.High-d.Close.shift()).abs(),
                             (d.Low-d.Close.shift()).abs()],axis=1).max(axis=1),period)
    h=(d.High+d.Low)/2
    bu=h+multiplier*a; bl=h-multiplier*a
    fu=pd.Series(np.nan,index=d.index); fl=fu.copy(); st=fu.copy()
    first=np.where(~a.isna())[0]
    if not len(first): return pd.DataFrame({"supertrend":st},index=d.index)
    i=first[0]; fu.iloc[i]=bu.iloc[i]; fl.iloc[i]=bl.iloc[i]
    st.iloc[i]=fu.iloc[i] if d.Close.iloc[i]<=fu.iloc[i] else fl.iloc[i]
    for j in range(i+1,len(d)):
        fu.iloc[j]=bu.iloc[j] if bu.iloc[j]<fu.iloc[j-1] or d.Close.iloc[j-1]>fu.iloc[j-1] else fu.iloc[j-1]
        fl.iloc[j]=bl.iloc[j] if bl.iloc[j]>fl.iloc[j-1] or d.Close.iloc[j-1]<fl.iloc[j-1] else fl.iloc[j-1]
        if st.iloc[j-1]==fu.iloc[j-1]:
            st.iloc[j]=fu.iloc[j] if d.Close.iloc[j]<=fu.iloc[j] else fl.iloc[j]
        else:
            st.iloc[j]=fl.iloc[j] if d.Close.iloc[j]>=fl.iloc[j] else fu.iloc[j]
    return pd.DataFrame({"supertrend":st},index=d.index)

def rsi_wilder(c,p=14):
    d=c.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=wilder_rma(g,p); al=wilder_rma(l,p)
    r=100-100/(1+ag/al.replace(0,np.nan))
    return r.where(~((al==0)&(ag>0)),100)

def bollinger(c,p=20,std=2):
    m=c.rolling(p).mean(); s=c.rolling(p).std(ddof=0)
    return m,m+std*s,m-std*s
