#!/usr/bin/env python3
"""Run the backtest for many setting variants on the same downloaded data.

    python sweep.py --days 30            # uses cached data from backtest.py (downloads if missing)

Each row changes ONE thing from the baseline so you can see what helps.
"""
import argparse
import copy
import sys

import backtest as bt
import bot

# (name, bot.CFG overrides, backtest-arg overrides)
VARIANTS = [
    ("baseline (as live)", {}, {}),
    ("fee 0.03% (mostly maker)", {}, {"fee": 0.03}),
    ("fee 0.00% (no fees)", {}, {"fee": 0.0}),
    ("leverage 10x", {}, {"lev": 10}),
    ("leverage 5x", {}, {"lev": 5}),
    ("LONG only", {"short": False}, {}),
    ("SHORT only", {"long": False}, {}),
    ("short ATR = current (not script)", {"script_short_atr": False}, {}),
    ("TP1 fixed 0.1%", {}, {"tp1_pct": 0.1}),
    ("single TP2 (3 ATR)", {"all_tps": False, "tp_level": 2}, {}),
    ("single TP3 (5 ATR)", {"all_tps": False, "tp_level": 3}, {}),
    ("SL 3 ATR", {"sl_mult": 3.0}, {}),
    ("move SL to entry after TP1", {}, {"be": True}),
    ("NO move-to-entry", {}, {"be": False}),
    ("no close on opposite signal", {}, {"close_opposite": False}),
    ("BB 150 / 3.1 (fewer signals)", {"bb_period": 150, "bb_dev": 3.1}, {}),
    ("BB 300 / 2.0 (fewer signals)", {"bb_period": 300, "bb_dev": 2.0}, {}),
]


def stats(taken, equity, max_dd, args):
    n = len(taken)
    wins = [t for t in taken if t["pnl_usd"] > 0]
    gp = sum(t["pnl_usd"] for t in wins)
    gl = -sum(t["pnl_usd"] for t in taken if t["pnl_usd"] <= 0)
    return {"eq": equity, "ret": (equity / args.capital - 1) * 100, "n": n,
            "wr": len(wins) / n * 100 if n else 0, "pf": gp / gl if gl > 0 else float("nan"),
            "dd": max_dd * 100}


def market_change(data, start_ms):
    ch = []
    for sym, kl in data.items():
        px = [(k[0], float(k[4])) for k in kl]
        first = next((p for t, p in px if t >= start_ms), None)
        if first:
            ch.append((px[-1][1] / first - 1) * 100)
    return sum(ch) / len(ch) if ch else float("nan"), data.get("BTCUSDT") and (
        float(data["BTCUSDT"][-1][4]) / next(float(k[4]) for k in data["BTCUSDT"] if k[0] >= start_ms) - 1) * 100


def sweep(data, start_ms, base_args, variants=VARIANTS):
    cfg0 = copy.deepcopy(bot.CFG)
    cache, rows = {}, []
    try:
        for name, cfg_over, arg_over in variants:
            bot.CFG.update(copy.deepcopy(cfg0))
            bot.CFG.update(cfg_over)
            args = argparse.Namespace(**{**vars(base_args), **arg_over})
            taken, skipped, eq, dd, conc, _ = bt.run_backtest(data, start_ms, args, cache)
            r = stats(taken, eq, dd, args)
            rows.append((name, r))
            print(f"  done: {name}", file=sys.stderr)
    finally:
        bot.CFG.clear()
        bot.CFG.update(cfg0)
    return rows


def print_table(rows, data, start_ms):
    print(f"\n{'variant':<36}{'final $':>10}{'return':>9}{'trades':>8}{'win%':>7}{'PF':>6}{'maxDD':>8}")
    print("-" * 84)
    for name, r in rows:
        print(f"{name:<36}{r['eq']:>10,.0f}{r['ret']:>8.0f}%{r['n']:>8}{r['wr']:>6.0f}%{r['pf']:>6.2f}{r['dd']:>7.0f}%")
    avg, btc = market_change(data, start_ms)
    print(f"\nMarket over the period: average coin {avg:+.1f}%" + (f", BTC {btc:+.1f}%" if btc is not None else ""))
    print("Read it like this: LONG-only vs SHORT-only vs that market move shows whether results just follow the trend.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--capital", type=float, default=5000)
    ap.add_argument("--pct", type=float, default=5)
    ap.add_argument("--lev", type=int, default=bot.CFG["leverage"])
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--max-open", type=int, default=0)
    ap.add_argument("--be", action="store_true", help="baseline moves SL to entry after TP1")
    ap.add_argument("--be-after", type=int, default=0, help="baseline moves SL to entry after TP number N")
    ap.add_argument("--entry-split", type=bt.parse_split, default=None)
    ap.add_argument("--tp-split", type=bt.parse_split, default=None)
    ap.add_argument("--no-close-opposite", dest="close_opposite", action="store_false",
                    help="allow hedged positions (hedge mode ON)")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    bt.apply_split_cfg(args)
    symbols = bot.resolve_symbols(bot.CFG["symbols"])
    data, start_ms = bt.load_data(symbols, args.days, args.cache, args.refresh)
    print_table(sweep(data, start_ms, args), data, start_ms)


if __name__ == "__main__":
    main()
