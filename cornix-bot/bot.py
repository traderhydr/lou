#!/usr/bin/env python3
"""GG-SHOT signal bot: detects signals on closed candles and posts a Cornix-format
message (3 entries, 1 TP, 1 SL) to a Telegram channel/group. No TradingView needed.

Config via environment variables (see README.md). Only dependency: requests.
"""
import json
import math
import os
import sys
import time

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

NAN = float("nan")


def env(name, default, cast=str):
    v = os.environ.get(name)
    return default if v in (None, "") else cast(v)


CFG = {
    "token": env("TELEGRAM_BOT_TOKEN", ""),
    "chat_id": env("TELEGRAM_CHAT_ID", ""),
    "symbols": [s.strip().upper() for s in env("SYMBOLS", "BTCUSDT").split(",") if s.strip()],
    "timeframe": env("TIMEFRAME", "15m"),
    "bb_period": env("BB_PERIOD", 100, int),        # GG-SHOT "Sensitivity"
    "bb_dev": env("BB_DEV", 1.5, float),            # GG-SHOT "Offset"
    "atr_period": env("ATR_PERIOD", 14, int),
    "sl_mult": env("SL_ATR_MULT", 6.0, float),
    "tp_level": env("TP_LEVEL", 1, int),            # which of TP1..TP4 to use (script default: 1)
    # 1 = copy the script: SHORT TP/SL use the ATR from the last LONG signal (its valuewhen(buy,..) quirk)
    "script_short_atr": env("SCRIPT_SHORT_ATR", "1") == "1",
    "tp_count": env("TP_COUNT", 4, int),             # how many of TP1..TP4 to send when ALL_TPS=1
    # entry distances in ATR from the signal price; empty = [0, ENTRY_STEP_ATR, 2*ENTRY_STEP_ATR]
    "entry_mults": [float(x) for x in env("ENTRY_ATR_MULTS", "").split(",") if x.strip()],
    "entry_mid_sl": env("ENTRY_MID_SL", "0") == "1",  # 1 = add a last entry halfway between the last entry and the SL
    "all_tps": env("ALL_TPS", "1") == "1",           # 1 = send TP1..TP4 like the chart
    "tp_mults": [float(x) for x in env("TP_ATR_MULTS", "1.5,3,5,9").split(",")],
    "entry_step": env("ENTRY_STEP_ATR", 0.5, float),  # spacing between the 3 entries
    "leverage": env("LEVERAGE", 10, int),
    "margin": env("MARGIN_TYPE", "Cross"),
    "base_url": env("EXCHANGE_URL", "https://fapi.binance.com"),
    "state_file": env("STATE_FILE", "state.json"),
    "active_raw": env("ACTIVE_HOURS", ""),          # post ONLY inside these windows, e.g. "13:00-16:15,19:30-23:15"
    "days_raw": env("ACTIVE_DAYS", ""),             # ISO weekdays Mon=1..Sun=7, e.g. "1-5" (empty = every day)
    "quiet_days_raw": env("QUIET_DAYS", ""),        # weekdays the QUIET_HOURS apply on, e.g. "1-5" (empty = every day)
    "quiet_raw": env("QUIET_HOURS", ""),            # e.g. "22:00-06:00" or "00:00-04:00,13:00-14:00"
    "quiet_tz": env("QUIET_TZ", "UTC"),             # IANA tz for QUIET_HOURS/ACTIVE_HOURS/ACTIVE_DAYS, e.g. "Europe/Madrid"
    "long": env("LONG_SIDE", "1") == "1",
    "short": env("SHORT_SIDE", "1") == "1",
}

LOOKBACK = 3  # candles re-checked each cycle; state file prevents duplicates

