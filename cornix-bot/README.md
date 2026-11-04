# GG-SHOT → Cornix → Telegram bot (no TradingView)

Runs 24/7 in the cloud. After each candle closes it checks for a GG-SHOT signal and, when one
appears, posts a Cornix-format message (3 entries, 1 TP, 1 SL) to your Telegram channel.

## Setup
1. Create a bot with @BotFather → copy the token. Add the bot as **admin** of your signal channel/group
   (the one Cornix is connected to). Get the chat id (channels look like `-100123456789`).
2. Configure environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | bot token |
| `TELEGRAM_CHAT_ID` | – | channel/group id |
| `SYMBOLS` | `BTCUSDT` | comma list, Binance USDT-M futures symbols |
| `TIMEFRAME` | `15m` | candle size |
| `BB_PERIOD` / `BB_DEV` | `100` / `1.5` | GG-SHOT Sensitivity / Offset |
| `ALL_TPS` | `1` | `1` = send all 4 TPs (like the chart); `0` = only the one in `TP_LEVEL` |
| `TP_LEVEL` | `1` | which of TP1–TP4 (ATR x `1.5,3,5,9`) |
| `SL_ATR_MULT` | `6` | stop distance in ATR |
| `ENTRY_STEP_ATR` | `0.5` | spacing between the 3 entries |
| `LEVERAGE`, `MARGIN_TYPE` | `10`, `Cross` | shown to Cornix |
| `ACTIVE_HOURS` | (off) | post ONLY inside these windows, e.g. `13:00-16:15,19:30-23:15` (end time exclusive) |
| `ACTIVE_DAYS` | (every day) | ISO weekdays, Mon=1: `1-5` = Monday to Friday |
| `QUIET_HOURS` | (off) | no posting in these windows, e.g. `22:00-06:00` or `00:00-04:00,13:00-14:00` |
| `QUIET_TZ` | `UTC` | timezone for `ACTIVE_HOURS`/`ACTIVE_DAYS`/`QUIET_HOURS`, IANA name e.g. `Asia/Kolkata` |
| `STATE_FILE` | `state.json` | MUST differ per running copy of the bot |
| `EXCHANGE_URL` | `https://fapi.binance.com` | change if your host is geo-blocked |

3. Check it: `python bot.py --test-telegram`, then `python bot.py --history` (past signals, posts nothing),
   then `python bot.py --dry-run` (prints instead of posting), then `python bot.py`.

## Deploy
Any always-on host works (Railway, Render worker, Fly.io, a VPS):
```bash
docker build -t gg-shot-bot .
docker run -d --restart always -v ggdata:/data \
  -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... -e SYMBOLS=BTCUSDT,ETHUSDT gg-shot-bot
```
Keep the `/data` volume so a restart never re-posts the same signal.

Notes: Binance blocks some cloud regions (US); pick an EU/Asia region. Signals are evaluated on
closed candles only and use the "No Filtering" mode with ATR-based TP/SL from the original script.

## Backtest
```bash
# load the same settings as the service (coins, timeframe, leverage)
export $(grep '^Environment=' /etc/systemd/system/ggshot.service | sed 's/^Environment=//' | xargs)
./venv/bin/python backtest.py --days 30 --capital 5000 --pct 5 --lev 25
```
Options: `--fee` (% per fill, default 0.05), `--max-open N`, `--be` (move SL to entry after TP1),
`--no-close-opposite`, `--refresh` (re-download). Data is cached in `cache/`; trades go to `trades.csv`.
Offline self-test: `python test_backtest.py`.

## Second channel / second copy
Run another systemd service (own `TELEGRAM_CHAT_ID`, own `STATE_FILE`, optional `QUIET_HOURS`).
Two copies sharing one state file would corrupt the duplicate protection. Signals inside quiet hours are dropped
(not delayed), and a trade opened earlier is NOT closed by an opposite signal that falls in the quiet window.
