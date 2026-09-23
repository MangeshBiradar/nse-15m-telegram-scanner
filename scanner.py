import os,json,time,math,requests,pandas as pd,yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from indicators import supertrend,rsi,bb_upper,sma

STATE="state.json"
BATCH=30
IST=ZoneInfo("Asia/Kolkata")

STRATEGY_LINES = [
    "• Weekly Close ≥ Supertrend",
    "• Weekly Close ≥ Upper BB",
    "• Weekly RSI ≥ 60",
    "• Previous Day Close < 20 SMA",
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
    """Send Telegram alert without ever crashing the scanner."""
    t, c = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not t or not c:
        print("Telegram: NOT CONFIGURED (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing)")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{t}/sendMessage",
            json={"chat_id": c, "text": msg},
            timeout=20,
        )
        if r.ok:
            print("Telegram: sent")
            return True
        print(f"Telegram: FAILED HTTP {r.status_code}: {r.text[:500]}")
        if r.status_code == 403:
            print("Telegram hint: verify bot token, chat ID, and that the bot can send messages to the target chat.")
        return False
    except Exception as e:
        print(f"Telegram: FAILED: {e}")
        return False


def flatten(d):
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] if isinstance(x, tuple) else x for x in d.columns]
    return d


def weekly(d):
    d = d.copy()
    d.index = pd.to_datetime(d.index)
    w = d.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum"
    }).dropna(subset=["Close"])
    now = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize()
    if len(w) and w.index[-1].normalize() >= now:
        w = w.iloc[:-1]
    return w


def signal(t, d):
    if d is None or d.empty:
        return None
    d = flatten(d).dropna(subset=["Open", "High", "Low", "Close"])
    if len(d) < 120:
        return None
    w = weekly(d)
    if len(w) < 60:
        return None

    st = supertrend(w, 10, 3)
    rr = rsi(w.Close, 14)
    bb = bb_upper(w.Close, 20, 2)
    wc, ws, wr, wb = map(float, [w.Close.iloc[-1], st.iloc[-1], rr.iloc[-1], bb.iloc[-1]])
    pc = float(d.Close.iloc[-2])
    ps = float(sma(d.Close, 20).iloc[-2])

    values = [wc, ws, wr, wb, pc, ps]
    if not all(math.isfinite(x) for x in values):
        return None

    if wc >= ws and wc >= wb and wr >= 60 and pc < ps:
        return {
            "ticker": t,
            "week": str(w.index[-1].date()),
            "close": wc,
            "st": ws,
            "bb": wb,
            "rsi": wr,
            "prev": pc,
            "sma20": ps,
        }
    return None


def run_type():
    explicit = os.getenv("RUN_TYPE")
    if explicit:
        return explicit
    event = os.getenv("GITHUB_EVENT_NAME", "scheduled")
    return "Manual" if event == "workflow_dispatch" else "Scheduled"


def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"


def send_success_alert(scanned, condition_matches, fresh_signals, failed, duration, matches):
    now = datetime.now(IST)
    status = "SUCCESS" if failed == 0 else "PARTIAL"
    lines = [
        "📊 NSE+BSE SIGNAL SCAN",
        "",
        f"🟢 Status: {status}" if failed == 0 else f"🟡 Status: {status}",
        f"🕒 Time: {now.strftime('%d-%b-%Y %I:%M %p')} IST",
        f"📡 Run: {run_type()}",
        "",
        f"📈 Stocks scanned: {scanned:,}",
        f"🎯 Matches: {condition_matches:,}",
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
                f"   Price/LTP: {s['close']:.2f}",
                f"   Weekly RSI: {s['rsi']:.2f}",
                f"   Weekly Supertrend: {s['st']:.2f}",
                f"   Weekly Upper BB: {s['bb']:.2f}",
                f"   Previous Close: {s['prev']:.2f}",
                f"   Previous SMA20: {s['sma20']:.2f}",
            ])

    return tg("\n".join(lines))


def send_failure_alert(reason, scanned, total, duration):
    now = datetime.now(IST)
    msg = "\n".join([
        "🔴 NSE+BSE SIGNAL SCAN",
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
    ])
    return tg(msg)


def main():
    started = time.monotonic()
    u = pd.read_csv("symbols.csv")
    state = load()
    ts = u.YF_TICKER.dropna().astype(str).unique().tolist()
    total = len(ts)
    print("Scanning", total, "eligible stocks")
    if total == 0:
        raise RuntimeError("Universe is empty. Run refresh_universe.yml or restore symbols.csv.")

    failed = 0
    scanned = 0
    condition_matches = []
    fresh_signals = []

    for i in range(0, total, BATCH):
        batch = ts[i:i + BATCH]
        try:
            data = yf.download(
                batch,
                period="2y",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as e:
            failed += len(batch)
            print("batch failed", e)
            continue

        for t in batch:
            scanned += 1
            try:
                d = data if len(batch) == 1 else (
                    data[t] if t in data.columns.get_level_values(0) else None
                )
                s = signal(t, d)
                if not s:
                    continue

                condition_matches.append(s)
                key = t + "|" + s["week"]
                if state.get(key):
                    continue

                state[key] = True
                fresh_signals.append(s)
            except Exception as e:
                failed += 1
                print(t, "skip", e)

        time.sleep(0.5)

    save(state)
    duration = time.monotonic() - started

    print(
        "Universe:", total,
        "Scanned:", scanned,
        "Failed/skipped:", failed,
        "Matches:", len(condition_matches),
        "Fresh signals:", len(fresh_signals),
    )

    send_success_alert(
        scanned=scanned,
        condition_matches=len(condition_matches),
        fresh_signals=len(fresh_signals),
        failed=failed,
        duration=duration,
        matches=condition_matches,
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
