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
SNAPSHOT = "market_snapshot.csv"
BATCH = 40
IST = ZoneInfo("Asia/Kolkata")

STRATEGY_LINES = [
    "• Weekly Close >= Weekly Supertrend(7,3)",
    "• Weekly RSI(14) > 60",
    "• Weekly Close >= Weekly Upper BB(20,2)",
    "• Previous Daily Close < Previous Daily SMA20",
    "• Current CMP > Current Daily SMA20",
]

RESULT_COLUMNS = [
    "scan_date","scan_time_ist","run_type",
    "exchange","symbol","ticker","name",
    "signal_price","price_source",
    "previous_date","previous_close","previous_sma20","daily_sma20",
    "weekly_date","weekly_close","weekly_rsi","weekly_supertrend","weekly_upper_bb",
    "monthly_date","monthly_rsi","strategy"
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
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")
    return "Manual" if event == "workflow_dispatch" else "Scheduled"

def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"

def get_live_prices(tickers):
    """Fetch only latest intraday CMP; no daily/weekly history is downloaded."""
    prices = {}
    failed = 0

    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i+BATCH]
        try:
            data = yf.download(
                batch,
                period="1d",
                interval="1m",
                group_by="ticker",
                auto_adjust=False,
                prepost=False,
                progress=False,
                threads=True,
                timeout=20,
            )
        except Exception as e:
            failed += len(batch)
            print("CMP batch failed:", e)
            continue

        for ticker in batch:
            try:
                d = data if len(batch) == 1 else (
                    data[ticker] if ticker in data.columns.get_level_values(0) else None
                )
                if d is None or d.empty or "Close" not in d.columns:
                    failed += 1
                    continue
                close = pd.to_numeric(d["Close"], errors="coerce").dropna()
                if close.empty:
                    failed += 1
                    continue
                prices[ticker] = float(close.iloc[-1])
            except Exception as e:
                failed += 1
                print(ticker, "CMP skip:", e)

        time.sleep(0.15)

    return prices, failed

def live_setup(row, cmp):
    required = [
        "previous_close", "previous_sma20",
        "weekly_close", "weekly_rsi", "weekly_supertrend", "weekly_upper_bb",
    ]
    if any(pd.isna(row.get(x)) for x in required) or not math.isfinite(float(cmp)):
        return None

    previous_close = float(row["previous_close"])
    previous_sma20 = float(row["previous_sma20"])
    weekly_close = float(row["weekly_close"])
    weekly_rsi = float(row["weekly_rsi"])
    weekly_supertrend = float(row["weekly_supertrend"])
    weekly_upper_bb = float(row["weekly_upper_bb"])

    # Reconstruct today's live SMA20 from yesterday's completed SMA20 and close.
    current_sma20 = previous_sma20 + (float(cmp) - previous_close) / 20.0

    if not (
        weekly_close >= weekly_supertrend
        and weekly_rsi > 60
        and weekly_close >= weekly_upper_bb
        and previous_close < previous_sma20
        and float(cmp) > current_sma20
    ):
        return None

    return {
        "signal_date": datetime.now(IST).date().isoformat(),
        "signal_price": float(cmp),
        "previous_date": str(row.get("previous_date", "")),
        "previous_close": previous_close,
        "previous_sma20": previous_sma20,
        "daily_sma20": current_sma20,
        "weekly_date": str(row.get("weekly_date", "")),
        "weekly_close": weekly_close,
        "weekly_rsi": weekly_rsi,
        "weekly_supertrend": weekly_supertrend,
        "weekly_upper_bb": weekly_upper_bb,
        "monthly_date": str(row.get("monthly_date", "")),
        "monthly_rsi": float(row["monthly_rsi"]) if pd.notna(row.get("monthly_rsi")) else None,
    }

