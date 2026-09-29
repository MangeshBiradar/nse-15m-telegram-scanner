import os,json,time,math,requests,pandas as pd,yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from indicators import supertrend,rsi,bb_upper,sma

STATE="state.json"
BATCH=30
IST=ZoneInfo("Asia/Kolkata")

STRATEGY_LINES = [
    "• Weekly Close >= Weekly Supertrend(7,3)",
    "• Weekly RSI(14) > 60",
    "• Monthly RSI(14) > 60",
    "• Weekly Close >= Weekly Upper BB(20,2)",
    "• Previous Daily Close < Previous Daily SMA20",
    "• Current Daily Close > Current Daily SMA20",
    "• 15m Close > 15m SMA20",
    "• 15m Close > 15m Supertrend(7,3)",
    "• 15m RSI(14) > 55",
    "• 15m momentum: Close > previous 15m High",
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
    w = timeframe_ohlcv(d, "W-FRI")
    m = timeframe_ohlcv(d, "ME")
    return w, m

def daily_setup(d):
    d = clean(d)
    if d is None or len(d) < 120:
        return None

    w, m = higher_timeframes(d)
    if len(w) < 60 or len(m) < 20:
        return None

    # IMPORTANT: keep the current weekly/monthly candle.
    # Chartink's live scanner evaluates the current incomplete HTF candle.
    w_st = supertrend(w, 7, 3)
    w_rsi = rsi(w["Close"], 14)
    w_bb = bb_upper(w["Close"], 20, 2)
    m_rsi = rsi(m["Close"], 14)

    vals = [
        w["Close"].iloc[-1], w_st.iloc[-1], w_rsi.iloc[-1],
        w_bb.iloc[-1], m_rsi.iloc[-1],
        d["Close"].iloc[-2], sma(d["Close"],20).iloc[-2],
        d["Close"].iloc[-1], sma(d["Close"],20).iloc[-1],
    ]
    if not all(math.isfinite(float(x)) for x in vals):
        return None

    # Exact MSB base logic:
    # Weekly Close >= Weekly ST(7,3)
    # Weekly RSI > 60
    # Monthly RSI > 60
    # Weekly Close >= Weekly Upper BB(20,2)
    # Previous daily Close < Previous daily SMA20
    # Current daily Close > Current daily SMA20
    if not (
        vals[0] >= vals[1]
        and vals[2] > 60
        and vals[4] > 60
        and vals[0] >= vals[3]
        and vals[5] < vals[6]
        and vals[7] > vals[8]
    ):
        return None

    return {
        "week": str(w.index[-1].date()),
        "month": str(m.index[-1].date()),
        "close": float(d["Close"].iloc[-1]),
        "weekly_rsi": float(w_rsi.iloc[-1]),
        "weekly_st": float(w_st.iloc[-1]),
        "weekly_bb": float(w_bb.iloc[-1]),
        "monthly_rsi": float(m_rsi.iloc[-1]),
        "prev_close": float(d["Close"].iloc[-2]),
        "prev_sma20": float(sma(d["Close"],20).iloc[-2]),
        "daily_sma20": float(sma(d["Close"],20).iloc[-1]),
    }

def intraday_trigger(d15, base):
    d15 = clean(d15)
    if d15 is None or len(d15) < 60:
        return None

    st = supertrend(d15, 7, 3)
    rr = rsi(d15["Close"], 14)
    sm = sma(d15["Close"], 20)

    i = len(d15) - 1
    vals = [d15["Close"].iloc[i], d15["Open"].iloc[i], st.iloc[i], rr.iloc[i], sm.iloc[i]]
    if not all(math.isfinite(float(x)) for x in vals):
        return None

    # For the first 15m bar of the day there is no same-day previous 15m bar.
    # Use bullish/opening confirmation. From the second bar onward require
    # close > previous 15m high for the momentum trigger.
    ts = d15.index[i]
    first_bar = ts.time() <= pd.Timestamp("09:29:59").time()

    common = (
        vals[0] > vals[2]
        and vals[0] > vals[4]
        and vals[3] > 55
    )
    if first_bar:
        momentum = vals[0] > vals[1]
    else:
        momentum = vals[0] > float(d15["High"].iloc[i-1])

    if not (common and momentum):
        return None

    return {
        **base,
        "bar_time": str(ts),
        "intraday_close": float(d15["Close"].iloc[i]),
        "intraday_open": float(d15["Open"].iloc[i]),
        "intraday_st": float(st.iloc[i]),
        "intraday_rsi": float(rr.iloc[i]),
        "intraday_sma20": float(sm.iloc[i]),
        "trigger": "OPENING_BULLISH" if first_bar else "15M_BREAKOUT",
    }

def run_type():
    explicit = os.getenv("RUN_TYPE")
    if explicit:
        return explicit
    event = os.getenv("GITHUB_EVENT_NAME", "scheduled")
    return "Manual" if event == "workflow_dispatch" else "Scheduled"

def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"

def send_success_alert(scanned, base_matches, condition_matches, fresh_signals, failed, duration, matches):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    lines = [
        "📊 NSE+BSE MSB SWING SCAN",
        "",
        f"🟢 Status: {status}" if failed == 0 else f"🟡 Status: {status}",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"📈 Stocks scanned: {scanned:,}",
        f"🧩 MSB base matches: {base_matches:,}",
        f"🎯 15m condition matches: {condition_matches:,}",
        f"🆕 New alerts: {fresh_signals:,}",
        f"⚠️ Failed/skipped: {failed:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
        "",
        "Strategy:",
        *STRATEGY_LINES,
        "",
        "🔄 Universe: NSE + BSE Equities",
        "🤖 Data: yfinance",
    ]

    if matches:
        lines.extend(["", "📌 MATCH DETAILS"])
        for i, s in enumerate(matches, 1):
            lines.extend([
                "",
                f"{i}. {s['ticker']}",
                f"   Trigger: {s['trigger']} @ {s['bar_time']}",
                f"   Price: {s['intraday_close']:.2f}",
                f"   15m RSI: {s['intraday_rsi']:.2f}",
                f"   15m SMA20: {s['intraday_sma20']:.2f}",
                f"   15m Supertrend: {s['intraday_st']:.2f}",
                f"   Weekly RSI: {s['weekly_rsi']:.2f}",
                f"   Monthly RSI: {s['monthly_rsi']:.2f}",
                f"   Weekly BB: {s['weekly_bb']:.2f}",
            ])
    return tg("\n".join(lines))

def send_failure_alert(reason, scanned, total, duration):
    now = datetime.now(IST)
    return tg("\n".join([
        "🔴 NSE+BSE MSB SWING SCAN",
        "",
        "🔴 Scan Status: FAILED",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"Reason: {reason}",
        f"Stocks scanned: {scanned:,} / {total:,}",
        f"⏱️ Duration: {fmt_duration(duration)}",
        "",
        "🤖 Data: yfinance",
    ]))

def main():
    started = time.monotonic()

    # Scheduled runs are restricted to NSE market hours.
    # Manual/workflow_dispatch runs are allowed at any time.
    now = datetime.now(IST)
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")
    if event == "schedule" and not (
        now.weekday() < 5
        and pd.Timestamp("09:15").time() <= now.time() <= pd.Timestamp("15:35").time()
    ):
        print("Scheduled run outside NSE market window:", now)
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

    ts = (u["YF_TICKER"].dropna().astype(str).str.strip()
          .loc[lambda x: x.ne("") & x.ne("nan")].unique().tolist())
    if not ts:
        from universe import refresh
        u = refresh("symbols.csv")
        ts = (u["YF_TICKER"].dropna().astype(str).str.strip()
              .loc[lambda x: x.ne("") & x.ne("nan")].unique().tolist())

    state = load()
    total = len(ts)
    print("Scanning", total, "eligible stocks")
    if total == 0:
        raise RuntimeError("Universe is empty after automatic refresh.")

    failed = 0
    scanned = 0
    base_matches = []
    condition_matches = []
    fresh_signals = []

    # Stage 1: daily + weekly + monthly MSB filter.
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
                base = daily_setup(d)
                if base:
                    base_matches.append((t, base))
            except Exception as e:
                failed += 1
                print(t, "daily skip", e)
        time.sleep(0.25)

    # Stage 2: only fetch expensive 15m data for MSB base matches.
    candidates = len(base_matches)
    print("MSB base candidates:", candidates)

    for i in range(0, candidates, BATCH):
        batch = [x[0] for x in base_matches[i:i+BATCH]]
        bases = dict(base_matches[i:i+BATCH])
        try:
            data = yf.download(
                batch, period="60d", interval="15m",
                group_by="ticker", auto_adjust=False,
                progress=False, threads=True,
            )
        except Exception as e:
            failed += len(batch)
            print("15m batch failed:", e)
            continue

        for t in batch:
            try:
                d15 = data if len(batch) == 1 else (
                    data[t] if t in data.columns.get_level_values(0) else None
                )
                s = intraday_trigger(d15, bases[t])
                if not s:
                    continue
                s["ticker"] = t
                condition_matches.append(s)

                # One new Telegram alert per ticker per trading day.
                key = t + "|" + now.strftime("%Y-%m-%d")
                if not state.get(key):
                    state[key] = True
                    fresh_signals.append(s)
            except Exception as e:
                failed += 1
                print(t, "15m skip", e)
        time.sleep(0.25)

    save(state)
    duration = time.monotonic() - started

    print(
        "Universe:", total,
        "Scanned:", scanned,
        "MSB base:", len(base_matches),
        "15m matches:", len(condition_matches),
        "Fresh:", len(fresh_signals),
        "Failed:", failed,
    )

    send_success_alert(
        scanned, len(base_matches), len(condition_matches),
        len(fresh_signals), failed, duration, fresh_signals
    )

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
