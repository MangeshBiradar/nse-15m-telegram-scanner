import os,json,time,math,requests,pandas as pd,yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from indicators import supertrend,rsi,bb_upper,sma

STATE="state.json"
RESULTS="scan_results.csv"
BATCH=30
IST=ZoneInfo("Asia/Kolkata")

STRATEGY_LINES = [
    "• Weekly Close >= Weekly Supertrend(7,3)",
    "• Weekly RSI(14) > 60",
    "• Weekly Close >= Weekly Upper BB(20,2)",
    "• Previous Daily Close < Previous Daily SMA20",
    "• Current Daily Close > Current Daily SMA20",
]

RESULT_COLUMNS = [
    "scan_date","scan_time_ist","run_type",
    "exchange","symbol","ticker","name",
    "signal_price","price_source",
    "previous_close","previous_sma20","daily_sma20",
    "weekly_close","weekly_rsi","weekly_supertrend","weekly_upper_bb",
    "monthly_rsi","strategy"
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

def flatten(d):
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] if isinstance(x, tuple) else x for x in d.columns]
    return d

def clean(d):
    if d is None or d.empty:
        return None
    d = flatten(d.copy())
    needed = ["Open","High","Low","Close"]
    if not all(x in d.columns for x in needed):
        return None
    d.index = pd.to_datetime(d.index)
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert(IST).tz_localize(None)
    return d.dropna(subset=needed).sort_index()

def timeframe_ohlcv(d, rule):
    x = d.resample(rule).agg({
        "Open":"first","High":"max","Low":"min",
        "Close":"last","Volume":"sum"
    })
    return x.dropna(subset=["Open","High","Low","Close"])

def higher_timeframes(d):
    return timeframe_ohlcv(d, "W-FRI"), timeframe_ohlcv(d, "ME")

def daily_setup(d):
    d = clean(d)
    if d is None or len(d) < 120:
        return None

    w, m = higher_timeframes(d)
    if len(w) < 60 or len(m) < 20:
        return None

    # Keep the current HTF candle, matching the live scanner's behavior.
    w_st = supertrend(w, 7, 3)
    w_rsi = rsi(w["Close"], 14)
    w_bb = bb_upper(w["Close"], 20, 2)
    m_rsi = rsi(m["Close"], 14)
    d_sma = sma(d["Close"], 20)

    vals = [
        w["Close"].iloc[-1], w_st.iloc[-1], w_rsi.iloc[-1], w_bb.iloc[-1],
        d["Close"].iloc[-2], d_sma.iloc[-2],
        d["Close"].iloc[-1], d_sma.iloc[-1],
    ]
    if not all(math.isfinite(float(x)) for x in vals):
        return None

    if not (
        vals[0] >= vals[1]
        and vals[2] > 60
        and vals[0] >= vals[3]
        and vals[4] < vals[5]
        and vals[6] > vals[7]
    ):
        return None

    return {
        "signal_date": d.index[-1].date().isoformat(),
        "signal_price": float(d["Close"].iloc[-1]),
        "previous_close": float(d["Close"].iloc[-2]),
        "previous_sma20": float(d_sma.iloc[-2]),
        "daily_sma20": float(d_sma.iloc[-1]),
        "weekly_close": float(w["Close"].iloc[-1]),
        "weekly_rsi": float(w_rsi.iloc[-1]),
        "weekly_supertrend": float(w_st.iloc[-1]),
        "weekly_upper_bb": float(w_bb.iloc[-1]),
        "monthly_rsi": float(m_rsi.iloc[-1]) if math.isfinite(float(m_rsi.iloc[-1])) else None,
    }

def run_type():
    explicit = os.getenv("RUN_TYPE")
    if explicit:
        return explicit
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")
    return "Manual" if event == "workflow_dispatch" else "Scheduled"

def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"

