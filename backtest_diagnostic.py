import math,time
import pandas as pd, numpy as np, yfinance as yf
from indicators import supertrend,rsi,bb_upper,sma
START=pd.Timestamp.now().normalize()-pd.Timedelta(days=60); END=pd.Timestamp.now().normalize()+pd.Timedelta(days=1)
def clean(d):
 if d is None or d.empty:return None
 if isinstance(d.columns,pd.MultiIndex): d.columns=[x[0] if isinstance(x,tuple) else x for x in d.columns]
 d.index=pd.to_datetime(d.index)
 if getattr(d.index,"tz",None) is not None:d.index=d.index.tz_convert("Asia/Kolkata").tz_localize(None)
 return d.dropna(subset=["Open","High","Low","Close"]).sort_index()
def res(d,r): return d.resample(r).agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna(subset=["Open","High","Low","Close"])
def conds(d,day):
 x=d[d.index<=day]
 if len(x)<120:return None
 w=res(x,"W-FRI")
 if len(w)<60:return None
 st=supertrend(w,7,3); wr=rsi(w.Close,14); ub=bb_upper(w.Close,20,2); ds=sma(x.Close,20)
 v=[w.Close.iloc[-1],st.iloc[-1],wr.iloc[-1],ub.iloc[-1],x.Close.iloc[-2],ds.iloc[-2],x.Close.iloc[-1],ds.iloc[-1]]
 if not all(math.isfinite(float(z)) for z in v):return None
 return [v[0]>=v[1],v[2]>60,v[0]>=v[3],v[4]<v[5],v[6]>v[7],all([v[0]>=v[1],v[2]>60,v[0]>=v[3],v[4]<v[5],v[6]>v[7]])]
u=pd.read_csv("symbols.csv"); ts=u.YF_TICKER.dropna().astype(str).str.strip().unique().tolist()
counts=np.zeros(6,dtype=int); rows=[]; n=0
for i in range(0,len(ts),30):
 b=ts[i:i+30]
 try:data=yf.download(b,start=(START-pd.Timedelta(days=500)).strftime("%Y-%m-%d"),end=END.strftime("%Y-%m-%d"),interval="1d",group_by="ticker",auto_adjust=False,progress=False,threads=True)
 except:continue
 for t in b:
  d=clean(data if len(b)==1 else (data[t] if t in data.columns.get_level_values(0) else None))
  if d is None:continue
  for day in d.index[(d.index>=START)&(d.index<END)]:
   c=conds(d,day)
   if c:
    n+=1; counts+=np.array(c,dtype=int)
    if c[-1]:rows.append((t,str(day.date()),float(d.loc[day,"Close"])))
 time.sleep(.03)
print("eligible day evaluations",n); print("condition counts",counts.tolist()); print("full matches",len(rows)); print("\n".join(map(str,rows[:100])))
