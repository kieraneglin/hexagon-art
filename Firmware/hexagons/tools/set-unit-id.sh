#!/usr/bin/env bash
# Set a unit's ID and/or clock calibration (OSCCAL) directly via the UPDI
# programmer.
#
# ID: the complement to the bus `I` command. `I` needs the unit to already
# have *some* valid address to target it at; this needs only physical UPDI
# access, and works regardless of current EEPROM state — including a
# freshly assembled board (EEPROM 0x00 = 0xFF) or a unit corrupted to
# 0x00/0xFF. This replaces the manual avrdude terminal-mode dance in
# Initial Setup.md with something that validates the ID and verifies the
# write.
#
# Clock: sets USERROW byte 0 (the OSCCAL trim clockcal.cpp applies at boot)
# to an exact value, rather than nudging it ±1 like the bus `K` command.
# Useful for recovering a lost calibration or applying one determined
# externally (e.g. via the calibrate_oscillator sketch's own UPDI flash, or
# a previously recorded value) without going through the bus.
#
# Both actions read-modify-write the FULL declared memory (EEPROM: 256
# bytes; USERROW: 32 bytes) rather than trusting an immediate-mode partial
# write to leave neighboring bytes alone — see lib.sh's write_mem_byte() for
# why. No fuses touched, no firmware uploaded, no chip erase either way.
#
# The unit does not need to be power-cycled for this script to work (UPDI
# programming works on a running target through the debug interface,
# independent of the normal reset line), but it DOES need a power cycle
# afterward for the firmware to notice either change: both the unit ID and
# the OSCCAL trim are only read once, at boot.
#
# Usage:
#   tools/set-unit-id.sh <port> [--id <new-id>] [--clock <value>] [--yes]
#
#   --id <new-id>    2 hex chars, 01-FE. 00 and FF are rejected — same range
#                    the firmware's own I command enforces in comm.cpp's
#                    parseLine(), matched deliberately so anything this
#                    script accepts is also something I would accept.
#   --clock <value>  1-2 hex chars, 00-3F (OSCCAL is a 6-bit trim —
#                    clockcal.cpp masks with & 0x3F, so anything above 0x3F
#                    would silently be reinterpreted by the firmware; this
#                    script rejects it outright instead).
#   --yes            Skip the confirmation prompt.
#
# At least one of --id/--clock is required; both may be given together to
# apply both changes in one pass. The current ID and current clock offset
# are always shown, regardless of which one(s) you're changing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PORT="${1:-}"
NEW_ID_RAW=""
NEW_CLOCK_RAW=""
AUTO_YES=0

shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --id) shift; NEW_ID_RAW="${1:-}" ;;
    --clock) shift; NEW_CLOCK_RAW="${1:-}" ;;
    --yes) AUTO_YES=1 ;;
    *) echo "ERROR: unrecognized argument: $1" >&2; exit 2 ;;
  esac
  shift || true
done

if [ -z "$PORT" ] || { [ -z "$NEW_ID_RAW" ] && [ -z "$NEW_CLOCK_RAW" ]; }; then
  echo "Usage: $0 <port> [--id <new-id 01-FE>] [--clock <value 00-3F>] [--yes]" >&2
  exit 2
fi

