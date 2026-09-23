import io, os, time, requests, pandas as pd

# NSE currently publishes the equity-segment security list from this page/link.
# Keep the direct archive URL as the primary source and bootstrap an NSE session
# before requesting it because NSE may reject a cold request.
NSE_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://www.nseindia.com/content/equities/EQUITY_L.csv",
]
NSE_HOME = "https://www.nseindia.com/static/market-data/securities-available-for-trading"

EX = ["ETF", "INDEX", "MUTUAL", "REIT", "INVIT", "WARRANT", "PREFERENCE",
      "DEBENTURE", "BOND", "PARTLY PAID", "IDR"]

# Emergency seed: prevents an empty repository from becoming permanently
# unscannable when an exchange endpoint is temporarily unavailable. The next
# successful universe refresh replaces this with the live NSE+BSE universe.
SEED_NSE = [
    "360ONE","3MINDIA","ABB","ACC","ADANIENT","ADANIGREEN","ADANIPORTS",
    "ADANIPOWER","ATGL","ABCAPITAL","ABFRL","ABSLAMC","ADVENZYMES","AEGISLOG",
    "AIAENG","APLAPOLLO","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO",
    "BAJFINANCE","BAJAJFINSV","BEL","BHARTIARTL","BPCL","BRITANNIA","CIPLA",
    "COALINDIA","DIVISLAB","DRREDDY","EICHERMOT","ETERNAL","GRASIM","HCLTECH",
    "HDFCBANK","HDFCLIFE","HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK",
    "INDUSINDBK","INFY","ITC","JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI",
    "NESTLEIND","NTPC","ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS","TECHM","TITAN",
    "TRENT","ULTRACEMCO","WIPRO","ZYDUSLIFE"
]

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
})


def _clean_equity(d, exchange="NSE"):
    d = d.copy()
    d.columns = [str(x).strip().upper() for x in d.columns]
    if "SYMBOL" not in d.columns:
        return pd.DataFrame()
    if "SERIES" in d.columns:
        series = d["SERIES"].astype(str).str.strip().str.upper()
        d = d[series.isin(["EQ", "BE"])]
    ic = "ISIN NUMBER" if "ISIN NUMBER" in d else ("ISIN" if "ISIN" in d else None)
    nc = "NAME OF COMPANY" if "NAME OF COMPANY" in d else None
    d["ISIN"] = d[ic].astype(str).str.strip() if ic else ""
    d["SYMBOL"] = d["SYMBOL"].astype(str).str.strip().str.upper()
    d["NAME"] = d[nc].astype(str).str.strip() if nc else d["SYMBOL"]
    d = d[d["SYMBOL"].ne("") & d["SYMBOL"].ne("NAN")]
    d = d[~d["NAME"].str.upper().apply(lambda x: any(w in x for w in EX))]
    return d[["SYMBOL", "ISIN", "NAME"]].assign(EXCHANGE=exchange)


def nse():
    last = None
    for attempt in range(3):
        try:
            # Bootstrap cookies/session first.
            try:
                S.get(NSE_HOME, timeout=20)
            except Exception:
                pass
            for u in NSE_URLS:
                try:
                    r = S.get(u, timeout=30, headers={**S.headers, "Referer": NSE_HOME})
                    r.raise_for_status()
                    d = pd.read_csv(io.BytesIO(r.content))
                    out = _clean_equity(d, "NSE")
                    if len(out) >= 100:
                        return out
                    last = RuntimeError(f"NSE list too small: {len(out)} rows")
                except Exception as e:
                    last = e
            time.sleep(2 ** attempt)
        except Exception as e:
            last = e
    raise RuntimeError(f"NSE universe failed: {last}")