def parse_quiet(text):
    """'22:00-06:00,12:00-13:00' -> [(1320, 360), (720, 780)] minutes since midnight."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-")
        h1, m1 = (int(x) for x in a.split(":"))
        h2, m2 = (int(x) for x in b.split(":"))
        out.append((h1 * 60 + m1, h2 * 60 + m2))
    return out


def parse_days(text):
    """'1-5' or '1,2,3' or '1-3,6' -> {1,2,3,...} (ISO weekdays, Mon=1)."""
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out.update(range(a, b + 1))
        else:
            out.add(int(part))
    return out


CFG["quiet"] = parse_quiet(CFG["quiet_raw"])
CFG["active"] = parse_quiet(CFG["active_raw"])
CFG["active_days"] = parse_days(CFG["days_raw"])
CFG["quiet_days"] = parse_days(CFG["quiet_days_raw"])


def _in_windows(windows, minute):
    for start, end in windows:
        if (start <= minute < end) if start <= end else (minute >= start or minute < end):
            return True
    return False


def is_quiet(now=None):
    """True when posting is NOT allowed: wrong weekday, outside ACTIVE_HOURS, or inside QUIET_HOURS.
    Windows may cross midnight; end time is exclusive. `now` must be timezone-aware."""
    if not (CFG["quiet"] or CFG["active"] or CFG["active_days"]):
        return False
    tz = ZoneInfo(CFG["quiet_tz"])
    now = now.astimezone(tz) if now else datetime.now(tz)
    m = now.hour * 60 + now.minute
    if CFG["active_days"] and now.isoweekday() not in CFG["active_days"]:
        return True
    if CFG["active"] and not _in_windows(CFG["active"], m):
        return True
    if CFG["quiet_days"] and now.isoweekday() not in CFG["quiet_days"]:
        return False  # quiet windows do not apply on this weekday (e.g. weekends)
    return _in_windows(CFG["quiet"], m)


def schedule_text():
    parts = []
    if CFG["active_raw"]:
        parts.append(f"active {CFG['active_raw']}")
    if CFG["days_raw"]:
        parts.append(f"days {CFG['days_raw']}")
    if CFG["quiet_raw"]:
        parts.append(f"quiet {CFG['quiet_raw']}" + (f" on days {CFG['quiet_days_raw']}" if CFG["quiet_days_raw"] else ""))
    return (", ".join(parts) + f" {CFG['quiet_tz']}") if parts else ""


TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
              "2h": 7200, "4h": 14400, "1d": 86400}


# ---------------------------------------------------------------- indicators
def atr_series(h, l, c, period):
    """Pine ta.atr: RMA of true range, seeded with the SMA of the first `period` values."""
    n = len(c)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
                          for i in range(1, n)]
    out = [NAN] * n
    if n < period:
        return out
    out[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def gg_shot(h, l, c, bb_period, bb_dev):
    """Literal port of the Pine logic (UseATRfilter=false, 'No Filtering').
    Returns (long_final[], short_final[])."""
    n = len(c)
    tl = [NAN] * n
    it = [NAN] * n
    longs = [False] * n
    shorts = [False] * n
    for i in range(n):
        sig = 0
        if i >= bb_period - 1:
            win = c[i - bb_period + 1:i + 1]
            m = sum(win) / bb_period
            sd = math.sqrt(sum((x - m) ** 2 for x in win) / bb_period)  # Pine stdev = population
            if c[i] > m + sd * bb_dev:
                sig = 1
            elif c[i] < m - sd * bb_dev:
                sig = -1
        prev = tl[i - 1] if i > 0 else NAN
        v = prev
        if sig == 1:
            v = l[i]
            if v < prev:
                v = prev
        elif sig == -1:
            v = h[i]
            if v > prev:
                v = prev
        tl[i] = v
        it[i] = it[i - 1] if i > 0 else NAN
        if v > prev:
            it[i] = 1
        elif v < prev:
            it[i] = -1
        if i > 0:
            longs[i] = it[i - 1] == -1 and it[i] == 1
            shorts[i] = it[i - 1] == 1 and it[i] == -1
    return longs, shorts


def levels(side, price, atr, tp_atr=None):
    """Returns (entries[3], take_profits[], stop_loss). Single source for bot and backtest.
    atr: current ATR (entry spacing). tp_atr: ATR used for TP/SL distances (defaults to atr)."""
    cfg = CFG
    sgn = 1 if side == "LONG" else -1
    tp_atr = atr if tp_atr is None else tp_atr
    e_mults = cfg["entry_mults"] or [cfg["entry_step"] * k for k in range(3)]
    entries = [price - sgn * atr * m for m in e_mults]
    mults = cfg["tp_mults"][:cfg["tp_count"]] if cfg["all_tps"] else [cfg["tp_mults"][cfg["tp_level"] - 1]]
    tps = [price + sgn * tp_atr * m for m in mults]
    sl = price - sgn * tp_atr * cfg["sl_mult"]
    if cfg["entry_mid_sl"] and (entries[-1] - sl) * sgn > 0:  # only if the last entry is still on the safe side of the SL
        entries.append((entries[-1] + sl) / 2)
    return entries, tps, sl


def build_signal(symbol, side, price, atr, prec, tp_atr=None):
    cfg = CFG
    entries, tps, sl = levels(side, price, atr, tp_atr)
    f = lambda x: f"{x:.{prec}f}"
    base, quote = (symbol[:-4], symbol[-4:]) if symbol.endswith("USDT") else (symbol[:-3], symbol[-3:])
    return "\n".join([
        f"#{base}/{quote}",
        side,
        "Entry Targets:",
        *[f"{i + 1}) {f(e)}" for i, e in enumerate(entries)],
        "Take-Profit Targets:",
        *[f"{i + 1}) {f(t)}" for i, t in enumerate(tps)],
        "Stop Targets:",
        f"1) {f(sl)}",
        f"Leverage: {cfg['margin']} ({cfg['leverage']}x)",
    ])


def compute_signals(klines, all_history=False):
    """klines: closed candles only. Returns dicts: i, ts, side, price, atr, tp_atr."""
    h = [float(k[2]) for k in klines]
    l = [float(k[3]) for k in klines]
    c = [float(k[4]) for k in klines]
    longs, shorts = gg_shot(h, l, c, CFG["bb_period"], CFG["bb_dev"])
    atr = atr_series(h, l, c, CFG["atr_period"])
    # ATR frozen at the most recent LONG signal (Pine: ta.valuewhen(buy, ta.atr(14), 0))
    last_buy_atr, cur = [NAN] * len(c), NAN
    for j in range(len(c)):
        if longs[j] and CFG["long"] and not math.isnan(atr[j]):
            cur = atr[j]
        last_buy_atr[j] = cur
    idx = range(len(c)) if all_history else range(max(0, len(c) - LOOKBACK), len(c))
    out = []
    for i in idx:
        if math.isnan(atr[i]):
            continue
        if longs[i] and CFG["long"]:
            out.append({"i": i, "ts": klines[i][0], "side": "LONG", "price": c[i],
                        "atr": atr[i], "tp_atr": atr[i]})
        elif shorts[i] and CFG["short"]:
            tp_atr = last_buy_atr[i] if CFG["script_short_atr"] and not math.isnan(last_buy_atr[i]) else atr[i]
            out.append({"i": i, "ts": klines[i][0], "side": "SHORT", "price": c[i],
                        "atr": atr[i], "tp_atr": tp_atr})
    return out


def detect(klines, symbol, prec, all_history=False):
    """Returns list of (open_time_ms, side, message)."""
    return [(s["ts"], s["side"], build_signal(symbol, s["side"], s["price"], s["atr"], prec, s["tp_atr"]))
            for s in compute_signals(klines, all_history)]


# ------------------------------------------------------------------- I/O
_prec_cache = {}


def get_json(url, params=None, tries=4):
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def price_precision(symbol):
    if not _prec_cache:
        for s in get_json(CFG["base_url"] + "/fapi/v1/exchangeInfo")["symbols"]:
            _prec_cache[s["symbol"]] = s["pricePrecision"]
    return _prec_cache.get(symbol, 4)


def resolve_symbols(names):
    """Accept 'BTC', 'BTCUSDT' or 'PEPE' (-> 1000PEPEUSDT). Unknown coins are skipped with a warning."""
    price_precision("BTCUSDT")  # loads exchangeInfo into the cache
    out = []
    for n in names:
        n = n.upper()
        if not n.endswith("USDT"):
            n += "USDT"
        for cand in (n, "1000" + n):
            if cand in _prec_cache:
                out.append(cand)
                break
        else:
            print(f"WARNING: {n} not found on exchange, skipping", file=sys.stderr)
    return out


def server_time_ms():
    try:
        return get_json(CFG["base_url"] + "/fapi/v1/time")["serverTime"]
    except requests.RequestException:
        return int(time.time() * 1000)


def fetch_closed_klines(symbol):
    k = get_json(CFG["base_url"] + "/fapi/v1/klines",
                 {"symbol": symbol, "interval": CFG["timeframe"], "limit": 1500})
    # drop still-forming candles, judged by the exchange's clock (not ours)
    now_ms = server_time_ms()
    while k and k[-1][6] >= now_ms:
        k = k[:-1]
    return k


def send_telegram(text):
    url = f"https://api.telegram.org/bot{CFG['token']}/sendMessage"
    for attempt in range(4):
        try:
            r = requests.post(url, data={"chat_id": CFG["chat_id"], "text": text}, timeout=20)
            if r.ok:
                return True
            print("telegram error:", r.status_code, r.text, file=sys.stderr)
        except requests.RequestException as e:
            print("telegram request failed:", e, file=sys.stderr)
        time.sleep(2 ** (attempt + 1))
    return False


def load_state():
    try:
        with open(CFG["state_file"]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = CFG["state_file"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, CFG["state_file"])


def run_once(dry_run=False):
    state = load_state()
    ok = errs = found = 0
    last_candle = None
    for sym in CFG["symbols"]:
        try:
            klines = fetch_closed_klines(sym)
            sigs = detect(klines, sym, price_precision(sym))
            ok += 1
            last_candle = klines[-1][0]
        except Exception as e:  # keep the loop alive on network/API errors
            print(f"{sym}: error {e}", file=sys.stderr)
            errs += 1
            continue
        found += len(sigs)
        for ts, side, msg in sigs:
            if ts <= state.get(sym, 0):
                continue  # already posted (or older than the last posted signal)
            if is_quiet():
                print(f"{sym} {side} signal @ {ts} SUPPRESSED (outside operation hours: {schedule_text()})")
                if not dry_run:  # mark as handled so it is not posted late, after the quiet window ends
                    state[sym] = ts
                    save_state(state)
                continue
            print(f"{sym} {side} signal @ {ts}\n{msg}\n")
            if dry_run or send_telegram(msg):
                if not dry_run:
                    state[sym] = ts
                    save_state(state)
    when = time.strftime("%m-%d %H:%M", time.gmtime(last_candle / 1000)) if last_candle else "n/a"
    heartbeat(f"cycle: {ok} ok, {errs} errors, {found} signal(s) in last {LOOKBACK} candles, "
              f"latest closed candle {when} UTC")


def heartbeat(ok_line):
    print(ok_line, flush=True)


def main():
    args = sys.argv[1:]
    if "--history" in args:  # print past signals, post nothing
        sym = CFG["symbols"][0]
        k = fetch_closed_klines(sym)
        for ts, side, msg in detect(k, sym, price_precision(sym), all_history=True)[-10:]:
            print(time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts / 1000)), side, "\n" + msg + "\n")
        return
    if "--recent" in args:  # list every signal of the last N hours (default 24) on fresh data; posts nothing
        i = args.index("--recent")
        hours = float(args[i + 1]) if len(args) > i + 1 else 24.0
        cutoff = (time.time() - hours * 3600) * 1000
        CFG["symbols"] = resolve_symbols(CFG["symbols"])
        found = []
        for sym in CFG["symbols"]:
            try:
                k = fetch_closed_klines(sym)
            except Exception as e:
                print(f"{sym}: error {e}", file=sys.stderr)
                continue
            found += [(x["ts"], sym, x["side"]) for x in compute_signals(k, all_history=True) if x["ts"] >= cutoff]
        for ts, sym, side in sorted(found):
            print(time.strftime("%m-%d %H:%M", time.gmtime(ts / 1000)), "UTC (candle open)", sym, side)
        print(f"{len(found)} signal(s) in the last {hours:g}h across {len(CFG['symbols'])} coins on {CFG['timeframe']}")
        return
    if "--test-telegram" in args:
        print("sent" if send_telegram("GG-SHOT bot connected ✅") else "failed")
        return
    if not CFG["token"] or not CFG["chat_id"]:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    dry = "--dry-run" in args
    CFG["symbols"] = resolve_symbols(CFG["symbols"])
    tf = TF_SECONDS[CFG["timeframe"]]
    if schedule_text():
        ZoneInfo(CFG["quiet_tz"])  # fail fast on a bad timezone name
    quiet = f", schedule: {schedule_text()}" if schedule_text() else ""
    print(f"Watching {CFG['symbols']} on {CFG['timeframe']} (dry_run={dry}{quiet})")
    while True:
        run_once(dry)
        if "--once" in args:
            return
        now = server_time_ms() / 1000
        time.sleep(max(1, ((now // tf) + 1) * tf + 5 - now))  # wake 5s after next candle close


if __name__ == "__main__":
    main()
