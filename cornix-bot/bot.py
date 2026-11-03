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
    "tp_level": env("TP_LEVEL", 2, int),            # which of TP1..TP4 to use
    "tp_mults": [float(x) for x in env("TP_ATR_MULTS", "1.5,3,5,9").split(",")],
    "entry_step": env("ENTRY_STEP_ATR", 0.5, float),  # spacing between the 3 entries
    "leverage": env("LEVERAGE", 10, int),
    "margin": env("MARGIN_TYPE", "Cross"),
    "base_url": env("EXCHANGE_URL", "https://fapi.binance.com"),
    "state_file": env("STATE_FILE", "state.json"),
    "long": env("LONG_SIDE", "1") == "1",
    "short": env("SHORT_SIDE", "1") == "1",
}

LOOKBACK = 3  # candles re-checked each cycle; state file prevents duplicates

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


def build_signal(symbol, side, price, atr, prec):
    cfg = CFG
    sgn = 1 if side == "LONG" else -1
    entries = [price - sgn * atr * cfg["entry_step"] * k for k in range(3)]
    tp = price + sgn * atr * cfg["tp_mults"][cfg["tp_level"] - 1]
    sl = price - sgn * atr * cfg["sl_mult"]
    f = lambda x: f"{x:.{prec}f}"
    base, quote = (symbol[:-4], symbol[-4:]) if symbol.endswith("USDT") else (symbol[:-3], symbol[-3:])
    return "\n".join([
        f"#{base}/{quote}",
        side,
        "Entry Targets:",
        *[f"{i + 1}) {f(e)}" for i, e in enumerate(entries)],
        "Take-Profit Targets:",
        f"1) {f(tp)}",
        "Stop Targets:",
        f"1) {f(sl)}",
        f"Leverage: {cfg['margin']} ({cfg['leverage']}x)",
    ])


def detect(klines, symbol, prec, all_history=False):
    """klines: closed candles only. Returns list of (open_time_ms, side, message)."""
    h = [float(k[2]) for k in klines]
    l = [float(k[3]) for k in klines]
    c = [float(k[4]) for k in klines]
    longs, shorts = gg_shot(h, l, c, CFG["bb_period"], CFG["bb_dev"])
    atr = atr_series(h, l, c, CFG["atr_period"])
    idx = range(len(c)) if all_history else range(max(0, len(c) - LOOKBACK), len(c))
    out = []
    for i in idx:
        if math.isnan(atr[i]):
            continue
        if longs[i] and CFG["long"]:
            out.append((klines[i][0], "LONG", build_signal(symbol, "LONG", c[i], atr[i], prec)))
        elif shorts[i] and CFG["short"]:
            out.append((klines[i][0], "SHORT", build_signal(symbol, "SHORT", c[i], atr[i], prec)))
    return out


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


def fetch_closed_klines(symbol):
    k = get_json(CFG["base_url"] + "/fapi/v1/klines",
                 {"symbol": symbol, "interval": CFG["timeframe"], "limit": 1500})
    # drop the still-forming candle
    now_ms = int(time.time() * 1000)
    if k and k[-1][6] > now_ms:
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
    for sym in CFG["symbols"]:
        try:
            klines = fetch_closed_klines(sym)
            sigs = detect(klines, sym, price_precision(sym))
        except Exception as e:  # keep the loop alive on network/API errors
            print(f"{sym}: error {e}", file=sys.stderr)
            continue
        for ts, side, msg in sigs:
            if ts <= state.get(sym, 0):
                continue  # already posted (or older than the last posted signal)
            print(f"{sym} {side} signal @ {ts}\n{msg}\n")
            if dry_run or send_telegram(msg):
                if not dry_run:
                    state[sym] = ts
                    save_state(state)


def main():
    args = sys.argv[1:]
    if "--history" in args:  # print past signals, post nothing
        sym = CFG["symbols"][0]
        k = fetch_closed_klines(sym)
        for ts, side, msg in detect(k, sym, price_precision(sym), all_history=True)[-10:]:
            print(time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts / 1000)), side, "\n" + msg + "\n")
        return
    if "--test-telegram" in args:
        print("sent" if send_telegram("GG-SHOT bot connected ✅") else "failed")
        return
    if not CFG["token"] or not CFG["chat_id"]:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    dry = "--dry-run" in args
    CFG["symbols"] = resolve_symbols(CFG["symbols"])
    tf = TF_SECONDS[CFG["timeframe"]]
    print(f"Watching {CFG['symbols']} on {CFG['timeframe']} (dry_run={dry})")
    while True:
        run_once(dry)
        if "--once" in args:
            return
        now = time.time()
        time.sleep(((now // tf) + 1) * tf + 5 - now)  # wake 5s after next candle close


if __name__ == "__main__":
    main()
