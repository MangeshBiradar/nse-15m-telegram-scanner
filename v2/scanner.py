import argparse
import io
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin

import logzero
import pandas as pd
import pandas_ta as ta
import pyotp
import requests
from dotenv import load_dotenv
from SmartApi import SmartConnect

logzero.loglevel(logging.ERROR)
logging.getLogger("smartConnect").setLevel(logging.ERROR)

load_dotenv()
API_KEY = os.getenv("ANGEL_API_KEY")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
PIN = os.getenv("ANGEL_PIN")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

if not all([API_KEY, CLIENT_CODE, PIN, TOTP_SECRET]):
    raise ValueError("Missing one or more Angel One credentials in .env file.")

TOKEN_CACHE_FILE = "nse_tokens_cache.json"
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
CANDLE_CACHE_DIR = os.path.join("candle_cache", TODAY_STR)


def prepare_daily_cache(force_refresh=False):
    """Creates today's historical cache folder, clearing old days or forcing refresh."""
    base_dir = "candle_cache"
    if force_refresh and os.path.exists(CANDLE_CACHE_DIR):
        shutil.rmtree(CANDLE_CACHE_DIR, ignore_errors=True)
        print("Cleared today's candle cache (--refresh-cache).")

    os.makedirs(CANDLE_CACHE_DIR, exist_ok=True)
    for folder in os.listdir(base_dir):
        full_path = os.path.join(base_dir, folder)
        if os.path.isdir(full_path) and folder != TODAY_STR:
            shutil.rmtree(full_path, ignore_errors=True)


def login_smartapi():
    """Authenticates with Angel One SmartAPI using PIN and TOTP."""
    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    session = smart_api.generateSession(CLIENT_CODE, PIN, totp)
    if not session.get("status"):
        raise Exception(f"Login failed: {session.get('message')}")
    return smart_api


class RateLimiter:
    """Thread-safe rate limiter with global cooldown on rate-limit errors."""

    def __init__(self, max_per_second=1.8):
        self.min_interval = 1.0 / max_per_second
        self.lock = threading.Lock()
        self.last_call = 0.0
        self.cooldown_until = 0.0

    def wait(self):
        with self.lock:
            now = time.perf_counter()
            if now < self.cooldown_until:
                time.sleep(self.cooldown_until - now)
                now = time.perf_counter()
            wait_time = self.min_interval - (now - self.last_call)
            if wait_time > 0:
                time.sleep(wait_time)
            self.last_call = time.perf_counter()

    def trigger_cooldown(self, seconds=3.5):
        with self.lock:
            self.cooldown_until = max(
                self.cooldown_until, time.perf_counter() + seconds
            )


def call_with_retry(func, limiter, retries=5):
    """Executes a SmartAPI call with global cooldown retry on rate limits."""
    for attempt in range(retries):
        limiter.wait()
        try:
            return func()
        except Exception as e:
            err = str(e).lower()
            if (
                "access rate" in err or "couldn't parse" in err or "ab1004" in err
            ) and attempt < retries - 1:
                limiter.trigger_cooldown(3.5 * (attempt + 1))
                continue
            raise
    return None
def fetch_index_csv(url, local_file, headers):
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].dropna().str.strip().tolist()
        print(f"Loaded {len(symbols)} symbols.")
        return symbols
    except Exception as e:
        if os.path.exists(local_file):
            print(f"Using local {local_file} fallback...")
            return pd.read_csv(local_file)["Symbol"].dropna().str.strip().tolist()
        raise RuntimeError(f"Failed to load {local_file} ({e}).")


def fetch_all_nse_cash_symbols(headers):
    url = urljoin(
        "https://" + "nsearchives.nseindia" + ".com/",
        "content/equities/EQUITY_L.csv",
    )
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = df.columns.str.strip()
        symbols = (
            df[df["SERIES"].str.strip() == "EQ"]["SYMBOL"]
            .dropna()
            .str.strip()
            .tolist()
        )
        print(f"Loaded {len(symbols)} NSE Cash (EQ) symbols.")
        return symbols
    except Exception as e:
        if os.path.exists("EQUITY_L.csv"):
            print("Using local EQUITY_L.csv fallback...")
            df = pd.read_csv("EQUITY_L.csv")
            df.columns = df.columns.str.strip()
            return (
                df[df["SERIES"].str.strip() == "EQ"]["SYMBOL"]
                .dropna()
                .str.strip()
                .tolist()
            )
        raise RuntimeError(f"Failed to download EQUITY_L.csv ({e}).")


