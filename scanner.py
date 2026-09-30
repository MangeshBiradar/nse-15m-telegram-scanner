import os
import json
import time
import math
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

STATE = "state.json"
RESULTS = "scan_results.csv"
RUN_HISTORY = "scan_runs.csv"
SNAPSHOT = "market_snapshot.csv"
BATCH = 40
IST = ZoneInfo("Asia/Kolkata")
STRATEGY = "MSB_BASE_V4_ASOF_15M_CONFIRMATION"

STRATEGY_LINES = [
    "• Weekly Close >= Weekly Supertrend(7,3)",
    "• Weekly RSI(14) > 60",
    "• Weekly Close >= Weekly Upper BB(20,2)",
    "• Monthly RSI(14) > 60",
    "• Previous Daily Close < Previous Daily SMA20",
    "• Current Daily Close > Current Daily SMA20",
    "• 15-minute Close > 15-minute Supertrend(7,3)",
    "• 15-minute RSI(14) > 55",
    "• 15-minute Close > Previous 15-minute High",
]

RESULT_COLUMNS = [
    "scan_date","scan_time_ist","run_type",
    "exchange","symbol","ticker","name",
    "signal_price","price_source",
    "previous_date","previous_close","previous_sma20","daily_sma20",
    "weekly_date","weekly_close","weekly_rsi","weekly_supertrend","weekly_upper_bb",
    "monthly_date","monthly_rsi",
    "intraday_15m_time","intraday_15m_close","intraday_15m_rsi",
    "intraday_15m_supertrend","previous_15m_high","strategy"
]

def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save(x):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(x, f, indent=2, sort_keys=True)

def tg(msg):
    t, c = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not t or not c:
        print("Telegram: NOT CONFIGURED")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{t}/sendMessage",
            json={"chat_id": c, "text": msg},
            timeout=20,
        )
        print("Telegram:", "sent" if r.ok else f"FAILED HTTP {r.status_code}: {r.text[:300]}")
        return r.ok
    except Exception as e:
        print("Telegram: FAILED:", e)
        return False

def run_type():
    explicit = os.getenv("RUN_TYPE")
    if explicit:
        return explicit
    return "Manual" if os.getenv("GITHUB_EVENT_NAME", "schedule") == "workflow_dispatch" else "Scheduled"

def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"

def clean_intraday(d):
    if d is None or d.empty:
        return None
    d = d.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] if isinstance(x, tuple) else x for x in d.columns]
    if "Close" not in d.columns:
        return None
    d.index = pd.to_datetime(d.index)
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert(IST).tz_localize(None)
    return d.sort_index().dropna(subset=["Close"])

