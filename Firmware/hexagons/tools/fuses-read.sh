#!/usr/bin/env bash
# Read-only fuse/EEPROM/USERROW audit for a connected hexagon unit.
# Safe to run on any live unit at any time — never writes anything.
#
# Usage:
#   tools/fuses-read.sh <port> [--check]
#
#   --check   Exit non-zero (no other output change) if fuse1 != 0x65 or
#             fuse5 != 0xC5. Useful as a post-fix verifier from another
#             script or CI-style check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PORT="${1:-}"
CHECK=0
for arg in "$@"; do
  [ "$arg" = "--check" ] && CHECK=1
done

if [ -z "$PORT" ]; then
  echo "Usage: $0 <port> [--check]" >&2
  exit 2
fi

resolve_toolchain

echo "==> Reading fuses/EEPROM/USERROW from $PORT ..."

FUSE0="$(read_fuse_hex "$PORT" fuse0)"
FUSE1="$(read_fuse_hex "$PORT" fuse1)"
FUSE2="$(read_fuse_hex "$PORT" fuse2)"
FUSE5="$(read_fuse_hex "$PORT" fuse5)"
LOCK="$(read_fuse_hex "$PORT" lock)"
ID="$(read_eeprom_byte0_hex "$PORT")"
OSCCAL="$(read_usersig_byte0_hex "$PORT")"

echo
printf 'fuse0 (WDTCFG)   %s\n' "$FUSE0"
printf 'fuse1 (BODCFG)   %s   %s\n' "$FUSE1" "$(decode_fuse1 "$FUSE1")"
printf 'fuse2 (OSCCFG)   %s   %s\n' "$FUSE2" "$([ "$FUSE2" = "0x02" ] && echo 'tuned/internal, correct for 10 or 20 MHz internal' || echo 'unexpected — check boards.txt')"
printf 'fuse5 (SYSCFG0)  %s   %s\n' "$FUSE5" "$(decode_fuse5 "$FUSE5")"
printf 'lock             %s\n' "$LOCK"
echo
printf 'Unit ID (EEPROM 0x00)   %s' "$ID"
case "$ID" in
  0x00|0xff) printf '   <-- UNPROVISIONED\n' ;;
  *) printf '\n' ;;
esac
printf 'OSCCAL (USERROW byte0)  %s\n' "$OSCCAL"

echo
printf 'Target: fuse1=%s  fuse5=%s\n' "$FUSE1_TARGET" "$FUSE5_TARGET"

if [ "$CHECK" -eq 1 ]; then
  ok=1
  [ "$FUSE1" = "$FUSE1_TARGET" ] || ok=0
  [ "$FUSE5" = "$FUSE5_TARGET" ] || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "CHECK: PASS — fuses match target"
    exit 0
  else
    echo "CHECK: FAIL — fuses do not match target"
    exit 1
  fi
fi
