import os, time, math
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import requests

IST = ZoneInfo("Asia/Kolkata")
OUT = "scan_results.csv"
BATCH_SLEEP = 0.12

def rma(s, n):
    s = pd.Series(s, dtype=float)
    out = pd.Series(float("nan"), index=s.index)
    if len(s) < n: return out
    out.iloc[n-1] = s.iloc[:n].mean()
    for i in range(n, len(s)):
        out.iloc[i] = out.iloc[i-1] + (s.iloc[i] - out.iloc[i-1]) / n
    return out

def rsi(close, n=14):
    d = pd.Series(close, dtype=float).diff()
    gain, loss = d.clip(lower=0), -d.clip(upper=0)
    ag, al = rma(gain, n), rma(loss, n)
    rs = ag / al.replace(0, float("nan"))
    result = 100 - 100 / (1 + rs)
    return result.mask((al == 0) & (ag > 0), 100)

def atr(df, n=7):
    prev = df.Close.shift(1)
    tr = pd.concat([df.High-df.Low, (df.High-prev).abs(), (df.Low-prev).abs()], axis=1).max(axis=1)
    return rma(tr, n)

def supertrend(df, n=7, mult=3):
    a = atr(df, n)
    mid = (df.High + df.Low) / 2
    bu, bl = mid + mult*a, mid - mult*a
    fu, fl, st = [pd.Series(float("nan"), index=df.index) for _ in range(3)]
    for i in range(len(df)):
        if pd.isna(a.iloc[i]): continue
        if i == 0 or pd.isna(fu.iloc[i-1]):
            fu.iloc[i], fl.iloc[i], st.iloc[i] = bu.iloc[i], bl.iloc[i], bu.iloc[i]
            continue
        pc = df.Close.iloc[i-1]
        fu.iloc[i] = bu.iloc[i] if bu.iloc[i] < fu.iloc[i-1] or pc > fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i] = bl.iloc[i] if bl.iloc[i] > fl.iloc[i-1] or pc < fl.iloc[i-1] else fl.iloc[i-1]
        st.iloc[i] = (fl.iloc[i] if st.iloc[i-1] == fu.iloc[i-1] and df.Close.iloc[i] > fu.iloc[i]
                      else fu.iloc[i] if st.iloc[i-1] == fu.iloc[i-1]
                      else fu.iloc[i] if df.Close.iloc[i] < fl.iloc[i] else fl.iloc[i])
    return st

def bb_upper(close, n=20, mult=2):
    close = pd.Series(close, dtype=float)
    return close.rolling(n).mean() + mult*close.rolling(n).std(ddof=0)

def resample_ohlc(df, rule):
    return df.resample(rule).agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna(subset=["Close"])

def tg(message):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram secrets missing; alert skipped.")
        return
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id":chat,"text":message}, timeout=20)
        print("Telegram:", resp.status_code, resp.text[:200])
    except Exception as e: print("Telegram alert error:", e)