def get_live_bars(tickers):
    """Fetch fresh intraday data and evaluate only completed 15-minute bars.
    1-minute data supplies the latest CMP/current-day OHLC.
    15-minute history supplies enough bars for RSI/Supertrend confirmation.
    """
    bars = {}
    failed = 0
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        try:
            live = yf.download(
                batch, period="1d", interval="1m",
                group_by="ticker", auto_adjust=False, prepost=False,
                progress=False, threads=True, timeout=25,
            )
            intraday15 = yf.download(
                batch, period="5d", interval="15m",
                group_by="ticker", auto_adjust=False, prepost=False,
                progress=False, threads=True, timeout=25,
            )
        except Exception as e:
            failed += len(batch)
            print("Intraday batch failed:", e)
            continue

        for ticker in batch:
            try:
                d = live if len(batch) == 1 else (
                    live[ticker] if ticker in live.columns.get_level_values(0) else None
                )
                d = clean_intraday(d)
                if d is None or d.empty:
                    failed += 1
                    continue

                latest_day = d.index[-1].date()
                x = d[d.index.date == latest_day]
                if x.empty:
                    failed += 1
                    continue

                q = intraday15 if len(batch) == 1 else (
                    intraday15[ticker] if ticker in intraday15.columns.get_level_values(0) else None
                )
                q = clean_intraday(q)
                if q is None or q.empty:
                    failed += 1
                    continue

                # Use the latest completed 15-minute candle, never a partial candle.
                now_ist = datetime.now(IST).replace(tzinfo=None)
                cutoff = now_ist.replace(
                    minute=(now_ist.minute // 15) * 15,
                    second=0, microsecond=0
                )
                completed = q[q.index + pd.Timedelta(minutes=15) <= cutoff]
                if len(completed) < 20:
                    failed += 1
                    continue

                p = completed.iloc[-1]
                prev = completed.iloc[-2]
                close15 = float(p["Close"])
                rsi15 = float(rsi(completed["Close"], 14).iloc[-1])
                st15 = float(supertrend(completed, 7, 3).iloc[-1])
                prev_high15 = float(prev["High"])

                values = [close15, rsi15, st15, prev_high15]
                if not all(math.isfinite(v) for v in values):
                    failed += 1
                    continue

                bars[ticker] = {
                    "date": latest_day.isoformat(),
                    "open": float(x["Open"].iloc[0]) if "Open" in x.columns else float(x["Close"].iloc[0]),
                    "high": float(x["High"].max()) if "High" in x.columns else float(x["Close"].max()),
                    "low": float(x["Low"].min()) if "Low" in x.columns else float(x["Close"].min()),
                    "close": float(x["Close"].iloc[-1]),
                    "last_bar_time": x.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
                    "15m_time": completed.index[-1].strftime("%Y-%m-%d %H:%M:%S"),
                    "15m_close": close15,
                    "15m_rsi": rsi15,
                    "15m_supertrend": st15,
                    "previous_15m_high": prev_high15,
                }
            except Exception as e:
                failed += 1
                print(ticker, "intraday skip:", e)
        time.sleep(0.15)
    return bars, failed
def decode_history(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.DataFrame()
    try:
        rows = json.loads(str(value))
        d = pd.DataFrame(rows)
        if d.empty:
            return d
        d["date"] = pd.to_datetime(d["date"])
        d = d.set_index("date").sort_index()
        for c in ["Open","High","Low","Close"]:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.dropna(subset=["Open","High","Low","Close"])
    except Exception:
        return pd.DataFrame()

def append_current_bar(history, bar):
    """Update the current weekly/monthly candle with today's live bar.
    Snapshot history is already resampled; do not append today's bar as a
    separate higher-timeframe candle because that would distort indicators.
    """
    d = history.copy().sort_index()
    if d.empty:
        return d
    idx = d.index[-1]
    d.loc[idx, "High"] = max(float(d.loc[idx, "High"]), float(bar["high"]))
    d.loc[idx, "Low"] = min(float(d.loc[idx, "Low"]), float(bar["low"]))
    d.loc[idx, "Close"] = float(bar["close"])
    return d

def rma(s, n):
    s = pd.Series(s, dtype="float64")
    out = pd.Series(float("nan"), index=s.index)
    if len(s) < n:
        return out
    out.iloc[n-1] = s.iloc[:n].mean()
    a = 1.0 / n
    for i in range(n, len(s)):
        out.iloc[i] = out.iloc[i-1] + a * (s.iloc[i] - out.iloc[i-1])
    return out

def atr(df, n=10):
    pc = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - pc).abs(),
        (df["Low"] - pc).abs()
    ], axis=1).max(axis=1)
    return rma(tr, n)

def supertrend(df, n=10, m=3):
    a = atr(df, n)
    mid = (df["High"] + df["Low"]) / 2
    bu, bl = mid + m*a, mid - m*a
    fu = pd.Series(float("nan"), index=df.index)
    fl = pd.Series(float("nan"), index=df.index)
    st = pd.Series(float("nan"), index=df.index)
    for i in range(len(df)):
        if pd.isna(a.iloc[i]):
            continue
        if i == 0 or pd.isna(fu.iloc[i-1]):
            fu.iloc[i], fl.iloc[i], st.iloc[i] = bu.iloc[i], bl.iloc[i], bu.iloc[i]
            continue
        pc = df["Close"].iloc[i-1]
        fu.iloc[i] = bu.iloc[i] if bu.iloc[i] < fu.iloc[i-1] or pc > fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i] = bl.iloc[i] if bl.iloc[i] > fl.iloc[i-1] or pc < fl.iloc[i-1] else fl.iloc[i-1]
        st.iloc[i] = (
            fl.iloc[i] if st.iloc[i-1] == fu.iloc[i-1] and df["Close"].iloc[i] > fu.iloc[i]
            else fu.iloc[i] if st.iloc[i-1] == fu.iloc[i-1]
            else fu.iloc[i] if df["Close"].iloc[i] < fl.iloc[i]
            else fl.iloc[i]
        )
    return st

