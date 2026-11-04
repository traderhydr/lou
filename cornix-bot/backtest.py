#!/usr/bin/env python3
"""Portfolio backtest of the GG-SHOT -> Cornix signals.

Uses the same signal and TP/SL code as the live bot (bot.compute_signals / bot.levels).

Example:
    python backtest.py --days 30 --capital 5000 --pct 5 --lev 25

How trades are simulated (Cornix-like, deliberately conservative):
  * Each signal reserves `pct`% of current equity as MARGIN; notional = margin * leverage.
  * The trade amount is split equally over the 3 entry limit orders; an entry fills when a
    later candle touches its price. Unfilled entries use no margin.
  * TPs close 30/30/30/10 % of the filled position (the script's split) when touched.
  * Inside one candle the order assumed is: entries -> liquidation -> stop-loss -> take-profits,
    so when a candle touches both stop and target, the stop wins.
  * The stop is a market-style fill at the stop price (no slippage modelled beyond fees).
  * A new opposite signal on the same coin closes the open trade at that candle's close
    (Cornix "close on opposite signal"); disable with --no-close-opposite.
  * Liquidation is checked per trade (isolated-style) at avg_entry * (1 -/+ 1/lev + 0.5%).
  * Fees are charged on every fill; funding fees are ignored.
"""
import argparse
import csv
import json
import math
import os
import sys
import time

import bot

TP_SPLIT = [0.30, 0.30, 0.30, 0.10]
MAINT = 0.005  # maintenance margin used for the liquidation estimate


# ------------------------------------------------------------------ data
def fetch_history(symbol, start_ms, end_ms):
    """Paginated klines from start_ms to end_ms (closed candles only)."""
    out, cur = [], start_ms
    tf_ms = bot.TF_SECONDS[bot.CFG["timeframe"]] * 1000
    while cur < end_ms:
        k = bot.get_json(bot.CFG["base_url"] + "/fapi/v1/klines",
                         {"symbol": symbol, "interval": bot.CFG["timeframe"],
                          "startTime": cur, "endTime": end_ms, "limit": 1500})
        if not k:
            break
        out.extend(k)
        cur = k[-1][0] + tf_ms
        time.sleep(0.25)
        if len(k) < 1500:
            break
    now_ms = bot.server_time_ms()
    return [x for x in out if x[6] < now_ms]


def load_data(symbols, days, cache_dir, refresh):
    os.makedirs(cache_dir, exist_ok=True)
    tf = bot.CFG["timeframe"]
    tf_ms = bot.TF_SECONDS[tf] * 1000
    end_ms = int(time.time() * 1000)
    warm = 1500 * tf_ms
    start_ms = end_ms - days * 86400000 - warm
    day = time.strftime("%Y%m%d", time.gmtime())
    data = {}
    for n, sym in enumerate(symbols, 1):
        path = os.path.join(cache_dir, f"{sym}_{tf}_{days}d_{day}.json")
        if os.path.exists(path) and not refresh:
            with open(path) as f:
                data[sym] = json.load(f)
        else:
            try:
                data[sym] = fetch_history(sym, start_ms, end_ms)
            except Exception as e:
                print(f"  {sym}: download failed ({e}), skipped", file=sys.stderr)
                continue
            with open(path, "w") as f:
                json.dump(data[sym], f)
        print(f"  [{n}/{len(symbols)}] {sym}: {len(data[sym])} candles", file=sys.stderr)
    return data, end_ms - days * 86400000


