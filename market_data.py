import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from indicators import supertrend, rsi, bb_upper, sma

SNAPSHOT = "market_snapshot.csv"
BATCH = 25
IST = ZoneInfo("Asia/Kolkata")

COLUMNS = [
    "snapshot_date", "previous_date", "weekly_date", "monthly_date",
    "exchange", "symbol", "ticker", "name",
    "previous_close", "previous_sma20",
    "weekly_close", "weekly_rsi", "weekly_supertrend", "weekly_upper_bb",
    "monthly_rsi",
]

def flatten(d):
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] if isinstance(x, tuple) else x for x in d.columns]
    return d

def clean(d):
    if d is None or d.empty:
        return None
    d = flatten(d.copy())
    needed = ["Open", "High", "Low", "Close"]
    if not all(x in d.columns for x in needed):
        return None
    d.index = pd.to_datetime(d.index)
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert(IST).tz_localize(None)
    return d.dropna(subset=needed).sort_index()

def timeframe_ohlcv(d, rule):
    x = d.resample(rule).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    })
    return x.dropna(subset=["Open", "High", "Low", "Close"])

def build_row(d, meta, snapshot_date):
    d = clean(d)
    if d is None or len(d) < 120:
        return None

    w = timeframe_ohlcv(d, "W-FRI")
    m = timeframe_ohlcv(d, "ME")
    if len(w) < 60 or len(m) < 20:
        return None

    # Universe refresh runs before market open, so these are completed bars.
    d_sma20 = sma(d["Close"], 20)
    w_st = supertrend(w, 7, 3)
    w_rsi = rsi(w["Close"], 14)
    w_bb = bb_upper(w["Close"], 20, 2)
    m_rsi = rsi(m["Close"], 14)

    vals = [
        d["Close"].iloc[-1], d_sma20.iloc[-1],
        w["Close"].iloc[-1], w_rsi.iloc[-1], w_st.iloc[-1], w_bb.iloc[-1],
        m_rsi.iloc[-1],
    ]
    if not all(math.isfinite(float(x)) for x in vals):
        return None

    return {
        "snapshot_date": snapshot_date,
        "previous_date": d.index[-1].date().isoformat(),
        "weekly_date": w.index[-1].date().isoformat(),
        "monthly_date": m.index[-1].date().isoformat(),
        "exchange": meta.get("EXCHANGE", ""),
        "symbol": meta.get("SYMBOL", ""),
        "ticker": meta.get("YF_TICKER", ""),
        "name": meta.get("NAME", ""),
        "previous_close": float(d["Close"].iloc[-1]),
        "previous_sma20": float(d_sma20.iloc[-1]),
        "weekly_close": float(w["Close"].iloc[-1]),
        "weekly_rsi": float(w_rsi.iloc[-1]),
        "weekly_supertrend": float(w_st.iloc[-1]),
        "weekly_upper_bb": float(w_bb.iloc[-1]),
        "monthly_rsi": float(m_rsi.iloc[-1]),
    }

def refresh(snapshot=SNAPSHOT, symbols_path="symbols.csv"):
    u = pd.read_csv(symbols_path)
    u["YF_TICKER"] = u["YF_TICKER"].fillna("").astype(str).str.strip()
    u = u[u["YF_TICKER"].ne("") & u["YF_TICKER"].ne("nan")].copy()

    tickers = u["YF_TICKER"].drop_duplicates().tolist()
    meta = u.drop_duplicates("YF_TICKER").set_index("YF_TICKER")

    snapshot_date = datetime.now(IST).date().isoformat()
    rows = []
    failed = 0

    print(f"Building market snapshot for {len(tickers):,} tickers")

    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        data = None
        for attempt in range(3):
            try:
                data = yf.download(
                    batch,
                    period="2y",
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                    timeout=45,
                )
                if data is not None and not data.empty:
                    break
            except Exception as e:
                print(f"historical batch attempt {attempt + 1}/3 failed: {e}")
            time.sleep(2 * (attempt + 1))

        if data is None or data.empty:
            print("Historical batch returned no data; retrying tickers individually.")
            for ticker in batch:
                try:
                    one = yf.download(
                        ticker,
                        period="2y",
                        interval="1d",
                        auto_adjust=False,
                        progress=False,
                        threads=False,
                        timeout=45,
                    )
                    if one is None or one.empty:
                        failed += 1
                        continue
                    row = build_row(one, meta.loc[ticker], snapshot_date)
                    if row:
                        rows.append(row)
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    print(ticker, "individual snapshot skip:", e)
                time.sleep(0.15)
            continue

        for ticker in batch:
            try:
                d = data if len(batch) == 1 else (
                    data[ticker] if ticker in data.columns.get_level_values(0) else None
                )
                if d is None:
                    failed += 1
                    continue
                row = build_row(d, meta.loc[ticker], snapshot_date)
                if row:
                    rows.append(row)
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(ticker, "snapshot skip:", e)

        print(f"Snapshot progress: {min(i+BATCH, len(tickers)):,}/{len(tickers):,}")
        time.sleep(0.2)

    new = pd.DataFrame(rows, columns=COLUMNS)

    # Never replace a good snapshot with a tiny/empty Yahoo result.
    if len(new) < 500 and os.path.exists(snapshot):
        old = pd.read_csv(snapshot)
        print(
            f"Snapshot refresh produced only {len(new)} rows; "
            f"keeping existing snapshot with {len(old)} rows."
        )
        return old

    if len(new) < 500:
        raise RuntimeError(
            f"Market snapshot validation failed: only {len(new):,} valid rows were produced."
        )

    new.to_csv(snapshot, index=False)
    print(
        f"Market snapshot: {len(new):,} rows | "
        f"failed/skipped: {failed:,} | file: {snapshot}"
    )
    return new

if __name__ == "__main__":
    refresh()
