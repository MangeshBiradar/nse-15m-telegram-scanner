# NSE + BSE Weekly Stock Scanner

Automated GitHub Actions scanner for eligible Indian equity shares.

Conditions:
1. Weekly Close >= Weekly Supertrend(10,3)
2. Weekly Close >= Weekly Upper Bollinger Band(20,2)
3. Weekly RSI(14) >= 60
4. Previous trading day's Close < 20-day SMA
5. NSE + BSE equity universe, excluding ETFs, indices, REITs/InvITs, preference shares, warrants and other non-ordinary instruments.

The workflow refreshes the universe before every scan and stores it in symbols.csv. Price history is retrieved with yfinance. Unavailable Yahoo symbols are skipped so one bad ticker does not stop the scan.

GitHub repository setup:
Settings -> Actions -> General -> Workflow permissions -> Read and write permissions.

Secrets:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID

Manual run:
Actions -> Weekly Stock Scanner -> Run workflow.

Schedule:
Friday 17:30 IST (12:00 UTC). GitHub scheduled jobs may be delayed.
