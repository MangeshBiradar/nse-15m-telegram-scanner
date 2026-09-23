import io,requests,pandas as pd
NSE_URLS=["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
          "https://archives.nseindia.com/content/equities/EQUITY_L.csv"]
EX=["ETF","INDEX","MUTUAL","REIT","INVIT","WARRANT","PREFERENCE","DEBENTURE","BOND","PARTLY PAID","IDR"]
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0","Accept":"*/*"})

def nse():
    err=None
    for u in NSE_URLS:
        try:
            r=S.get(u,timeout=30); r.raise_for_status(); d=pd.read_csv(io.BytesIO(r.content))
            d.columns=[str(x).strip().upper() for x in d.columns]
            if "SYMBOL" not in d: continue
            if "SERIES" in d: d=d[d.SERIES.astype(str).str.upper().eq("EQ")]
            ic="ISIN NUMBER" if "ISIN NUMBER" in d else ("ISIN" if "ISIN" in d else None)
            nc="NAME OF COMPANY" if "NAME OF COMPANY" in d else None
            d["ISIN"]=d[ic].astype(str).str.strip() if ic else ""
            d["SYMBOL"]=d.SYMBOL.astype(str).str.strip().str.upper()
            d["NAME"]=d[nc].astype(str).str.strip() if nc else d.SYMBOL
            d=d[~d.NAME.str.upper().apply(lambda x:any(w in x for w in EX))]
            return d[["SYMBOL","ISIN","NAME"]].assign(EXCHANGE="NSE")
        except Exception as e: err=e
    raise RuntimeError(f"NSE universe failed: {err}")

def bse():
    urls=["https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?pageno=1&strSearch=&strSearchText=&pageSize=5000",
          "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"]
    for u in urls:
        try:
            r=S.get(u,timeout=30,headers={**S.headers,"Referer":"https://www.bseindia.com/"}); r.raise_for_status()
            x=r.json(); rows=x.get("Table") or x.get("Table1") or x
            d=pd.DataFrame(rows); d.columns=[str(c).strip().upper() for c in d.columns]
            def c(*a): return next((z for z in a if z in d.columns),None)
            code,sym,name,isin=c("SCRIP_CD","SCRIPCODE","SCRIP_CODE"),c("SCRIP_ID","SCRIPID","SYMBOL"),c("SCRIP_NAME","SCRIPNAME","SECURITY_NAME","NAME"),c("ISIN_NO","ISIN","ISINNUMBER")
            if not code:continue
            o=pd.DataFrame(); o["BSE_CODE"]=d[code].astype(str).str.extract(r"(\d+)")[0]
            o["SYMBOL"]=d[sym].astype(str).str.strip().str.upper() if sym else o.BSE_CODE
            o["NAME"]=d[name].astype(str).str.strip() if name else o.SYMBOL
            o["ISIN"]=d[isin].astype(str).str.strip() if isin else ""
            o=o[~o.NAME.str.upper().apply(lambda x:any(w in x for w in EX))]
            o=o[o.BSE_CODE.notna() & o.BSE_CODE.ne("")]
            return o[["SYMBOL","ISIN","NAME","BSE_CODE"]].assign(EXCHANGE="BSE")
        except Exception as e: print("BSE:",e)
    return pd.DataFrame(columns=["SYMBOL","ISIN","NAME","BSE_CODE","EXCHANGE"])

def refresh(path="symbols.csv"):
    d=pd.concat([nse(),bse()],ignore_index=True,sort=False); d["ISIN"]=d.ISIN.fillna("").astype(str).str.strip()
    d["_key"]=d.ISIN; d.loc[d._key.eq(""),"_key"]=d.NAME.str.upper().str.replace(r"[^A-Z0-9]","",regex=True)
    d["YF_TICKER"]=d.apply(lambda r:r.SYMBOL+".NS" if r.EXCHANGE=="NSE" else str(r.BSE_CODE)+".BO",axis=1)
    d["_p"]=d.EXCHANGE.map({"NSE":0,"BSE":1}).fillna(9)
    d=d.sort_values(["_key","_p"]).drop_duplicates("_key",keep="first")
    out=d[["EXCHANGE","SYMBOL","BSE_CODE","ISIN","NAME","YF_TICKER"]]; out.to_csv(path,index=False)
    print(f"Universe: {len(out)} | NSE: {(out.EXCHANGE=='NSE').sum()} | BSE-only: {(out.EXCHANGE=='BSE').sum()}")
    return out

if __name__=="__main__":refresh()
