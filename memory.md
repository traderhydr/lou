# Project memory — GG-SHOT → Cornix → Telegram bot

Last updated: 2026-11-04. Branch: `claude/gifted-hypatia-62mwjo` (repo `traderhydr/lou`).
No secrets are stored here (bot token / chat id live only in the server's systemd service file).

## What this is
- Source: the user's TradingView Pine v5 strategy "GG-SHOT" (`GG-SHOT-Cornix.pine` = Pine variant that sends one
  Cornix-format alert; kept for reference, the user decided NOT to use TradingView).
- `cornix-bot/bot.py`: standalone Python bot (only dep: `requests`). Polls Binance USDT-M futures klines after each
  candle close, detects GG-SHOT signals on CLOSED candles, posts a Cornix-format message to a Telegram channel.
- Message: `#BASE/QUOTE`, LONG/SHORT, 3 Entry Targets, Take-Profit Targets (4 by default), 1 Stop Target, `Leverage: Cross (Nx)`.

## Signal / level logic (ported 1:1 from the script, defaults = "Dev mode", no filtering)
- BB: `BB_PERIOD`=100, `BB_DEV`=1.5 (script's Sensitivity/Offset). Signal = ratcheted TrendLine direction flip (`gg_shot`).
- Entry price = close of signal candle. 3 entries spaced `ENTRY_STEP_ATR`=0.5 ATR apart (bot's own choice; script has 1 entry).
- TPs = 1.5 / 3 / 5 / 9 x ATR(14); SL = 6 x ATR(14). `TP_COUNT`, `ALL_TPS`, `TP_LEVEL` control how many TPs are sent.
- SCRIPT QUIRK (kept on purpose, `SCRIPT_SHORT_ATR=1`): the script sizes SHORT TP/SL from the ATR of the LAST LONG signal
  (`valuewhen(buy, ...)`), so chart labels for shorts use a stale ATR. Bot copies this to match the user's chart.
  Test on 1h showed turning it off was not better (it was worse).
- `levels()` / `compute_signals()` in bot.py are the single source used by both the live bot and the backtest.

## Bot reliability features
- State file (`state.json`, per-symbol last posted candle ts) prevents duplicate posts; re-checks last 3 candles each cycle.
- Retries (4x backoff) on Binance calls; candle-closed decision uses Binance server time; heartbeat line each cycle:
  `cycle: N ok, X errors, S signal(s) in last 3 candles, latest closed candle ...`.
- Symbols: bare tickers accepted (`PEPE` -> `1000PEPEUSDT` when needed); unknown coins skipped with a WARNING.
- One real missed signal happened early (XRP LONG 2026-11-03 04:15 UTC, old single-candle check); cause never proven
  (server clock was fine). Look-back + server time + heartbeat were added as mitigation.

## Server (user's Debian VPS "lobo")
- Code: `~/lou/cornix-bot`, venv `./venv/bin/python`. Service: `ggshot` (`/etc/systemd/system/ggshot.service`,
  settings are `Environment=` lines: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SYMBOLS, TIMEFRAME, LEVERAGE ...).
- Update: `cd ~/lou && git pull origin claude/gifted-hypatia-62mwjo && sudo systemctl restart ggshot`.
- Logs: `journalctl -u ggshot -n 5 --no-pager`. Stop: `sudo systemctl stop ggshot` (disable --now to prevent restart).
- To run backtests with the service's settings in a shell: 
  `export $(grep '^Environment=' /etc/systemd/system/ggshot.service | sed 's/^Environment=//' | xargs)`
  (forgetting this makes backtests silently use defaults: BTCUSDT only, 15m).
- Sandbox here cannot reach Binance (proxy 403) — data/backtests must be run on the user's server.

## Current live configuration (as last seen, 2026-11-04 ~15:48 UTC)
- SYMBOLS (43): BTC ETH BNB ADA LTC BCH ARB WOO DOT XRP FIL NEAR 1000SHIB CRV RUNE 1INCH LDO ZEC INJ KNC GRT APE ZIL COTI BEL
  ZRX PEOPLE IMX C98 APT CTSI JASMY DYDX BLUR DOGE TRX RARE LQTY ENA SAGA PYTH EGLD API3 (all loaded, no warnings).
  History of lists: 37 requested -> +ETH -> 50 -> 75 -> 25 (AAVE..PEPE) +ETH,BNB,SOL (28) -> current 43. Most of the
  current list's small caps were NEVER backtested.
- TIMEFRAME was still 5m and LEVERAGE 25 at last check (user was advised to move to 15m / 10x; not confirmed done).
- Cornix-side settings the bot cannot control: entry split, TP split (user wants entries 50/25/25, TPs 34/33/33 =
  3 TPs, SL to entry after TP2), close on opposite signal / Binance one-way mode ("hedge mode off"), max simultaneous
  trades, size. Live bot still sends 4 TPs unless `TP_COUNT=3` is set.

## Second channel + quiet hours (added 2026-11-04)
- `QUIET_HOURS` (e.g. `22:00-06:00`, comma list allowed, may cross midnight) + `QUIET_TZ` (IANA, default UTC): signals
  inside the window are suppressed and marked handled in state (never posted late). Opposite signals in the window are
  also dropped, so an earlier open trade stays open until TP/SL or the next non-quiet signal.
- Second channel = second systemd service named `louga-v2` (user's name for it; first one is `ggshot`), copied from `ggshot` with its own `TELEGRAM_CHAT_ID`,
  `STATE_FILE` (must differ!) and quiet-hours lines. Bot must be admin of that channel.
- FINAL (2026-11-04) channel-2 schedule = QUIET hours, from the user's Cornix "Operation hours" calendar: grey blocks
  with icon = paused, blue entries = resume. Quiet 13:00-16:15 and 19:30-23:15 Europe/Madrid (CET, UTC+1), Mon-Fri only
  (weekends have no block = no pause). Config: `QUIET_HOURS=13:00-16:15,19:30-23:15`, `QUIET_TZ=Europe/Madrid`.
  UPDATE: user then asked for the SAME quiet windows on weekends too -> `QUIET_DAYS` line REMOVED (applies every day). (An interim version wrongly treated these as ACTIVE hours; the `ACTIVE_HOURS`/`ACTIVE_DAYS`
  options still exist but must stay unset for channel 2.) End times exclusive; signals inside a quiet window are dropped.
- Coin list/timeframe for channel 2 not yet specified (it copies channel 1).

## Current setup per bot (user-confirmed 2026-11-05)
- `ggshot` (channel 1): 15m candles. Quiet hours 13:00-16:15, 19:30-23:15 Europe/Madrid were added for it (user ran/was given the
  commands; verify with `grep QUIET /etc/systemd/system/ggshot.service`). State file `state.json`.
- `louga-v2` (channel 2, chat id from its service file, separate `state_ch2.json`): 5m candles, no quiet hours, posts at all hours.
- Both: 43 coins (list above), same code in `~/lou/cornix-bot`. Backtests favoured 15m over 5m and 1h; 5m was the worst.
  Running both is effectively a live side-by-side comparison of 15m vs 5m.

## louga-v2 targets (2026-11-06)
- `louga-v2` sends 3 entries + 3 TPs (1.5, 3, 5 x ATR) + 1 SL via `TP_COUNT=3` in its service file. `ggshot` still sends 4 TPs.
- Splits (entries 50/25/25, TPs 34/33/33) are Cornix-side settings.

## ggshot entry ladder (requested 2026-11-06)
- Wanted: drop old 2nd entry; old 3rd -> 2nd; old "4th" -> 3rd; new 4th halfway between the SL and the new 3rd.
  ggshot only had 3 entries, so user chose old 4th = 1.5 ATR (continuing the 0.5 step).
- Result (ATR from signal price): 0, 1.0, 1.5, then midpoint(1.5 ATR entry, SL at 6 ATR) = 3.75 ATR. Config on ggshot:
  `ENTRY_ATR_MULTS=0,1,1.5` + `ENTRY_MID_SL=1`. Midpoint is skipped when the SL is not beyond the last entry
  (stale-ATR short case). 4 entries means the Cornix entry split (was 50/25/25) must be re-set for 4 entries.
- Not backtested at the time of the change; risk: more size averaged in at worse prices before the 6 ATR stop.

## Coin list update (2026-11-11)
- User pasted the Pine script's asset dropdown; crypto coins only were added to BOTH bots, existing ones ignored, forex/stocks/
  metals and BTC/D (dominance index) skipped. 25 new: QTUM AUDIO AXS BAND OP ENS BAKE CELR 1000PEPE CHR HNT BLZ KAVA ZEN FLOW YFI
  MATIC ANKR TOMO DASH SUSHI SKL UNFI FTM SFP -> 68 coins. `resolve_symbols` now maps MATIC->POL, FTM->S, dedupes, and skips
  coins Binance does not list (possibly delisted: AUDIO, BAKE, BLZ, HNT, TOMO, UNFI...). Not backtested.
- The script has per-coin Sensitivity/Offset presets for these coins (e.g. OP 15m 170/2.8, ZIL 15m 150/3.5, DOT 15m 20/2.5);
  the bot still uses 100/1.5 for all. Per-coin presets = possible next step.

## ggshot 3-entry ladder (2026-11-11)
- Replaces the 4-entry ladder: removed old 2nd entry; entries = signal price, 1.5 ATR, and the old 4th (midpoint between the
  1.5 ATR entry and the SL = 3.75 ATR) moved 0.3% of price toward entry 2 (away from the SL). Config on ggshot:
  `ENTRY_ATR_MULTS=0,1.5`, `ENTRY_MID_SL=1`, `ENTRY_LAST_SHIFT_PCT=0.3`. If the shift would cross entry 2 (tiny ATR), the last
  entry becomes the midpoint of entries 2 and 3. Re-set the Cornix entry split for 3 entries. Not backtested.

## Coins removed (2026-11-11)
- User removed LQTY, COTI, RUNE, INJ from BOTH bots (CHZ and WLD were not in the 68-coin list, so no-ops). 68 -> 64 coins.
  To re-add: append to the `SYMBOLS=` line of the service file.

## History note
- 2026-11-10: quiet hours REMOVED from `ggshot` too at the user's request (no QUIET_/ACTIVE_ lines) -> both bots post at all
  hours. ggshot now: 15m, 25x, 4 entries (0/1.0/1.5/3.75 ATR via ENTRY_ATR_MULTS + ENTRY_MID_SL), 4 TPs, 1 SL.
- 2026-11-05: quiet hours REMOVED from `louga-v2` at the user's request (no QUIET_/ACTIVE_ lines; posts at all hours).
  `ggshot` was given the same quiet hours (13:00-16:15, 19:30-23:15 Europe/Madrid) earlier and keeps them unless changed.
- 2026-11-05: a 24-hour fixed 0.1% TP1 trial for louga-v2 was built, then REMOVED at the user's request (code reverted;
  server `TP1_PCT`/`TP1_UNTIL` lines must be deleted). TPs are back to 1.5/3/5/9 x ATR.

## Backtest tooling (`cornix-bot/`)
- `backtest.py`: portfolio sim — capital, % of equity as MARGIN per trade, leverage, fee per fill, 3 limit entries with
  configurable split, TP split (sets TP count), SL-to-entry after TP N, per-trade liquidation estimate, close on
  opposite signal, stop-before-TP on same candle (conservative). Options: `--days --capital --pct --lev --fee --max-open
  --entry-split --tp-split --be / --be-after N --no-close-opposite --offset-days (earlier window) --refresh --csv`.
- `sweep.py`: one table of single-setting variants (fees, leverage, long/short only, TP, SL, BB presets, BE...).
- `test_backtest.py` / `test_bot.py`: offline tests (synthetic data + fake exchange). Run with `python -I test_backtest.py`.
- Ignores funding, slippage, outages; fills limit orders on touch. Cache in `cornix-bot/cache/` (gitignored).

## Backtest results (user's server, 75 coins unless noted; start $5,000, 5% margin/trade)
| Setup | Final | PF | Max DD | Notes |
|---|---|---|---|---|
| 5m, 25x, 30d, equal entries, 4 TPs, 0.05% fee | $1,007 (-80%) | 0.85 | 89% | fees ~ $4.2k > net loss |
| 5m, 25x, BE after TP1 | $401 (-92%) | 0.87 | 95% | more trades -> more fees |
| 5m, 25x, 50/25/25 + 34/33/33, BE after TP2, 0.03% | $882 (-82%) | 0.93 | 94% | gross ~+$2.2k, fees ~$6.3k |
| 1h, 25x, 90d | $15 (-99.7%) | 0.73 | 99.7% | 40% of trades LIQUIDATED (6xATR SL beyond ~3.5% liq distance) |
| 1h, 10x, 90d | $862 (-82.8%) | 0.84 | 88% | signals lose even with small fees |
| 1h, 10x, SCRIPT_SHORT_ATR=0 | $432 (-91%) | 0.82 | 96% | stale-ATR quirk is NOT the cause |
| 15m, 10x, last 60d | $6,814 (+36%) | 1.04 | 52% | profit all from one quarter (Q3 +$3.0k) |
| 15m, 10x, previous 60d | $4,419 (-11.6%) | 0.98 | 65% | out-of-sample: no edge |
- (BTC-only 15m runs by mistake: ~-1% / +2.4% — ignore, tiny sample.)
- Pattern: long/short PnL flips sign with period/timeframe => market drift, not a stable edge. On 15m shorts beat longs
  in both windows (alts drifting down), not evidence of skill.

## Conclusions / advice given
- No tested setup shows a robust edge; 5m and 1h lose heavily, 15m is ~break-even with 50-65% drawdowns. Fees and
  25x leverage amplify losses; 6xATR stop is beyond the 25x liquidation distance on higher timeframes.
- Assistant recommended NOT trading this with real money, pausing/disconnecting Cornix, and at most using 15m, <=10x,
  tiny size. User kept the bot running with a new 43-coin list on their own decision.

## Open items / ideas
- Confirm user moved the service to 15m / 10x (and whether Cornix is on a real account).
- Backtest the current 43-coin list (15m, both windows, also `--fee 0.05`).
- Optional: cost filter (skip signals whose TP is too small vs fees), `TP_COUNT=3` in service, per-coin BB presets from
  the script (only a few coins have crypto presets: BTC 15m 150/3.1, ETH 15m 300/2, ...), round prices to exchange tick size.