def bse():
    urls = [
        "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?pageno=1&strSearch=&strSearchText=&pageSize=5000",
        "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?pageno=1&pageSize=10000",
        "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w",
    ]
    last = None
    for attempt in range(3):
        for u in urls:
            try:
                r = S.get(u, timeout=30, headers={**S.headers, "Referer": "https://www.bseindia.com/"})
                r.raise_for_status()
                x = r.json()
                rows = x.get("Table") or x.get("Table1") or x
                d = pd.DataFrame(rows)
                d.columns = [str(c).strip().upper() for c in d.columns]

                def c(*a):
                    return next((z for z in a if z in d.columns), None)

                code = c("SCRIP_CD", "SCRIPCODE", "SCRIP_CODE")
                sym = c("SCRIP_ID", "SCRIPID", "SYMBOL")
                name = c("SCRIP_NAME", "SCRIPNAME", "SECURITY_NAME", "NAME")
                isin = c("ISIN_NO", "ISIN", "ISINNUMBER")
                if not code:
                    continue

                o = pd.DataFrame()
                o["BSE_CODE"] = d[code].astype(str).str.extract(r"(\d+)")[0]
                o["SYMBOL"] = d[sym].astype(str).str.strip().str.upper() if sym else o["BSE_CODE"]
                o["NAME"] = d[name].astype(str).str.strip() if name else o["SYMBOL"]
                o["ISIN"] = d[isin].astype(str).str.strip() if isin else ""
                o = o[o.BSE_CODE.notna() & o.BSE_CODE.ne("")]
                o = o[o.SYMBOL.ne("") & o.SYMBOL.ne("NAN")]
                o = o[~o.NAME.str.upper().apply(lambda x: any(w in x for w in EX))]
                if len(o) >= 100:
                    return o[["SYMBOL", "ISIN", "NAME", "BSE_CODE"]].assign(EXCHANGE="BSE")
                last = RuntimeError(f"BSE list too small: {len(o)} rows")
            except Exception as e:
                last = e
        time.sleep(2 ** attempt)
    print("BSE unavailable:", last)
    return pd.DataFrame(columns=["SYMBOL", "ISIN", "NAME", "BSE_CODE", "EXCHANGE"])


def seed_frame():
    return pd.DataFrame({
        "EXCHANGE": ["NSE"] * len(SEED_NSE),
        "SYMBOL": SEED_NSE,
        "BSE_CODE": [""] * len(SEED_NSE),
        "ISIN": [""] * len(SEED_NSE),
        "NAME": SEED_NSE,
        "YF_TICKER": [s + ".NS" for s in SEED_NSE],
    })


def _existing(path):
    try:
        d = pd.read_csv(path)
        if "YF_TICKER" in d and len(d) >= 100:
            return d
    except Exception:
        pass
    return None


def refresh(path="symbols.csv"):
    old = _existing(path)
    parts = []
    try:
        parts.append(nse())
    except Exception as e:
        print("NSE:", e)
    try:
        b = bse()
        if len(b):
            parts.append(b)
    except Exception as e:
        print("BSE:", e)

    if not parts:
        if old is not None:
            print(f"Live universe unavailable; keeping existing universe: {len(old)}")
            return old
        out = seed_frame()
        out.to_csv(path, index=False)
        print(f"Live universe unavailable; installed emergency NSE seed: {len(out)}")
        return out

    d = pd.concat(parts, ignore_index=True, sort=False)
    d["ISIN"] = d.ISIN.fillna("").astype(str).str.strip()
    d["NAME"] = d.NAME.fillna(d.SYMBOL).astype(str).str.strip()
    d["SYMBOL"] = d.SYMBOL.fillna("").astype(str).str.strip().str.upper()
    d["_key"] = d.ISIN
    d.loc[d._key.eq(""), "_key"] = d.NAME.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    d["BSE_CODE"] = d.get("BSE_CODE", "").fillna("").astype(str).str.extract(r"(\d+)")[0].fillna("")
    d["YF_TICKER"] = d.apply(
        lambda r: r.SYMBOL + ".NS" if r.EXCHANGE == "NSE" else r.BSE_CODE + ".BO", axis=1
    )
    d = d[d.YF_TICKER.str.len().gt(3)]
    d["_p"] = d.EXCHANGE.map({"NSE": 0, "BSE": 1}).fillna(9)
    d = d.sort_values(["_key", "_p"]).drop_duplicates("_key", keep="first")
    out = d[["EXCHANGE", "SYMBOL", "BSE_CODE", "ISIN", "NAME", "YF_TICKER"]]

    # Never replace a known-good universe with a suspiciously small partial feed.
    if len(out) < 100:
        if old is not None:
            print(f"Live universe suspiciously small ({len(out)}); keeping existing {len(old)}")
            return old
        out = seed_frame()
        print(f"Live universe suspiciously small; using emergency NSE seed: {len(out)}")

    out.to_csv(path, index=False)
    print(f"Universe: {len(out)} | NSE: {(out.EXCHANGE=='NSE').sum()} | BSE-only: {(out.EXCHANGE=='BSE').sum()}")
    return out


if __name__ == "__main__":
    refresh()
