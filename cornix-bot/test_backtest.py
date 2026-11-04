"""Offline tests for backtest.py (no network). Run: python test_backtest.py"""
import argparse, random, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot, backtest as bt

bot.CFG.update(entry_step=0.5, tp_mults=[1.5, 3, 5, 9], sl_mult=6.0, all_tps=True)
approx = lambda a, b, tol=1e-4: abs(a - b) < tol

def K(i, o, h, l, c):  # [open_ts, o, h, l, c, vol, close_ts]
    return [i * 300000, o, h, l, c, 0, i * 300000 + 299999]

LONG = {"i": 0, "side": "LONG", "price": 100.0, "atr": 1.0, "tp_atr": 1.0}
SHORT = {"i": 0, "side": "SHORT", "price": 100.0, "atr": 1.0, "tp_atr": 1.0}

# 1. long: entry 1 fills, all four TPs hit (entries 2/3 never fill)
kl = [K(0, 100, 100, 100, 100), K(1, 100, 101.6, 99.9, 101), K(2, 101, 109.5, 100.5, 109)]
t = bt.simulate_trade(kl, LONG, None, 25, 0.0, False)
exp = (1/3) * (0.3*0.015 + 0.3*0.03 + 0.3*0.05 + 0.1*0.09)
assert t["reason"] == "TP" and t["tps_hit"] == 4 and approx(t["pnl_units"], exp), (t, exp)

# 2. long: entry 1 fills and the stop (94) is hit on the same candle -> stop wins
kl = [K(0, 100, 100, 100, 100), K(1, 100, 101.6, 93.0, 95)]
t = bt.simulate_trade(kl, LONG, None, 5, 0.0, False)   # lev 5 -> liquidation far below the stop
# the 93 low also fills entries 2 and 3 -> full size, average entry 99.5
assert t["reason"] == "SL" and t["tps_hit"] == 0 and approx(t["pnl_units"], 94/99.5 - 1), t

# 3. 25x: liquidation (~96.5) comes before the 94 stop
kl = [K(0, 100, 100, 100, 100), K(1, 100, 100.2, 96.0, 97)]
t = bt.simulate_trade(kl, LONG, None, 25, 0.0, False)
# the 96.0 low fills all 3 entries (avg 99.5, liq ~96.02) -> full margin of the full position lost
assert t["reason"] == "LIQUIDATED" and approx(t["pnl_units"], -1.0 / 25), t

# 4. short mirror: TP1 then stop at 106
kl = [K(0, 100, 100, 100, 100), K(1, 100, 100.1, 98.4, 99), K(2, 99, 106.5, 99, 105)]
t = bt.simulate_trade(kl, SHORT, None, 5, 0.0, False)
assert t["tps_hit"] == 1 and t["reason"] == "SL", t
# TP1 closes 0.1 at 98.5; the 106.5 spike then fills entries 2+3 (100.5, 101) and hits the 106 stop
avg2 = (0.2333333333 * 100 + (1/3) * 100.5 + (1/3) * 101) / 0.9
exp = 0.1 * 0.015 + 0.9 * (1 - 106 / avg2)
assert approx(t["pnl_units"], exp, 1e-6), (t["pnl_units"], exp)

# 5. no fill -> no trade;  opposite-signal exit closes at that candle's close
kl = [K(0, 100, 100, 100, 100), K(1, 101, 102, 100.5, 101)]
assert bt.simulate_trade(kl, LONG, None, 25, 0.0, False) is None
kl = [K(0, 100, 100, 100, 100), K(1, 100, 100.5, 99.8, 100.2), K(2, 100.2, 101.0, 100, 101.0)]
t = bt.simulate_trade(kl, LONG, 2, 25, 0.0, False)
assert t["reason"] == "OPPOSITE" and approx(t["pnl_units"], (1/3) * 0.01), t