# ------------------------------------------------------------ trade engine
def simulate_trade(kl, sig, exit_idx, lev, fee, be_after_tp1):
    """Simulate one signal on candles after sig['i'].
    exit_idx: index of the candle whose close ends the trade (next opposite signal) or None.
    Returns dict with pnl in units of the FULL notional (1.0 = whole trade size), or None if
    no entry ever filled."""
    side = sig["side"]
    sgn = 1 if side == "LONG" else -1
    entries, tps, sl = bot.levels(side, sig["price"], sig["atr"], sig["tp_atr"])
    n_e = len(entries)
    filled = [False] * n_e
    hit = [False] * len(tps)
    split = TP_SPLIT[:len(tps)] if len(tps) == len(TP_SPLIT) else [1.0]
    qty = 0.0          # open position, in fractions of full notional
    cost = 0.0         # sum(qty_i * price_i) for average entry
    total_filled = 0.0
    pnl = 0.0
    fees = 0.0
    closed_pct = 0.0
    first_fill_i = None
    reason = "open"
    last_i = len(kl) - 1 if exit_idx is None else exit_idx
    closed = False

    def close(q, px, avg):
        nonlocal pnl, fees, qty, cost
        q = min(q, qty)
        if q <= 0:
            return
        pnl += q * ((px / avg) - 1) * sgn
        fees += q * fee * (px / avg)
        qty -= q
        cost -= q * avg

    for k in range(sig["i"] + 1, last_i + 1):
        hi, lo = float(kl[k][2]), float(kl[k][3])
        # 1. entries
        for e in range(n_e):
            if not filled[e] and ((lo <= entries[e]) if sgn == 1 else (hi >= entries[e])):
                filled[e] = True
                q = 1.0 / n_e
                qty += q
                total_filled += q
                cost += q * entries[e]
                fees += q * fee
                if first_fill_i is None:
                    first_fill_i = k
        if qty <= 0:
            continue
        avg = cost / qty
        # 2. liquidation (loses the margin of the open position)
        liq = avg * (1 - sgn * (1 / lev - MAINT))
        if (lo <= liq) if sgn == 1 else (hi >= liq):
            pnl -= qty / lev
            fees += qty * fee
            qty = 0.0
            reason, closed = "LIQUIDATED", True
            last_i = k
            break
        # 3. stop-loss (before TPs on the same candle)
        cur_sl = sl
        if be_after_tp1 and hit[0]:
            cur_sl = avg
        if (lo <= cur_sl) if sgn == 1 else (hi >= cur_sl):
            close(qty, cur_sl, avg)
            reason, closed = ("SL" if cur_sl == sl else "BE"), True
            last_i = k
            break
        # 4. take-profits
        for t in range(len(tps)):
            if not hit[t] and ((hi >= tps[t]) if sgn == 1 else (lo <= tps[t])):
                hit[t] = True
                closed_pct += split[t]
                close(total_filled * split[t], tps[t], avg)
                avg = cost / qty if qty > 1e-12 else avg
        if qty <= 1e-9 or all(hit):
            if qty > 1e-9:
                close(qty, tps[-1], avg)
            reason, closed = "TP", True
            last_i = k
            break
    if first_fill_i is None:
        return None
    if not closed and qty > 1e-9:
        px = float(kl[last_i][4])
        avg = cost / qty
        close(qty, px, avg)
        reason = "OPPOSITE" if exit_idx is not None else "END"
    n_tp = sum(hit)
    return {"open_ts": kl[first_fill_i][0], "close_ts": kl[last_i][6],
            "pnl_units": pnl - fees, "fees_units": fees, "filled": total_filled,
            "reason": reason, "tps_hit": n_tp, "side": side}


# ----------------------------------------------------------- portfolio loop
def run_backtest(data, start_ms, args):
    trades = []
    for sym, kl in data.items():
        sigs = bot.compute_signals(kl, all_history=True)
        for n, s in enumerate(sigs):
            if s["ts"] < start_ms:
                continue
            exit_idx = None
            if args.close_opposite:
                nxt = next((x for x in sigs[n + 1:] if x["side"] != s["side"]), None)
                exit_idx = nxt["i"] if nxt else None
            t = simulate_trade(kl, s, exit_idx, args.lev, args.fee / 100, args.be)
            if t:
                t["symbol"] = sym
                trades.append(t)
    trades.sort(key=lambda t: t["open_ts"])

    equity = args.capital
    peak = equity
    max_dd = 0.0
    open_trades = []   # (close_ts, margin, pnl_usd)
    taken, skipped = [], 0
    max_conc = 0
    curve = [(start_ms, equity)]

    def settle(until_ts):
        nonlocal equity, peak, max_dd
        done = sorted([o for o in open_trades if o[0] <= until_ts], key=lambda o: o[0])
        for o in done:
            open_trades.remove(o)
            equity += o[2]
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0)
            curve.append((o[0], equity))

    for t in trades:
        settle(t["open_ts"])
        if equity <= 0:
            skipped += 1
            continue
        margin = equity * args.pct / 100
        free = equity - sum(o[1] for o in open_trades)
        if margin > free or (args.max_open and len(open_trades) >= args.max_open):
            skipped += 1
            continue
        notional = margin * args.lev
        pnl_usd = t["pnl_units"] * notional
        pnl_usd = max(pnl_usd, -margin)  # a trade cannot lose more than its margin
        t.update(margin=margin, pnl_usd=pnl_usd, ret_pct=pnl_usd / margin * 100)
        open_trades.append((t["close_ts"], margin, pnl_usd))
        max_conc = max(max_conc, len(open_trades))
        taken.append(t)
    settle(10 ** 18)
    return taken, skipped, equity, max_dd, max_conc, curve