def save_scan_results(matches, universe, now):
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
            "price_source": "Latest/current 1m CMP from yfinance",
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
            "strategy": "MSB_BASE_V2_SNAPSHOT_CMP",
        })

    new = pd.DataFrame(rows, columns=RESULT_COLUMNS)

    if os.path.exists(RESULTS) and os.path.getsize(RESULTS) > 0:
        old = pd.read_csv(RESULTS)
    else:
        old = pd.DataFrame(columns=RESULT_COLUMNS)

    old = old.reindex(columns=RESULT_COLUMNS)
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["scan_date","ticker","strategy"], keep="first"
    ).sort_values(["scan_date","ticker"]).reset_index(drop=True)

    combined.to_csv(RESULTS, index=False)
    old_unique = old.drop_duplicates(subset=["scan_date","ticker","strategy"])
    added = len(combined) - len(old_unique)
    print(f"Stored {max(0, added)} new scan results in {RESULTS}; total rows={len(combined)}")
    return max(0, added)

def send_success_alert(scanned, matches, fresh, failed, duration):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    lines = [
        "📊 NSE+BSE MSB INTRADAY SCAN",
        "",
        f"🟢 Status: {status}" if failed == 0 else f"🟡 Status: {status}",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"📈 Stocks scanned: {scanned:,}",
        f"🎯 MSB matches: {matches:,}",
        f"🆕 New CSV records: {fresh:,}",
        f"⚠️ Failed/skipped: {failed:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
        "",
        "Strategy:",
        *STRATEGY_LINES,
        "",
        "💾 Results: scan_results.csv",
        "📚 Weekly/previous-day indicators: market_snapshot.csv",
        "💰 Signal price = latest/current 1m CMP at scan time",
        "",
        "🔄 Universe: NSE + BSE Equities",
        "🤖 Data: yfinance",
    ]
    return tg("\n".join(lines))

def send_failure_alert(reason, scanned, total, duration):
    now = datetime.now(IST)
    return tg("\n".join([
        "🔴 NSE+BSE MSB INTRADAY SCAN",
        "",
        "🔴 Scan Status: FAILED",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"Reason: {reason}",
        f"Stocks scanned: {scanned:,} / {total:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
    ]))

def main():
    started = time.monotonic()
    now = datetime.now(IST)
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")

    if event == "schedule" and now.weekday() >= 5:
        print("Scheduled scan on weekend:", now)
        return

    try:
        u = pd.read_csv("symbols.csv")
        snap = pd.read_csv(SNAPSHOT)
    except Exception as e:
        raise RuntimeError(
            f"Required universe/snapshot file missing: {e}. "
            "Run the daily universe refresh first."
        )

    if "YF_TICKER" not in u.columns or "ticker" not in snap.columns:
        raise RuntimeError("symbols.csv or market_snapshot.csv has an invalid schema.")

    # The 15-minute scanner uses frozen historical/HTF values from the morning
    # snapshot and downloads only the latest CMP. It never downloads daily history.
    ts = (
        snap["ticker"].dropna().astype(str).str.strip()
        .loc[lambda x: x.ne("") & x.ne("nan")]
        .unique().tolist()
    )

    total = len(ts)
    print("Scanning", total, "snapshot-covered eligible stocks")
    if total == 0:
        raise RuntimeError("Market snapshot is empty.")

    prices, price_failed = get_live_prices(ts)

    matches = []
    failed = price_failed
    scanned = 0

    snap = snap.drop_duplicates("ticker").set_index("ticker")
    for ticker in ts:
        scanned += 1
        cmp = prices.get(ticker)
        if cmp is None:
            continue
        try:
            setup = live_setup(snap.loc[ticker], cmp)
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
    print(
        "Universe:", total,
        "Scanned:", scanned,
        "CMP available:", len(prices),
        "Matches:", len(matches),
        "New alerts:", len(fresh),
        "CSV records:", stored,
        "Failed:", failed,
    )

    send_success_alert(scanned, len(matches), stored, failed, duration)

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
