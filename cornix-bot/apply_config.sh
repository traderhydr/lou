#!/usr/bin/env bash
# Brings the two systemd services (ggshot = channel 1, louga-v2 = channel 2) to the agreed configuration.
# Safe to run more than once. Never touches TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID or STATE_FILE.
#   bash ~/lou/cornix-bot/apply_config.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC_DIR="${SERVICE_DIR:-/etc/systemd/system}"
SUDO="${SUDO-sudo}"   # tests set SUDO= to run without root

# 64 coins (original list minus LQTY COTI RUNE INJ, plus 25 crypto coins from the Pine script's presets)
SYMBOLS="BTC,ETH,BNB,ADA,LTC,BCH,ARB,WOO,DOT,XRP,FIL,NEAR,1000SHIB,CRV,1INCH,LDO,ZEC,KNC,GRT,APE,ZIL,BEL,ZRX,PEOPLE,IMX,C98,APT,CTSI,JASMY,DYDX,BLUR,DOGE,TRX,RARE,ENA,SAGA,PYTH,EGLD,API3,QTUM,AUDIO,AXS,BAND,OP,ENS,BAKE,CELR,1000PEPE,CHR,HNT,BLZ,KAVA,ZEN,FLOW,YFI,MATIC,ANKR,TOMO,DASH,SUSHI,SKL,UNFI,FTM,SFP"

GG="$SVC_DIR/ggshot.service"
LV="$SVC_DIR/louga-v2.service"

del()   { $SUDO sed -i -E "/^Environment=($2)=/d" "$1"; }                       # delete lines for these keys
setkv() { del "$1" "$2"; $SUDO sed -i "/^Environment=SYMBOLS=/a Environment=$2=$3" "$1"; }

for f in "$GG" "$LV"; do
  [ -f "$f" ] || { echo "ERROR: $f not found"; exit 1; }
  grep -q "^Environment=SYMBOLS=" "$f" || { echo "ERROR: no SYMBOLS line in $f"; exit 1; }
  $SUDO cp -p "$f" "$f.bak"                                                       # backup (systemd ignores .bak)
  $SUDO sed -i "s|^Environment=SYMBOLS=.*|Environment=SYMBOLS=$SYMBOLS|" "$f"
  # settings that were tried and removed, or that are re-set below
  del "$f" "TP1_PCT|TP1_UNTIL|QUIET_HOURS|QUIET_TZ|QUIET_DAYS|ACTIVE_HOURS|ACTIVE_DAYS|ENTRY_ATR_MULTS|ENTRY_MID_SL|ENTRY_LAST_SHIFT_PCT|ENTRY_STEP_ATR|TP_COUNT"
done

# ggshot (channel 1): 15m, 25x, 3 entries (0 / 1.5 ATR / old 4th moved 0.3% off the SL), 4 TPs, no quiet hours
setkv "$GG" TIMEFRAME 15m
setkv "$GG" LEVERAGE 25
setkv "$GG" ENTRY_ATR_MULTS 0,1.5
setkv "$GG" ENTRY_MID_SL 1
setkv "$GG" ENTRY_LAST_SHIFT_PCT 0.3

# louga-v2 (channel 2): 5m, 3 entries (default ladder), 3 TPs, no quiet hours. Its LEVERAGE is left as it is.
setkv "$LV" TIMEFRAME 5m
setkv "$LV" TP_COUNT 3

if [ "${SKIP_SYSTEMCTL:-0}" != 1 ]; then
  (cd "$DIR" && ./venv/bin/pip install -q -r requirements.txt)
  sudo systemctl daemon-reload
  sudo systemctl restart ggshot louga-v2
  echo "Restarted. Waiting 30s for the first cycle..."
  sleep 30
  for s in ggshot louga-v2; do
    echo; echo "== $s"
    journalctl -u "$s" -n 12 --no-pager | grep -E "WARNING|cycle:|Watching" | sed -E 's/Watching \[[^]]*\]/Watching [...]/'
  done
  for s in ggshot louga-v2; do
    echo; echo "== sample message with $s settings (old BTC signal, NOT posted)"
    (cd "$DIR" && env -i HOME="$HOME" PATH="$PATH" $(grep '^Environment=' "$SVC_DIR/$s.service" | sed 's/^Environment=//' | xargs) \
      SYMBOLS=BTCUSDT ./venv/bin/python bot.py --history | head -18)
  done
fi
echo; echo "DONE"