def report(taken, skipped, equity, max_dd, max_conc, args):
    wins = [t for t in taken if t["pnl_usd"] > 0]
    losses = [t for t in taken if t["pnl_usd"] <= 0]
    gp = sum(t["pnl_usd"] for t in wins)
    gl = -sum(t["pnl_usd"] for t in losses)
    print("\n================ BACKTEST RESULT ================")
    print(f"Period           : last {args.days} days, {bot.CFG['timeframe']} candles, {len(set(t['symbol'] for t in taken))} coins traded")
    print(f"Capital          : ${args.capital:,.2f}  ->  ${equity:,.2f}  ({(equity / args.capital - 1) * 100:+.1f}%)")
    print(f"Sizing           : {args.pct}% of equity as margin per trade, {args.lev}x leverage, fee {args.fee}%/fill")
    print(f"Trades taken     : {len(taken)}  (skipped for no free margin/limit: {skipped}, peak simultaneous: {max_conc})")
    if taken:
        print(f"Win rate         : {len(wins) / len(taken) * 100:.1f}%  ({len(wins)} wins / {len(losses)} losses)")
        print(f"Profit factor    : {gp / gl:.2f}" if gl > 0 else "Profit factor    : n/a (no losses)")
        print(f"Avg win / loss   : ${gp / len(wins) if wins else 0:,.2f} / -${gl / len(losses) if losses else 0:,.2f}")
        print(f"Max drawdown     : {max_dd * 100:.1f}% (realised equity)")
        print(f"Fees paid (est.) : ~${sum(t['fees_units'] * t['margin'] * args.lev for t in taken):,.2f}")
        reasons = {}
        for t in taken:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print("Exits            : " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))
        for side in ("LONG", "SHORT"):
            ts = [t for t in taken if t["side"] == side]
            if ts:
                print(f"{side:<5} trades     : {len(ts)}, PnL ${sum(t['pnl_usd'] for t in ts):+,.2f}, win rate {sum(t['pnl_usd'] > 0 for t in ts) / len(ts) * 100:.0f}%")
        per = {}
        for t in taken:
            per.setdefault(t["symbol"], 0.0)
            per[t["symbol"]] += t["pnl_usd"]
        best = sorted(per.items(), key=lambda x: -x[1])
        print("Best coins       : " + ", ".join(f"{s} {p:+,.0f}" for s, p in best[:5]))
        print("Worst coins      : " + ", ".join(f"{s} {p:+,.0f}" for s, p in best[-5:]))
    print("=================================================")
    print("Note: backtests ignore funding, slippage on stops and exchange outages; real results will differ.")


def save_csv(taken, path):
    cols = ["symbol", "side", "open_ts", "close_ts", "margin", "pnl_usd", "ret_pct", "reason", "tps_hit", "filled"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "side", "open_utc", "close_utc", "margin_usd", "pnl_usd", "return_on_margin_pct",
                    "exit_reason", "tps_hit", "entries_filled_fraction"])
        for t in taken:
            w.writerow([t["symbol"], t["side"],
                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(t["open_ts"] / 1000)),
                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(t["close_ts"] / 1000)),
                        round(t["margin"], 2), round(t["pnl_usd"], 2), round(t["ret_pct"], 1),
                        t["reason"], t["tps_hit"], round(t["filled"], 2)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--capital", type=float, default=5000)
    ap.add_argument("--pct", type=float, default=5, help="%% of equity used as margin per trade")
    ap.add_argument("--lev", type=int, default=bot.CFG["leverage"], help="leverage (default: LEVERAGE env)")
    ap.add_argument("--fee", type=float, default=0.05, help="fee per fill in %% (default 0.05)")
    ap.add_argument("--max-open", type=int, default=0, help="max simultaneous trades (0 = no limit)")
    ap.add_argument("--no-close-opposite", dest="close_opposite", action="store_false")
    ap.add_argument("--be", action="store_true", help="move SL to entry after TP1")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--refresh", action="store_true", help="re-download data")
    ap.add_argument("--csv", default="trades.csv")
    args = ap.parse_args()

    symbols = bot.resolve_symbols(bot.CFG["symbols"])
    print(f"Downloading {args.days} days of {bot.CFG['timeframe']} data for {len(symbols)} coins...", file=sys.stderr)
    data, start_ms = load_data(symbols, args.days, args.cache, args.refresh)
    taken, skipped, equity, max_dd, max_conc, _ = run_backtest(data, start_ms, args)
    report(taken, skipped, equity, max_dd, max_conc, args)
    if taken:
        save_csv(taken, args.csv)
        print(f"Trade list saved to {args.csv}")


if __name__ == "__main__":
    main()