def rsi(s, n=14):
    d = pd.Series(s, dtype="float64").diff()
    g, l = d.clip(lower=0), -d.clip(upper=0)
    ag, al = rma(g, n), rma(l, n)
    rs = ag / al.replace(0, float("nan"))
    x = 100 - 100 / (1 + rs)
    return x.where(~((al == 0) & (ag > 0)), 100)

def bb_upper(s, n=20, m=2):
    s = pd.Series(s, dtype="float64")
    return s.rolling(n).mean() + m*s.rolling(n).std(ddof=0)

def asof_setup(row, bar):
    if not bar or not math.isfinite(float(bar["close"])):
        return None

    previous_close = float(row["previous_close"])
    previous_sma20 = float(row["previous_sma20"])
    current_close = float(bar["close"])
    current_sma20 = previous_sma20 + (current_close - previous_close) / 20.0

    weekly = append_current_bar(decode_history(row.get("weekly_history")), bar)
    monthly = append_current_bar(decode_history(row.get("monthly_history")), bar)

    if len(weekly) < 60 or len(monthly) < 20:
        return None

    weekly_rsi = float(rsi(weekly["Close"], 14).iloc[-1])
    weekly_bb = float(bb_upper(weekly["Close"], 20, 2).iloc[-1])
    weekly_st = float(supertrend(weekly, 7, 3).iloc[-1])
    weekly_close = float(weekly["Close"].iloc[-1])
    monthly_rsi = float(rsi(monthly["Close"], 14).iloc[-1])

    values = [
        weekly_close, weekly_rsi, weekly_bb, weekly_st,
        monthly_rsi, current_sma20, float(bar["15m_close"]),
        float(bar["15m_rsi"]), float(bar["15m_supertrend"]),
        float(bar["previous_15m_high"])
    ]
    if not all(math.isfinite(x) for x in values):
        return None

    if not (
        weekly_close >= weekly_st
        and weekly_rsi > 60
        and weekly_close >= weekly_bb
        and monthly_rsi > 60
        and previous_close < previous_sma20
        and current_close > current_sma20
        and float(bar["15m_close"]) > float(bar["15m_supertrend"])
        and float(bar["15m_rsi"]) > 55
        and float(bar["15m_close"]) > float(bar["previous_15m_high"])
    ):
        return None

    return {
        "signal_date": bar["date"],
        "signal_price": current_close,
        "previous_date": str(row.get("previous_date", "")),
        "previous_close": previous_close,
        "previous_sma20": previous_sma20,
        "daily_sma20": current_sma20,
        "weekly_date": str(weekly.index[-1].date()),
        "weekly_close": weekly_close,
        "weekly_rsi": weekly_rsi,
        "weekly_supertrend": weekly_st,
        "weekly_upper_bb": weekly_bb,
        "monthly_date": str(monthly.index[-1].date()),
        "monthly_rsi": monthly_rsi,
        "last_bar_time": bar["last_bar_time"],
        "15m_time": bar["15m_time"],
        "15m_close": float(bar["15m_close"]),
        "15m_rsi": float(bar["15m_rsi"]),
        "15m_supertrend": float(bar["15m_supertrend"]),
        "previous_15m_high": float(bar["previous_15m_high"]),
    }

