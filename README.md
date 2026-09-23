# NSE+BSE 15-Minute Intraday Scanner V2

Runs the requested weekly-condition scanner every 15 minutes during Indian market hours.

Schedule: 09:15 through 15:30 IST, Monday-Friday, using GitHub Actions cron. GitHub cron is best-effort and may be delayed.

Conditions:
1. Latest completed weekly Close >= Weekly Supertrend(10,3)
2. Latest completed weekly Close >= Weekly Upper Bollinger Band(20,2)
3. Latest completed weekly RSI(14) >= 60
4. Previous trading day's Close < its 20-day SMA

The universe is refreshed once per day, then cached in symbols.csv. The scanner uses the cached universe on each 15-minute run.

IMPORTANT: yfinance is a third-party data source. This is a low-cost prototype, not exchange-grade real-time infrastructure. GitHub Actions cron and Yahoo Finance data can be delayed or unavailable. For true 15-minute intraday execution/alerts, a broker or market-data API is more reliable.

GitHub repository Settings -> Actions -> General -> Workflow permissions -> Read and write permissions.

Required repository secrets:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID

## Telegram alerts (V2)
Each run sends a Telegram summary with IST timestamp, status, run type, stocks scanned, matches, new alerts, failures/skips, duration, strategy conditions, and detailed match metrics. The data source remains **yfinance**.

## Schedule
GitHub Actions runs at approximately **09:15 AM IST** and **03:00 PM IST**, Monday-Friday. `workflow_dispatch` remains available for manual runs.
