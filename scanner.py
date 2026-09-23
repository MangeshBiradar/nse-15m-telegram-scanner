import os,json,time,math,requests,pandas as pd,yfinance as yf
from indicators import supertrend,rsi,bb_upper,sma
from universe import refresh

STATE="state.json"; BATCH=30

def load():
    try:return json.load(open(STATE,encoding="utf-8"))
    except:return {}
def save(x):
    json.dump(x,open(STATE,"w",encoding="utf-8"),indent=2,sort_keys=True)

def tg(msg):
    t,c=os.getenv("TELEGRAM_BOT_TOKEN"),os.getenv("TELEGRAM_CHAT_ID")
    if not t or not c: print(msg); return
    r=requests.post(f"https://api.telegram.org/bot{t}/sendMessage",json={"chat_id":c,"text":msg},timeout=20); r.raise_for_status()

def flatten(d):
    if isinstance(d.columns,pd.MultiIndex): d.columns=[x[0] if isinstance(x,tuple) else x for x in d.columns]
    return d

def weekly(d):
    d=d.copy(); d.index=pd.to_datetime(d.index)
    w=d.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna(subset=["Close"])
    now=pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize()
    if len(w) and w.index[-1].normalize()>=now:w=w.iloc[:-1]
    return w

def signal(t,d):
    if d is None or d.empty:return None
    d=flatten(d).dropna(subset=["Open","High","Low","Close"])
    if len(d)<120:return None
    w=weekly(d)
    if len(w)<60:return None
    st=supertrend(w,10,3); rr=rsi(w.Close,14); bb=bb_upper(w.Close,20,2)
    wc,ws,wr,wb=map(float,[w.Close.iloc[-1],st.iloc[-1],rr.iloc[-1],bb.iloc[-1]])
    pc=float(d.Close.iloc[-2]); ps=float(sma(d.Close,20).iloc[-2])
    if not all(math.isfinite(x) for x in [wc,ws,wr,wb,pc,ps]):return None
    if wc>=ws and wc>=wb and wr>=60 and pc<ps:
        return {"ticker":t,"week":str(w.index[-1].date()),"close":wc,"st":ws,"bb":wb,"rsi":wr,"prev":pc,"sma20":ps}
    return None

def main():
    # Refresh universe only when requested by workflow; scanner then uses cache.
    u=pd.read_csv("symbols.csv"); state=load(); ts=u.YF_TICKER.dropna().astype(str).unique().tolist()
    print("Scanning",len(ts),"eligible stocks")
    fresh=bad=0
    for i in range(0,len(ts),BATCH):
        batch=ts[i:i+BATCH]
        try:data=yf.download(batch,period="2y",interval="1d",group_by="ticker",auto_adjust=False,progress=False,threads=True)
        except Exception as e:print("batch failed",e);bad+=len(batch);continue
        for t in batch:
            try:
                d=data if len(batch)==1 else (data[t] if t in data.columns.get_level_values(0) else None)
                s=signal(t,d)
                if not s:continue
                key=t+"|"+s["week"]
                if state.get(key):continue
                tg(f"📈 WEEKLY SETUP\\n\\n{t}\\nWeek: {s['week']}\\nClose: {s['close']:.2f}\\nSupertrend: {s['st']:.2f}\\nUpper BB: {s['bb']:.2f}\\nWeekly RSI: {s['rsi']:.2f}\\nPrev Close: {s['prev']:.2f}\\nPrev SMA20: {s['sma20']:.2f}")
                state[key]=True;fresh+=1
            except Exception as e:bad+=1;print(t,"skip",e)
        time.sleep(.5)
    save(state);print("Universe:",len(ts),"Failed/skipped:",bad,"Fresh signals:",fresh)

if __name__=="__main__":main()
