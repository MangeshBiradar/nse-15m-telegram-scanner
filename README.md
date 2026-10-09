# On-demand NSE+BSE Chartink Conditions Scanner

A **manual/on-demand only** GitHub Actions scanner using Yahoo Finance via `yfinance`. It does not use Angel One SmartAPI and has no scheduled cron run in its new workflow.

## Run it
1. Open the repository on GitHub.
2. Select **Actions → On-demand Chartink Conditions Scanner**.
3. Click **Run workflow → Run workflow**.
4. Open the completed run and download the **on-demand-scan-results** artifact. If Telegram secrets are configured, matches are also sent to Telegram.

## Conditions (all six must pass)
1. Weekly Close > Weekly Supertrend(7,3)
2. Weekly Close > Weekly Upper Bollinger Band(20,2)
3. Weekly RSI(14) > 60
4. Monthly RSI(14) > 55
5. Previous trading day's Close < its Daily SMA(20)
6. Latest completed 15-minute Close > current-as-of Daily SMA(20)

## Output
- `scan_results.csv`: matched symbols and the values used for each condition
- `scan_errors.csv`: symbols whose market data or indicators could not be evaluated
- GitHub Actions artifact: `on-demand-scan-results`
- Optional Telegram summary using repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

## Implementation notes
- Uses the current `symbols.csv` universe already in the repository.
- Uses the latest **completed** 15-minute candle, not an unfinished candle.
- Weekly and monthly bars are recalculated as-of the latest available 15-minute price, so the active week/month can change while markets are open.
- The daily SMA20 comparison uses the latest 19 completed daily closes plus the latest completed 15-minute close; the previous-day comparison uses the prior completed daily close and its 20-day SMA.
- Yahoo Finance data can be delayed, incomplete, or unavailable for individual tickers. Check `scan_errors.csv`; this is not an exchange-grade real-time feed.
