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