\ndef save_scan_results(matches, universe, now):
    if not matches:
        print("No scan matches to record.")
        return 0

    meta = universe.copy()
    meta["YF_TICKER"] = meta["YF_TICKER"].fillna("").astype(str).str.strip()
    meta = meta.drop_duplicates("YF_TICKER").set_index("YF_TICKER")
    rows = []
    for ticker, setup in matches:
        info = meta.loc[ticker] if ticker in meta.index else pd.Series(dtype=object)
        rows.append({
            "scan_date": setup["signal_date"],
            "scan_time_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
            "run_type": run_type(),
            "exchange": info.get("EXCHANGE", ""),
            "symbol": info.get("SYMBOL", ""),
            "ticker": ticker,
            "name": info.get("NAME", ""),
            "signal_price": setup["signal_price"],
            "price_source": "Latest/current 1m CMP + current-day OHLC from yfinance",
            "previous_date": setup["previous_date"],
            "previous_close": setup["previous_close"],
            "previous_sma20": setup["previous_sma20"],
            "daily_sma20": setup["daily_sma20"],
            "weekly_date": setup["weekly_date"],
            "weekly_close": setup["weekly_close"],
            "weekly_rsi": setup["weekly_rsi"],
            "weekly_supertrend": setup["weekly_supertrend"],
            "weekly_upper_bb": setup["weekly_upper_bb"],
            "monthly_date": setup["monthly_date"],
            "monthly_rsi": setup["monthly_rsi"],
            "intraday_15m_time": setup["15m_time"],
            "intraday_15m_close": setup["15m_close"],
            "intraday_15m_rsi": setup["15m_rsi"],
            "intraday_15m_supertrend": setup["15m_supertrend"],
            "previous_15m_high": setup["previous_15m_high"],
            "strategy": STRATEGY,
        })

    new = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    old = pd.read_csv(RESULTS) if os.path.exists(RESULTS) and os.path.getsize(RESULTS) > 0 else pd.DataFrame(columns=RESULT_COLUMNS)
    old = old.reindex(columns=RESULT_COLUMNS)
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["scan_date","scan_time_ist","ticker","strategy"], keep="first")
    combined = combined.sort_values(["scan_date","ticker"]).reset_index(drop=True)
    combined.to_csv(RESULTS, index=False)
    old_unique = old.drop_duplicates(subset=["scan_date","scan_time_ist","ticker","strategy"])
    added = max(0, len(combined) - len(old_unique))
    print(f"Stored {added} new scan results in {RESULTS}; total rows={len(combined)}")
    return added

def save_run_history(now, scanned, live_bars, matches, fresh, failed, stored, duration):
    columns = [
        "run_time_ist","run_type","scan_date","scanned","live_bars",
        "matches","new_alerts","failed","new_result_rows","duration_seconds","status"
    ]
    row = pd.DataFrame([{
        "run_time_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
        "run_type": run_type(),
        "scan_date": now.date().isoformat(),
        "scanned": scanned,
        "live_bars": live_bars,
        "matches": matches,
        "new_alerts": fresh,
        "failed": failed,
        "new_result_rows": stored,
        "duration_seconds": round(duration, 2),
        "status": "SUCCESS" if failed == 0 else "PARTIAL",
    }], columns=columns)
    old = pd.read_csv(RUN_HISTORY) if os.path.exists(RUN_HISTORY) and os.path.getsize(RUN_HISTORY) > 0 else pd.DataFrame(columns=columns)
    old = old.reindex(columns=columns)
    pd.concat([old, row], ignore_index=True).to_csv(RUN_HISTORY, index=False)
    print(f"Stored scan run in {RUN_HISTORY}; total runs={len(old)+1}")

def send_data_pull_alert(total, live_bars, failed, duration):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    return tg("\\n".join([
        "📡 NSE+BSE MARKET DATA PULL",
        "",
        f"🟢 Status: {status}" if failed == 0 else f"🟡 Status: {status}",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"📈 Stocks requested: {total:,}",
        f"✅ Fresh intraday data: {live_bars:,}",
        f"⚠️ Failed/skipped: {failed:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
        "",
        "Data: yfinance 1m CMP/current-day OHLC + 15m completed candles",
        "15m confirmation uses the latest completed 15-minute candle.",
    ]))