def choose_options(args):
    if args.universe:
        choice = args.universe.lower().strip()
    else:
        print("\nSelect Stock Universe to Scan:")
        print("  1. Nifty 50")
        print("  2. Nifty 500")
        print("  3. All NSE Cash Components (EQ series)")
        raw = input("\nEnter choice (1, 2, or 3) [default: 1]: ").strip()
        choice = {"1": "nifty50", "2": "nifty500", "3": "all", "": "nifty50"}.get(
            raw, raw.lower()
        )

    if args.skip_cmp_sma:
        check_c5 = False
    elif args.universe:
        check_c5 = True
    else:
        c5_in = (
            input("Enable Condition 5 (CMP > Daily 20 SMA)? (Y/N) [default: Y]: ")
            .strip()
            .lower()
        )
        check_c5 = c5_in not in ("n", "no", "false", "0")

    headers = {"User-Agent": "Mozilla/5.0"}
    base = "https://" + "www.niftyindices" + ".com/"
    if choice == "nifty50":
        return (
            "nifty50",
            fetch_index_csv(
                urljoin(base, "IndexConstituent/ind_nifty50list.csv"),
                "ind_nifty50list.csv",
                headers,
            ),
            check_c5,
        )
    if choice == "nifty500":
        return (
            "nifty500",
            fetch_index_csv(
                urljoin(base, "IndexConstituent/ind_nifty500list.csv"),
                "ind_nifty500list.csv",
                headers,
            ),
            check_c5,
        )
    if choice == "all":
        return "all_nse_cash", fetch_all_nse_cash_symbols(headers), check_c5
    raise ValueError("Invalid selection.")


def load_token_cache():
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_token_cache(cache):
    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def fetch_live_ohlc_batch(smart_api, tokens, limiter):
    """Fetches current-day OHLC quotes in batches of 50 tokens per call."""
    live_map = {}
    unique_tokens = [str(t) for t in set(tokens) if t]
    for i in range(0, len(unique_tokens), 50):
        batch = unique_tokens[i : i + 50]
        res = call_with_retry(
            lambda b=batch: smart_api.getMarketData("OHLC", {"NSE": b}), limiter
        )
        if res and res.get("status") and res.get("data"):
            for item in res["data"].get("fetched", []):
                tok = str(item.get("symbolToken"))
                ltp = float(item.get("ltp") or 0)
                if ltp > 0:
                    live_map[tok] = {
                        "open": float(item.get("open") or ltp),
                        "high": float(item.get("high") or ltp),
                        "low": float(item.get("low") or ltp),
                        "close": ltp,
                        "volume": float(item.get("tradeVolume") or 0),
                    }
    return live_map