WANT_ID=0
if [ -n "$NEW_ID_RAW" ]; then
  WANT_ID=1
  if ! [[ "$NEW_ID_RAW" =~ ^[0-9a-fA-F]{2}$ ]]; then
    echo "ERROR: --id must be exactly 2 hex characters (e.g. 0A, 1f, FE)." >&2
    exit 2
  fi
  NEW_ID_DEC=$((16#$NEW_ID_RAW))
  if [ "$NEW_ID_DEC" -eq 0 ] || [ "$NEW_ID_DEC" -eq 255 ]; then
    echo "ERROR: 00 and FF are reserved (unprovisioned / broadcast) — use 01-FE." >&2
    exit 2
  fi
  NEW_ID_DISPLAY="$(printf '%02X' "$NEW_ID_DEC")"        # e.g. "1A" — for humans
  NEW_ID_HEX="0x$(printf '%02x' "$NEW_ID_DEC")"          # e.g. "0x1a" — for exact
                                                          # comparison against
                                                          # read_eeprom_*'s
                                                          # always-lowercase output
fi

WANT_CLOCK=0
if [ -n "$NEW_CLOCK_RAW" ]; then
  WANT_CLOCK=1
  if ! [[ "$NEW_CLOCK_RAW" =~ ^[0-9a-fA-F]{1,2}$ ]]; then
    echo "ERROR: --clock must be 1-2 hex characters (e.g. 0, 1f, 3F)." >&2
    exit 2
  fi
  NEW_CLOCK_DEC=$((16#$NEW_CLOCK_RAW))
  if [ "$NEW_CLOCK_DEC" -gt 63 ]; then
    echo "ERROR: OSCCAL is a 6-bit value — valid range is 00-3F (0-63), got $NEW_CLOCK_RAW." >&2
    exit 2
  fi
  NEW_CLOCK_DISPLAY="$(printf '%02X' "$NEW_CLOCK_DEC")"
  NEW_CLOCK_HEX="0x$(printf '%02x' "$NEW_CLOCK_DEC")"
fi

resolve_toolchain

BACKUP_DIR="$SCRIPT_DIR/backups"

echo "==> Snapshotting EEPROM + USERROW from $PORT before writing ..."
snapshot_nvm "$PORT" "$BACKUP_DIR" "set-unit-id"
echo "    Backup: ${SNAPSHOT_PATH}.eeprom.hex / .usersig.hex"

CURRENT_CLOCK_HEX="$(read_usersig_byte0_hex "$PORT")"
CURRENT_CLOCK_DISPLAY="$(printf '%02X' "$CURRENT_CLOCK_HEX")"

# Always shown, regardless of which one(s) are being changed.
echo "    Current ID:             $HEX_ID"
echo "    Current clock (OSCCAL): $CURRENT_CLOCK_DISPLAY"

DO_ID=0
if [ "$WANT_ID" -eq 1 ]; then
  if [ "$BEFORE_ID_HEX" = "$NEW_ID_HEX" ]; then
    echo "ID already set to $NEW_ID_DISPLAY — nothing to do there."
  else
    DO_ID=1
  fi
fi

DO_CLOCK=0
if [ "$WANT_CLOCK" -eq 1 ]; then
  if [ "$CURRENT_CLOCK_HEX" = "$NEW_CLOCK_HEX" ]; then
    echo "Clock already set to $NEW_CLOCK_DISPLAY — nothing to do there."
  else
    DO_CLOCK=1
  fi
fi

if [ "$DO_ID" -eq 0 ] && [ "$DO_CLOCK" -eq 0 ]; then
  echo "Nothing to do — no write performed."
  exit 0
fi

echo
if [ "$DO_ID" -eq 1 ]; then
  if [ "$BEFORE_ID_HEX" = "0x00" ] || [ "$BEFORE_ID_HEX" = "0xff" ]; then
    echo "Unit is currently unprovisioned (EEPROM 0x00 = $BEFORE_ID_HEX)."
  else
    echo "WARNING: unit currently reports ID $HEX_ID — this will overwrite it."
    echo "         Make sure no other unit on the bus already uses $NEW_ID_DISPLAY."
  fi
  echo "Planned: ID     $HEX_ID -> $NEW_ID_DISPLAY"
fi
if [ "$DO_CLOCK" -eq 1 ]; then
  echo "Planned: Clock  $CURRENT_CLOCK_DISPLAY -> $NEW_CLOCK_DISPLAY"
fi
echo "No fuses touched, no firmware uploaded, no chip erase."

if [ "$AUTO_YES" -ne 1 ]; then
  read -r -p "Proceed on $PORT? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

if [ "$DO_ID" -eq 1 ]; then
  EEPROM_TAIL_BEFORE="$(mem_tail_hex "$PORT" eeprom "$EEPROM_SIZE" 1)"
  write_eeprom_byte0 "$PORT" "$NEW_ID_HEX"

  AFTER_ID="$(read_eeprom_byte0_hex "$PORT")"
  if [ "$AFTER_ID" != "$NEW_ID_HEX" ]; then
    echo "FAIL: EEPROM 0x00 read back as $AFTER_ID, expected $NEW_ID_HEX. Restore from ${SNAPSHOT_PATH}.eeprom.hex" >&2
    exit 1
  fi
  EEPROM_TAIL_AFTER="$(mem_tail_hex "$PORT" eeprom "$EEPROM_SIZE" 1)"
  if [ "$EEPROM_TAIL_AFTER" != "$EEPROM_TAIL_BEFORE" ]; then
    echo "FAIL: EEPROM bytes 0x01-0xFF (includes clockcal's backup byte) changed during this write — restore from ${SNAPSHOT_PATH}.eeprom.hex and investigate." >&2
    exit 1
  fi
  echo "PASS: EEPROM 0x00 is now $NEW_ID_DISPLAY. Rest of EEPROM unchanged."
fi

if [ "$DO_CLOCK" -eq 1 ]; then
  USERSIG_TAIL_BEFORE="$(mem_tail_hex "$PORT" usersig "$USERSIG_SIZE" 1)"
  write_usersig_byte0 "$PORT" "$NEW_CLOCK_HEX"

  AFTER_CLOCK="$(read_usersig_byte0_hex "$PORT")"
  if [ "$AFTER_CLOCK" != "$NEW_CLOCK_HEX" ]; then
    echo "FAIL: USERROW byte 0 read back as $AFTER_CLOCK, expected $NEW_CLOCK_HEX. Restore from ${SNAPSHOT_PATH}.usersig.hex" >&2
    exit 1
  fi
  USERSIG_TAIL_AFTER="$(mem_tail_hex "$PORT" usersig "$USERSIG_SIZE" 1)"
  if [ "$USERSIG_TAIL_AFTER" != "$USERSIG_TAIL_BEFORE" ]; then
    echo "FAIL: USERROW bytes 0x01-0x1F changed during this write — restore from ${SNAPSHOT_PATH}.usersig.hex and investigate." >&2
    exit 1
  fi
  echo "PASS: USERROW byte 0 (OSCCAL) is now $NEW_CLOCK_DISPLAY. Rest of USERROW unchanged."
fi

echo "Power-cycle the unit for the firmware to pick up the change(s)."
