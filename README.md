# NSE+BSE V2.1 Weekly Signal Scanner

This is V2.1 of the existing yfinance-based scanner. **No Angel One SmartAPI is used.**

## Strategy
- Weekly Close >= Weekly Supertrend
- Weekly Close >= Weekly Upper Bollinger Band
- Weekly RSI >= 60
- Previous trading-day Close < 20-day SMA

## Schedule
GitHub Actions runs at:
- 09:15 IST (03:45 UTC), Monday-Friday
- 03:00 PM IST (09:30 UTC), Monday-Friday

Universe refresh remains at 08:30 IST (03:00 UTC), Monday-Friday.

## V2.1 fixes
### 1. Empty universe protection
The exchange universe refresh now:
- bootstraps an NSE session before downloading the official equity list;
- retries NSE/BSE requests;
- rejects suspiciously small/partial feeds;
- preserves a previously valid universe instead of overwriting it with an empty/partial file;
- includes an emergency NSE seed universe so a fresh repository never starts with `Scanning 0 eligible stocks`.

When the official live universe refresh succeeds, `symbols.csv` is replaced with the live NSE+BSE universe.

### 2. Telegram 403 protection
Telegram errors are now **non-fatal**. A 403 is printed with a useful configuration hint, but the scanner itself does not crash while trying to send an alert.

GitHub Secrets required:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

If Telegram is not configured, the scanner still runs and reports the result in the GitHub Actions log.

## Data
Yahoo Finance via `yfinance` is retained as the market-data source, as in V2. This version does not use Angel One SmartAPI.

## Important
The schedule is twice daily, but the strategy calculation continues to use **daily yfinance candles aggregated to weekly data**. It is not a true 15-minute-candle strategy.
