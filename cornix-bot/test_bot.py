"""Offline sanity test on synthetic candles (no network)."""
import random, bot
random.seed(1)
p, ks = 100.0, []
for i in range(1500):
    o = p; p *= 1 + random.gauss(0, 0.004); c = p
    ks.append([i * 900000, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 0, i * 900000 + 899999])
sigs = bot.detect(ks, "BTCUSDT", 2, all_history=True)
print(len(sigs), "signals;", sum(s[1] == "LONG" for s in sigs), "long")
print(sigs[-1][2])
assert sigs and all(s[1] in ("LONG", "SHORT") for s in sigs)