def save_scan_results(matches, universe, now):
    if not matches:
        print("No scan matches to record.")
        return 0

    meta = universe.copy()
    if "YF_TICKER" not in meta.columns:
        meta["YF_TICKER"] = ""

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
            "price_source": "Previous completed trading-day close",
            "previous_close": setup["previous_close"],
            "previous_sma20": setup["previous_sma20"],
            "daily_sma20": setup["daily_sma20"],
            "weekly_close": setup["weekly_close"],
            "weekly_rsi": setup["weekly_rsi"],
            "weekly_supertrend": setup["weekly_supertrend"],
            "weekly_upper_bb": setup["weekly_upper_bb"],
            "monthly_rsi": setup["monthly_rsi"],
            "strategy": "MSB_BASE_V1",
        })

    new = pd.DataFrame(rows, columns=RESULT_COLUMNS)

    if os.path.exists(RESULTS) and os.path.getsize(RESULTS) > 0:
        old = pd.read_csv(RESULTS)
    else:
        old = pd.DataFrame(columns=RESULT_COLUMNS)

    old = old.reindex(columns=RESULT_COLUMNS)
    combined = pd.concat([old, new], ignore_index=True)

    # Prevent duplicate records if a manual run is repeated on the same signal date.
    combined = combined.drop_duplicates(
        subset=["scan_date","ticker","strategy"], keep="first"
    ).sort_values(["scan_date","ticker"]).reset_index(drop=True)

    combined.to_csv(RESULTS, index=False)
    added = len(combined) - len(old.drop_duplicates(
        subset=["scan_date","ticker","strategy"]
    ))
    print(f"Stored {max(0, added)} new scan results in {RESULTS}; total rows={len(combined)}")
    return max(0, added)

def send_success_alert(scanned, matches, fresh, failed, duration):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    lines = [
        "📊 NSE+BSE MSB DAILY SCAN",
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
        "💰 Signal price = previous completed trading-day close",
        "",
        "🔄 Universe: NSE + BSE Equities",
        "🤖 Data: yfinance",
    ]
    return tg("\n".join(lines))

def send_failure_alert(reason, scanned, total, duration):
    now = datetime.now(IST)
    return tg("\n".join([
        "🔴 NSE+BSE MSB DAILY SCAN",
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

    # Scheduled scan is intended for the pre-market weekday slot.
    # Do not reject a delayed GitHub Actions run: scheduled workflows can be delayed.
    # The scan always uses the latest completed daily candle; there is no 15m condition.
    if event == "schedule" and now.weekday() >= 5:
        print("Scheduled scan on weekend:", now)
        return

    try:
        u = pd.read_csv("symbols.csv")
    except Exception as e:
        from universe import refresh
        print("Universe file unavailable; attempting refresh:", e)
        u = refresh("symbols.csv")

    if "YF_TICKER" not in u.columns:
        from universe import refresh
        u = refresh("symbols.csv")

    ts = (
        u["YF_TICKER"].dropna().astype(str).str.strip()
        .loc[lambda x: x.ne("") & x.ne("nan")]
        .unique().tolist()
    )

    if not ts:
        from universe import refresh
        u = refresh("symbols.csv")
        ts = (
            u["YF_TICKER"].dropna().astype(str).str.strip()
            .loc[lambda x: x.ne("") & x.ne("nan")]
            .unique().tolist()
        )

    total = len(ts)
    print("Scanning", total, "eligible stocks")
    if total == 0:
        raise RuntimeError("Universe is empty after automatic refresh.")

    failed = 0
    scanned = 0
    matches = []

    for i in range(0, total, BATCH):
        batch = ts[i:i+BATCH]
        try:
            data = yf.download(
                batch, period="2y", interval="1d",
                group_by="ticker", auto_adjust=False,
                progress=False, threads=True,
            )
        except Exception as e:
            failed += len(batch)
            print("daily batch failed:", e)
            continue

        for t in batch:
            scanned += 1
            try:
                d = data if len(batch) == 1 else (
                    data[t] if t in data.columns.get_level_values(0) else None
                )
                setup = daily_setup(d)
                if setup:
                    matches.append((t, setup))
            except Exception as e:
                failed += 1
                print(t, "daily skip", e)
        time.sleep(0.25)

    # Persist every signal with its entry/reference price and indicator values.
    stored = save_scan_results(matches, u, now)

    # Keep one alert per ticker per signal date.
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