# 6. breakeven mode: TP1 hit, then price returns to entry -> BE exit
kl = [K(0, 100, 100, 100, 100), K(1, 100, 101.6, 99.9, 101), K(2, 101, 101, 99.5, 100)]
t = bt.simulate_trade(kl, LONG, None, 5, 0.0, True)
assert t["reason"] == "BE" and approx(t["pnl_units"], (1/3) * 0.3 * 0.015), t

# 7. full portfolio run on synthetic multi-coin data
random.seed(11)
def series(seed):
    random.seed(seed); p, ks = 100.0, []
    for i in range(3000):
        o = p; p *= 1 + random.gauss(0, 0.004 if (i // 400) % 2 == 0 else 0.011); c = p
        ks.append(K(i, o, max(o, c) * 1.001, min(o, c) * 0.999, c))
    return ks
data = {f"C{n}USDT": series(n) for n in range(6)}
args = argparse.Namespace(capital=5000, pct=5, lev=25, fee=0.05, max_open=0, close_opposite=True, be=False, days=10)
taken, skipped, eq, dd, conc, curve = bt.run_backtest(data, 0, args)
assert taken and eq > 0 and 0 <= dd <= 1 and conc >= 1
assert all(t["pnl_usd"] >= -t["margin"] - 1e-9 for t in taken)
bt.report(taken, skipped, eq, dd, conc, args)
print("ALL TESTS PASSED")

# 8. sweep runs and restores bot.CFG afterwards
import sweep
before = dict(bot.CFG)
rows = sweep.sweep(data, 0, args, sweep.VARIANTS[:8])
assert len(rows) == 8 and dict(bot.CFG) == before
sweep.print_table(rows, data, 0)
long_only = dict(rows)["LONG only"]; short_only = dict(rows)["SHORT only"]
assert long_only["n"] > 0 and short_only["n"] > 0
print("SWEEP OK")

# 9. entry split 50/25/25, TP split 34/33/33 (3 TPs), SL to entry after TP2
bot.CFG.update(all_tps=True, tp_count=3)
ES, TS = [50, 25, 25], [34, 33, 33]
kl = [K(0, 100, 100, 100, 100), K(1, 100, 101.6, 99.9, 101), K(2, 101, 103.2, 100.2, 103), K(3, 103, 103, 99.8, 100)]
t = bt.simulate_trade(kl, LONG, None, 25, 0.0, 2, ES, TS)
# only entry 1 filled (0.5): TP1 closes 0.17 @101.5, TP2 closes 0.165 @103, rest stopped at entry (100)
assert t["reason"] == "BE" and t["tps_hit"] == 2 and approx(t["filled"], 0.5), t
assert approx(t["pnl_units"], 0.17 * 0.015 + 0.165 * 0.03, 1e-6), t
# BE only after TP2: after TP1 alone a dip to entry does NOT stop the trade
kl = [K(0, 100, 100, 100, 100), K(1, 100, 101.6, 99.9, 101), K(2, 101, 101.0, 99.8, 100.5)]
t = bt.simulate_trade(kl, LONG, None, 25, 0.0, 2, ES, TS)
assert t["reason"] == "END" and t["tps_hit"] == 1, t
assert approx(t["pnl_units"], 0.17 * 0.015 + 0.33 * 0.005, 1e-6), t
# a deep dip fills all three entries at the 50/25/25 weights (avg 99.625)
kl = [K(0, 100, 100, 100, 100), K(1, 100, 100.2, 93.0, 95)]
t = bt.simulate_trade(kl, LONG, None, 5, 0.0, 0, ES, TS)
assert t["reason"] == "SL" and approx(t["pnl_units"], 94 / 99.625 - 1, 1e-6), t
# the live message now lists exactly 3 TPs
msg = bot.build_signal("BTCUSDT", "LONG", 100.0, 1.0, 2)
import re
assert len(re.findall(r"^\d\) ", msg, re.M)) == 3 + 3 + 1, msg  # 3 entries, 3 TPs, 1 stop
bot.CFG.update(all_tps=True, tp_count=4)
print("SPLIT TESTS OK")
