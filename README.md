# NSE/BSE Weekly Scanner

Exact requested logic:

- Weekly Close >= Weekly Supertrend
- Weekly Close >= Weekly Bollinger Upper Band
- Weekly RSI >= 60
- 1-day-ago Daily Close < Daily 20 SMA

The scanner evaluates the latest **completed weekly candle** and uses the daily
condition exactly as a daily condition. It sends only fresh signals and stores
the signalled week per symbol in state.json.

## Universe
The included symbols.csv is only a starter fallback. Replace it with your
full universe generated from exchange security-master files.

For NSE, the official securities page separates the equity-segment CSV from
ETF, REIT/InvIT, debt, preference-share, warrants and other categories.
Therefore use the equity-share/security-master list rather than a broad
instrument list.

For BSE, use the BSE equity security master and exclude ETF, MF, index,
debt, preference, warrants, rights and other non-equity instruments.

## Data caveat
yfinance is used for a zero-cost prototype. It is not an exchange-grade feed.
Validate signals against your broker/chart before trading.

## Telegram
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables or GitHub
Actions secrets.

## Run
pip install -r requirements.txt
python scanner.py