def send_success_alert(scanned, matches, fresh, failed, duration):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    return tg("\n".join([
        "📊 NSE+BSE MSB NON-REPAINT INTRADAY SCAN","",
        f"🟢 Status: {status}" if failed == 0 else f"🟡 Status: {status}",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}","",
        f"📈 Stocks scanned: {scanned:,}",
        f"🎯 MSB matches: {matches:,}",
        f"🆕 New alerts: {fresh:,}",
        f"⚠️ Failed/skipped: {failed:,}",
        f"⏱️ Duration: {fmt_duration(duration)}","",
        "Strategy:","* AS-OF current timestamp; no future HTF candle values",
        *STRATEGY_LINES,"",
        "💾 Results: scan_results.csv",
        "📚 Completed HTF history: market_snapshot.csv",
        "💰 Signal price = latest/current 1m CMP",
        "",
        "🔄 Universe: NSE + BSE Equities",
        "🤖 Data: yfinance",
    ]))

def send_failure_alert(reason, scanned, total, duration):
    now = datetime.now(IST)
    return tg("\n".join([
        "🔴 NSE+BSE MSB NON-REPAINT INTRADAY SCAN","",
        "🔴 Scan Status: FAILED",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}","",
        f"Reason: {reason}",
        f"Stocks scanned: {scanned:,} / {total:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
    ]))

def main():
    started = time.monotonic()
    now = datetime.now(IST)
    if os.getenv("GITHUB_EVENT_NAME", "schedule") == "schedule" and now.weekday() >= 5:
        print("Scheduled scan on weekend:", now)
        return

    try:
        u = pd.read_csv("symbols.csv")
        snap = pd.read_csv(SNAPSHOT)
    except Exception as e:
        raise RuntimeError(f"Required universe/snapshot file missing: {e}. Run the daily universe refresh first.")

    required_cols = {"YF_TICKER","ticker","weekly_history","monthly_history"}
    if not required_cols.issubset(u.columns | snap.columns):
        missing = sorted(required_cols - set(snap.columns))
        raise RuntimeError(f"market_snapshot.csv is missing AS-OF history columns: {missing}. Run universe_refresh once after this update.")

    ts = snap["ticker"].dropna().astype(str).str.strip().loc[lambda x: x.ne("") & x.ne("nan")].unique().tolist()
    total = len(ts)
    print("Scanning", total, "snapshot-covered eligible stocks")
    if total == 0:
        raise RuntimeError("Market snapshot is empty.")

    prices, price_failed = get_live_bars(ts)
    data_pull_duration = time.monotonic() - started
    send_data_pull_alert(total, len(prices), price_failed, data_pull_duration)
    snap = snap.drop_duplicates("ticker").set_index("ticker")
    matches, failed, scanned = [], price_failed, 0

    for ticker in ts:
        scanned += 1
        bar = prices.get(ticker)
        if bar is None or ticker not in snap.index:
            continue
        try:
            setup = asof_setup(snap.loc[ticker], bar)
            if setup:
                matches.append((ticker, setup))
        except Exception as e:
            failed += 1
            print(ticker, "signal skip:", e)

    stored = save_scan_results(matches, u, now)

    state = load()
    fresh = []
    for ticker, setup in matches:
        key = f"{ticker}|{setup['signal_date']}"
        if not state.get(key):
            state[key] = True
            fresh.append((ticker, setup))
    save(state)

    duration = time.monotonic() - started
    save_run_history(now, scanned, len(prices), len(matches), len(fresh), failed, stored, duration)
    print("Universe:", total, "Scanned:", scanned, "Live bars:", len(prices),
          "Matches:", len(matches), "New alerts:", len(fresh),
          "CSV records:", stored, "Failed:", failed)
    send_success_alert(scanned, len(matches), len(fresh), failed, duration)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        elapsed = time.monotonic() - globals().get("started", time.monotonic())
        try:
            send_failure_alert(str(e), 0, 0, elapsed)
        except Exception as alert_error:
            print("Failure alert could not be sent:", alert_error)
        raise