def main():
    started = time.monotonic()
    now = datetime.now(IST)
    symbols = pd.read_csv("symbols.csv")
    if "YF_TICKER" not in symbols.columns:
        raise RuntimeError("symbols.csv must contain YF_TICKER")
    symbols = symbols.dropna(subset=["YF_TICKER"]).drop_duplicates("YF_TICKER")
    rows, failed = [], []
    tickers = symbols["YF_TICKER"].astype(str).tolist()
    print(f"On-demand scan started: {now:%Y-%m-%d %H:%M:%S} IST; universe={len(tickers)}")
    for i, ticker in enumerate(tickers, 1):
        try:
            daily = yf.download(ticker, period="18mo", interval="1d", auto_adjust=False, progress=False, threads=False)
            intraday = yf.download(ticker, period="5d", interval="15m", auto_adjust=False, prepost=False, progress=False, threads=False)
            if daily is None or daily.empty or intraday is None or intraday.empty:
                raise ValueError("No daily or 15-minute data")
            for d in (daily, intraday):
                if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
                d.index = pd.to_datetime(d.index)
                if d.index.tz is not None: d.index = d.index.tz_convert(IST).tz_localize(None)
                d.sort_index(inplace=True)
            daily = daily[~daily.index.duplicated(keep="last")]
            intraday = intraday[~intraday.index.duplicated(keep="last")]
            daily = daily.dropna(subset=["Close"])
            intraday = intraday.dropna(subset=["Close"])
            if len(daily) < 80: raise ValueError("Insufficient daily history")
            # Most recent 15-minute candle is used only after it has completed.
            now_naive = now.replace(tzinfo=None)
            cutoff = now_naive.replace(minute=(now_naive.minute//15)*15, second=0, microsecond=0)
            complete = intraday[intraday.index + pd.Timedelta(minutes=15) <= cutoff]
            if complete.empty: raise ValueError("No completed 15-minute candle")
            bar = complete.iloc[-1]
            bar_time = complete.index[-1]
            latest_price = float(bar["Close"])
            intraday_today = complete[complete.index.date == now.date()]
            if intraday_today.empty:
                raise ValueError("No completed 15-minute candle for current date")
            day_open = float(intraday_today["Open"].iloc[0]) if "Open" in intraday_today.columns else latest_price
            day_high = float(intraday_today["High"].max()) if "High" in intraday_today.columns else latest_price
            day_low = float(intraday_today["Low"].min()) if "Low" in intraday_today.columns else latest_price
            day_volume = float(intraday_today["Volume"].fillna(0).sum()) if "Volume" in intraday_today.columns else 0.0
            # Keep only completed daily candles for the prior-day comparison and daily SMA.
            today = now.date()
            hist = daily[daily.index.date < today].copy()
            if len(hist) < 60: raise ValueError("Insufficient completed daily candles")
            hist["SMA20"] = hist["Close"].rolling(20).mean()
            prev = hist.iloc[-1]
            prev_close, prev_sma = float(prev.Close), float(prev.SMA20)
            if not (math.isfinite(prev_sma) and prev_close < prev_sma): continue
            daily_sma20 = float(hist["Close"].tail(19).sum() + latest_price) / 20.0
            if not latest_price > daily_sma20: continue
            # Build current as-of weekly/monthly candles using the latest intraday price.
            daily_asof = hist.copy()
            current_day = pd.DataFrame([{"Open":day_open,
                                         "High":day_high,
                                         "Low":day_low,
                                         "Close":latest_price,"Volume":day_volume}],
                                       index=[pd.Timestamp(today)])
            daily_asof = pd.concat([daily_asof[["Open","High","Low","Close","Volume"]], current_day])
            weekly, monthly = resample_ohlc(daily_asof, "W-FRI"), resample_ohlc(daily_asof, "ME")
            if len(weekly) < 60 or len(monthly) < 20: raise ValueError("Insufficient weekly/monthly history")
            wc = float(weekly.Close.iloc[-1])
            wst = float(supertrend(weekly,7,3).iloc[-1])
            wbb = float(bb_upper(weekly.Close,20,2).iloc[-1])
            wrsi = float(rsi(weekly.Close,14).iloc[-1])
            mrsi = float(rsi(monthly.Close,14).iloc[-1])
            if not all(math.isfinite(v) for v in [wc,wst,wbb,wrsi,mrsi]): raise ValueError("Indicator warm-up/NaN")
            checks = [wc > wst, wc > wbb, wrsi > 60, mrsi > 55,
                      prev_close < prev_sma, latest_price > daily_sma20]
            if all(checks):
                meta = symbols.loc[symbols.YF_TICKER.astype(str)==ticker].iloc[0]
                rows.append({"scan_time_ist":now.strftime("%Y-%m-%d %H:%M:%S"),
                    "exchange":meta.get("EXCHANGE",""),"symbol":meta.get("SYMBOL",""),
                    "name":meta.get("NAME",""),"ticker":ticker,"cmp_15m_close":latest_price,
                    "15m_candle_time":bar_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "previous_day":hist.index[-1].date().isoformat(),"previous_close":prev_close,
                    "previous_sma20":prev_sma,"daily_sma20_asof":daily_sma20,
                    "weekly_close":wc,"weekly_supertrend_7_3":wst,"weekly_upper_bb_20_2":wbb,
                    "weekly_rsi14":wrsi,"monthly_rsi14":mrsi,
                    "conditions_passed":"6/6"})
        except Exception as e:
            failed.append((ticker, str(e)))
        if i % 100 == 0: print(f"Progress {i}/{len(tickers)}; matches={len(rows)}; failed={len(failed)}")
        time.sleep(BATCH_SLEEP)
    columns = ["scan_time_ist","exchange","symbol","name","ticker","cmp_15m_close","15m_candle_time",
               "previous_day","previous_close","previous_sma20","daily_sma20_asof","weekly_close",
               "weekly_supertrend_7_3","weekly_upper_bb_20_2","weekly_rsi14","monthly_rsi14","conditions_passed"]
    pd.DataFrame(rows,columns=columns).to_csv(OUT,index=False)
    pd.DataFrame(failed,columns=["ticker","error"]).to_csv("scan_errors.csv",index=False)
    duration = round(time.monotonic()-started,1)
    print(f"Completed. Matches={len(rows)}, failed={len(failed)}, duration={duration}s. Results: {OUT}")
    if rows:
        lines = ["📊 ON-DEMAND CHARTINK-CONDITION SCAN",f"🕒 {now:%d-%b-%Y %I:%M %p} IST",
                 f"Universe: {len(tickers)} | Matches: {len(rows)} | Failed: {len(failed)}",""]
        for r in rows[:35]:
            lines.append(f"{r['symbol']} | CMP ₹{r['cmp_15m_close']:.2f} | W RSI {r['weekly_rsi14']:.1f} | M RSI {r['monthly_rsi14']:.1f}")
        if len(rows)>35: lines.append(f"...and {len(rows)-35} more; full list in scan_results.csv")
    else:
        lines = ["📊 ON-DEMAND CHARTINK-CONDITION SCAN",f"🕒 {now:%d-%b-%Y %I:%M %p} IST",
                 "No stocks matched all 6 conditions.",f"Universe: {len(tickers)} | Failed: {len(failed)}"]
    lines += ["",f"Duration: {duration}s","CSV artifact: scan_results.csv","Data: Yahoo Finance (yfinance); latest completed 15-minute candle."]
    tg("\n".join(lines))

if __name__ == "__main__":
    main()
