#!/usr/bin/env bash
# Fuses only, no firmware. The granular escape hatch for debugging one unit
# at a time — for normal use, prefer tools/update-unit.sh, which does this
# plus the firmware flash in one pass.
#
# Sets fuse1 (BODCFG) to 2.9V Enabled/Enabled, and fuse5 (SYSCFG0)'s EESAVE
# bit if (and only if) it reads as the one specific "EESAVE off" value we
# know how to fix — see tools/lib.sh's plan_fuses() for the full rule.
# Idempotent: run it again and it reports "already correct", writes nothing.
#
# Never chip-erases, never touches flash/EEPROM/USERROW/lock/RSTPINCFG.
#
# Usage:
#   tools/fuses-fix-bod.sh <port> [--yes] [--force]
#
#   --yes     Skip the confirmation prompt.
#   --force   Proceed even if the unit looks unprovisioned (EEPROM 0x00 is
#             0x00 or 0xFF). Without it, an unprovisioned unit aborts before
#             any write, on the theory that an operator should know that
#             before touching fuses on it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PORT="${1:-}"
AUTO_YES=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --yes) AUTO_YES=1 ;;
    --force) FORCE=1 ;;
  esac
done

if [ -z "$PORT" ]; then
  echo "Usage: $0 <port> [--yes] [--force]" >&2
  exit 2
fi

resolve_toolchain

BACKUP_DIR="$SCRIPT_DIR/backups"

echo "==> Snapshotting EEPROM + USERROW from $PORT before touching fuses ..."
snapshot_nvm "$PORT" "$BACKUP_DIR" "fuses-fix-bod"
echo "    Unit ID: $HEX_ID"
echo "    Backup:  ${SNAPSHOT_PATH}.eeprom.hex / .usersig.hex"

if [ "$HEX_ID" = "00" ] || [ "$HEX_ID" = "FF" ]; then
  if [ "$FORCE" -ne 1 ]; then
    echo "ERROR: unit looks unprovisioned (EEPROM 0x00 = 0x${HEX_ID}). Pass --force to proceed anyway." >&2
    exit 1
  fi
  echo "WARNING: unit is unprovisioned (0x${HEX_ID}); continuing because --force was passed."
fi

plan_fuses "$PORT"

echo
printf 'fuse1 (BODCFG)   current %s   %s\n' "$CUR_FUSE1" "$(decode_fuse1 "$CUR_FUSE1")"
printf 'fuse5 (SYSCFG0)  current %s   %s\n' "$CUR_FUSE5" "$(decode_fuse5 "$CUR_FUSE5")"
echo

if [ "$FUSE5_UNEXPECTED" -eq 1 ]; then
  echo "WARNING: fuse5 is 0x${CUR_FUSE5#0x} — neither the target (${FUSE5_TARGET}) nor the one"
  echo "         known pre-EESAVE value (${FUSE5_PRE_EESAVE}). Leaving fuse5 untouched;"
  echo "         investigate before writing it by hand. (This never touches RSTPINCFG.)"
fi

if [ "$NEED_FUSE1" -eq 0 ] && [ "$NEED_FUSE5" -eq 0 ]; then
  echo "Nothing to do — fuse1 and fuse5 already match target. No write performed."
  exit 0
fi

echo "Planned writes:"
[ "$NEED_FUSE1" -eq 1 ] && echo "  fuse1: $CUR_FUSE1 -> $FUSE1_TARGET"
[ "$NEED_FUSE5" -eq 1 ] && echo "  fuse5: $CUR_FUSE5 -> $FUSE5_TARGET"
echo
echo "No chip erase. Flash/EEPROM/USERROW/lock/RSTPINCFG are not touched."

if [ "$AUTO_YES" -ne 1 ]; then
  read -r -p "Proceed on $PORT (unit $HEX_ID)? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

apply_planned_fuses "$PORT"

AFTER_ID="$(read_eeprom_byte0_hex "$PORT")"
if [ "$AFTER_ID" != "$BEFORE_ID_HEX" ]; then
  echo "FAIL: EEPROM 0x00 changed during fuse write — was ${BEFORE_ID_HEX}, now ${AFTER_ID}. Restore from ${SNAPSHOT_PATH}.eeprom.hex" >&2
  exit 1
fi

FINAL_FUSE5="$FUSE5_TARGET"
[ "$NEED_FUSE5" -eq 1 ] || FINAL_FUSE5="$CUR_FUSE5"
echo "PASS: unit $HEX_ID — fuse1=$FUSE1_TARGET, fuse5=$FINAL_FUSE5, EEPROM unchanged."
[ "$FUSE5_UNEXPECTED" -eq 1 ] && echo "      (fuse5 left as-is — see WARNING above)"
