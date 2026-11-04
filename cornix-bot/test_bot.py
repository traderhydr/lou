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

# quiet hours
from datetime import datetime, timezone
import bot
bot.CFG["quiet"] = bot.parse_quiet("22:00-06:00,12:00-13:00"); bot.CFG["quiet_tz"] = "UTC"
U = lambda h, m=0: datetime(2026, 11, 4, h, m, tzinfo=timezone.utc)
assert bot.is_quiet(U(23)) and bot.is_quiet(U(5, 59)) and bot.is_quiet(U(0)) and bot.is_quiet(U(22, 0))
assert not bot.is_quiet(U(6, 0)) and not bot.is_quiet(U(11, 59)) and not bot.is_quiet(U(21, 59))
assert bot.is_quiet(U(12, 30)) and not bot.is_quiet(U(13, 0))
bot.CFG["quiet_tz"] = "Asia/Kolkata"          # UTC+5:30: 17:00 UTC = 22:30 local -> quiet; 01:00 UTC = 06:30 -> not
assert bot.is_quiet(U(17)) and not bot.is_quiet(U(1))
bot.CFG["quiet"] = []
assert not bot.is_quiet(U(23))                  # no quiet hours configured -> never quiet
print("QUIET OK")
