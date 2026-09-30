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
STRATEGY = "MSB_BASE_V3_ASOF_NON_REPAINT"

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