def get_combined_candles(smart_api, symbol, token, limiter, live_ohlc_map):
    """Loads completed daily candles from disk cache and appends today's live candle only during an active new session."""
    cache_path = os.path.join(CANDLE_CACHE_DIR, f"{symbol}.pkl")
    today_ts = pd.Timestamp(TODAY_STR)

    if os.path.exists(cache_path):
        df_hist = pd.read_pickle(cache_path)
    else:
        to_date = datetime.now()
        from_date = to_date - timedelta(days=1500)
        params = {
            "exchange": "NSE",
            "symboltoken": str(token),
            "interval": "ONE_DAY",
            "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
            "todate": to_date.strftime("%Y-%m-%d 15:30"),
        }
        res = call_with_retry(lambda: smart_api.getCandleData(params), limiter)
        if not res or not res.get("status") or not res.get("data"):
            return None
        df = pd.DataFrame(
            res["data"], columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.set_index("timestamp")
        df_hist = df[df.index.normalize() < today_ts]
        df_hist.to_pickle(cache_path)

    live_bar = live_ohlc_map.get(str(token))
    if live_bar and today_ts.weekday() < 5 and not df_hist.empty:
        last_bar = df_hist.iloc[-1]
        is_duplicate_candle = (
            round(float(last_bar["open"]), 2) == round(live_bar["open"], 2)
            and round(float(last_bar["high"]), 2) == round(live_bar["high"], 2)
            and round(float(last_bar["low"]), 2) == round(live_bar["low"], 2)
            and round(float(last_bar["close"]), 2) == round(live_bar["close"], 2)
            and int(last_bar["volume"]) == int(live_bar["volume"])
        )
        if not is_duplicate_candle:
            today_row = pd.DataFrame(
                [live_bar], index=pd.DatetimeIndex([today_ts], name="timestamp")
            )
            df_combined = pd.concat([df_hist, today_row])
            return df_combined[~df_combined.index.duplicated(keep="last")]

    return df_hist
def evaluate_stock(df_daily, check_c5=True):
    """Evaluates weekly and daily conditions using previous daily candle and current daily candle."""
    if df_daily is None or len(df_daily) < 200:
        return False, None

    df_daily = df_daily.sort_index()
    df_daily["sma20"] = ta.sma(df_daily["close"], length=20)
    if pd.isna(df_daily["sma20"].iloc[-2]):
        return False, None

    prev_candle_close = float(df_daily["close"].iloc[-2])
    prev_candle_sma20 = float(df_daily["sma20"].iloc[-2])
    cmp = float(df_daily["close"].iloc[-1])
    curr_sma20 = float(df_daily["sma20"].iloc[-1])

    df_weekly = (
        df_daily.resample("W-SUN")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    if len(df_weekly) < 30:
        return False, None

    st = ta.supertrend(df_weekly["high"], df_weekly["low"], df_weekly["close"], length=7, multiplier=3)
    df_weekly["rsi14"] = ta.rsi(df_weekly["close"], length=14)
    bb = ta.bbands(df_weekly["close"], length=20, std=2, ddof=0)

    st_cols = [c for c in st.columns if c.startswith("SUPERT_7_")]
    bbu_cols = [c for c in bb.columns if c.startswith("BBU_20_")]
    if not st_cols or not bbu_cols:
        return False, None

    w_close = float(df_weekly["close"].iloc[-1])
    w_supertrend = float(st[st_cols[0]].iloc[-1])
    w_rsi = float(df_weekly["rsi14"].iloc[-1])
    w_bb_upper = float(bb[bbu_cols[0]].iloc[-1])

    if any(pd.isna([w_supertrend, w_rsi, w_bb_upper])):
        return False, None

    c1 = w_close > w_supertrend
    c2 = w_rsi > 60
    c3 = w_close > w_bb_upper
    c4 = prev_candle_close < prev_candle_sma20
    c5 = cmp > curr_sma20

    is_match = all([c1, c2, c3, c4, c5] if check_c5 else [c1, c2, c3, c4])

    return is_match, {
        "W_Close": round(w_close, 2),
        "W_ST_7_3": round(w_supertrend, 2),
        "C1_W_ST": c1,
        "W_RSI": round(w_rsi, 2),
        "C2_W_RSI": c2,
        "W_BB_Upper": round(w_bb_upper, 2),
        "C3_W_BB": c3,
        "Prev_Candle_Close": round(prev_candle_close, 2),
        "Prev_Candle_SMA20": round(prev_candle_sma20, 2),
        "C4_Prev_Candle_Below_SMA": c4,
        "CMP": round(cmp, 2),
        "Daily_SMA20": round(curr_sma20, 2),
        "C5_CMP_Above_SMA": c5 if check_c5 else f"SKIP ({c5})",
        "Matched": is_match,
    }


def process_single_symbol(idx, symbol, smart_api, token_cache, cache_lock, limiter, live_ohlc_map, check_c5):
    with cache_lock:
        token = token_cache.get(symbol)

    fetched_new = False
    if not token:
        time.sleep(0.6)
        res = call_with_retry(lambda: smart_api.searchScrip("NSE", f"{symbol}-EQ"), limiter)
        if res and res.get("status") and res.get("data"):
            for item in res["data"]:
                if item.get("tradingsymbol") == f"{symbol}-EQ":
                    token = item.get("symboltoken")
                    with cache_lock:
                        token_cache[symbol] = token
                    fetched_new = True
                    break

    if not token:
        return idx, symbol, None, None, fetched_new, "SKIP (Token not found)"

    df_daily = get_combined_candles(smart_api, symbol, token, limiter, live_ohlc_map)
    is_match, m = evaluate_stock(df_daily, check_c5=check_c5)
    if m is None:
        return idx, symbol, None, None, fetched_new, "SKIP (Insufficient data)"

    m["Symbol"] = symbol
    return idx, symbol, is_match, m, fetched_new, None


def run_screener():
    parser = argparse.ArgumentParser(description="Angel One SmartAPI Stock Screener")
    parser.add_argument("--universe", choices=["nifty50", "nifty500", "all"])
    parser.add_argument("--skip-cmp-sma", action="store_true")
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Force re-download of today's historical candle cache",
    )
    args = parser.parse_args()

    prepare_daily_cache(force_refresh=args.refresh_cache)
    universe_name, watchlist, check_c5 = choose_options(args)

    print("Logging into Angel One SmartAPI...")
    smart_api = login_smartapi()

    token_cache = load_token_cache()
    cache_lock = threading.Lock()
    limiter = RateLimiter(max_per_second=1.8)
    cache_updated = False

    print("Fetching batch live intraday OHLC quotes...")
    cached_tokens = [token_cache.get(s) for s in watchlist if token_cache.get(s)]
    live_ohlc_map = fetch_live_ohlc_batch(smart_api, cached_tokens, limiter)

    matches, all_scanned = [], []
    total = len(watchlist)
    completed = 0
    start_time = time.perf_counter()
    c5_status = "ENABLED" if check_c5 else "DISABLED"
    print(f"Scanning {total} stocks in [{universe_name.upper()}] | Condition 5: {c5_status}\n")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                process_single_symbol, idx, sym, smart_api, token_cache, cache_lock, limiter, live_ohlc_map, check_c5
            ): sym
            for idx, sym in enumerate(watchlist, start=1)
        }

        for future in as_completed(futures):
            completed += 1
            try:
                _, symbol, is_match, m, fetched_new, skip_reason = future.result()
                if fetched_new:
                    cache_updated = True
                if skip_reason:
                    print(f"[{completed}/{total}] {symbol:<12} | {skip_reason}")
                    continue

                all_scanned.append(m)
                if is_match:
                    matches.append(m)

                tag = "MATCH" if is_match else "SCAN "
                c5_label = f"{'Y' if m['C5_CMP_Above_SMA'] is True else 'N'}" if check_c5 else "OFF"
                print(
                    f"[{completed}/{total}] [{tag}] {symbol:<12} | "
                    f"W_C:{m['W_Close']} > W_ST:{m['W_ST_7_3']} ({'Y' if m['C1_W_ST'] else 'N'}) | "
                    f"W_RSI:{m['W_RSI']}>60 ({'Y' if m['C2_W_RSI'] else 'N'}) | "
                    f"W_C:{m['W_Close']} > W_BB:{m['W_BB_Upper']} ({'Y' if m['C3_W_BB'] else 'N'}) | "
                    f"PrevC:{m['Prev_Candle_Close']} < PrevSMA:{m['Prev_Candle_SMA20']} ({'Y' if m['C4_Prev_Candle_Below_SMA'] else 'N'}) | "
                    f"CMP:{m['CMP']} > SMA20:{m['Daily_SMA20']} ({c5_label})"
                )

                if cache_updated and completed % 100 == 0:
                    with cache_lock:
                        save_token_cache(token_cache)
                    cache_updated = False
            except Exception as e:
                print(f"[{completed}/{total}] [ERROR] {futures[future]:<12} | {e}")

    if cache_updated:
        with cache_lock:
            save_token_cache(token_cache)

    elapsed = round(time.perf_counter() - start_time, 1)
    all_csv = f"{universe_name}_all_scan.csv"
    match_csv = f"{universe_name}_matches.csv"

    if all_scanned:
        pd.DataFrame(all_scanned).set_index("Symbol").to_csv(all_csv)

    print(f"\n--- {universe_name.upper()} MATCHING STOCKS (Completed in {elapsed}s) ---")
    if matches:
        results_df = pd.DataFrame(matches).set_index("Symbol")
        print(results_df.to_string())
        results_df.to_csv(match_csv)
        print(f"\nSaved matches to {match_csv} and full report to {all_csv}")
    else:
        print(f"No stocks matched the active criteria. Full report saved to {all_csv}")


if __name__ == "__main__":
    run_screener()
