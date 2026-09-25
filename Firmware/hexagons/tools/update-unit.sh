#!/usr/bin/env bash
# The one script to actually run. Fuses + firmware in a single pass:
# checks fuse1/fuse5, fixes only what's wrong, uploads the current firmware,
# and confirms the unit's EEPROM ID survived. Idempotent — running it twice
# in a row on an already-correct unit writes nothing the second time.
#
# Never chip-erases. See tools/lib.sh's header and the plan note "Does any
# of this erase the chip?" for why fuse writes and `arduino-cli upload` are
# both safe for EEPROM/USERROW here.
#
# Usage:
#   tools/update-unit.sh <port> [--yes] [--force] [--skip-fuses] [--skip-backup]
#   tools/update-unit.sh <port> --loop [--force] [--skip-fuses] [--skip-backup]
#
#   --yes          Skip the single-pass confirmation prompt. Implied by
#                  --loop (see below) — not meaningful combined with it.
#   --force        Proceed even if the unit looks unprovisioned (EEPROM 0x00
#                  is 0x00 or 0xFF). Reported as a loud FAIL in --loop mode
#                  without it.
#   --skip-fuses   Skip the fuse check/write entirely — firmware upload only.
#                  Two avrdude round-trips cheaper per unit than the default.
#                  Use once you've already run this (without the flag, or via
#                  fuses-fix-bod.sh) on every unit at least once and just want
#                  to iterate on firmware quickly. The EEPROM snapshot,
#                  unprovisioned guard, and post-upload ID-unchanged check
#                  still run — only the fuse read/write is skipped.
#   --skip-backup  Skip dumping eeprom.hex/usersig.hex to tools/backups/ —
#                  one fewer avrdude round-trip per unit. The unit ID is
#                  still read (needed for the unprovisioned guard and the
#                  post-upload ID-unchanged check, and it's the same read
#                  either way, not an extra one) — only the backup FILE is
#                  skipped, so there's nothing to restore from if something
#                  goes wrong. Combine with --skip-fuses for the fastest
#                  possible firmware-only loop; use once you trust the fuses
#                  are already correct on every unit and just want speed.
#   --loop         Repeatedly prompt "seat the programmer, press ENTER" and
#                  run one pass per keypress, so a helper can plug in unit
#                  after unit without you at the keyboard. The ENTER keypress
#                  *is* the confirmation for that pass — --loop runs each
#                  pass with no further prompt. A failed pass (including a
#                  badly-seated programmer) does not end the loop; it's
#                  logged and the loop asks for the next unit. Press 'q'
#                  instead of ENTER, or Ctrl-C, to stop and print the summary.
set -uo pipefail
# Deliberately not `set -e` at the top level: --loop must survive a failed
# pass. Each pass runs in its own function and is checked explicitly instead.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PORT="${1:-}"
LOOP=0
AUTO_YES=0
FORCE=0
SKIP_FUSES=0
SKIP_BACKUP=0
for arg in "$@"; do
  case "$arg" in
    --loop) LOOP=1 ;;
    --yes) AUTO_YES=1 ;;
    --force) FORCE=1 ;;
    --skip-fuses) SKIP_FUSES=1 ;;
    --skip-backup) SKIP_BACKUP=1 ;;
  esac
done

if [ -z "$PORT" ]; then
  echo "Usage: $0 <port> [--yes] [--force] [--skip-fuses] [--skip-backup]" >&2
  echo "       $0 <port> --loop [--force] [--skip-fuses] [--skip-backup]" >&2
  exit 2
fi

resolve_toolchain

SKETCH_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$SCRIPT_DIR/backups"
LOG_FILE="$BACKUP_DIR/session-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$BACKUP_DIR"

log() {
  echo "$*" | tee -a "$LOG_FILE"
}

# Compiled once, before any hardware is touched — not per unit. A compile
# error aborts here, before the loop, instead of mid-session.
log "==> Compiling (profile ATtiny1616, 10 MHz) ..."
if ! "$ARDUINO_CLI_BIN" compile --profile ATtiny1616 "$SKETCH_DIR/hexagons.ino" >>"$LOG_FILE" 2>&1; then
  log "COMPILE FAILED — see $LOG_FILE. Nothing was touched on any unit."
  exit 1
fi
log "    Compile OK."

# run_pass <port>
# One full unit pass: snapshot -> plan fuses -> write only what's needed ->
# upload firmware -> verify EEPROM 0x00 unchanged. Prints a PASS/FAIL line.
# Returns 0/1; never exits the process (so --loop can continue past it).
run_pass() {
  local port="$1"
  local id

  log ""
  log "==> Pass starting on $port"

  if [ "$SKIP_BACKUP" -eq 1 ]; then
    if ! BEFORE_ID_HEX="$(read_eeprom_byte0_hex "$port" 2>>"$LOG_FILE")"; then
      log "FAIL ($port): could not reach the chip — reseat the programmer and try again."
      return 1
    fi
    HEX_ID="$(printf '%02X' "$BEFORE_ID_HEX")"
    SNAPSHOT_PATH=""
    id="$HEX_ID"
    log "    Unit ID: $id   (no backup taken — --skip-backup)"
  else
    if ! snapshot_nvm "$port" "$BACKUP_DIR" "update-unit" 2>>"$LOG_FILE"; then
      log "FAIL ($port): could not reach the chip — reseat the programmer and try again."
      return 1
    fi
    id="$HEX_ID"
    log "    Unit ID: $id   (backup: ${SNAPSHOT_PATH}.eeprom.hex)"
  fi

  if [ "$BEFORE_ID_HEX" = "0x00" ] || [ "$BEFORE_ID_HEX" = "0xff" ]; then
    if [ "$FORCE" -ne 1 ]; then
      log "FAIL ($id): unit is unprovisioned (EEPROM 0x00 = $BEFORE_ID_HEX). Re-run with --force if that's expected."
      return 1
    fi
    log "    WARNING: unprovisioned unit, continuing because --force was passed."
  fi

  if [ "$SKIP_FUSES" -eq 1 ]; then
    log "    Skipping fuse check (--skip-fuses)."
  else
    if ! plan_fuses "$port" 2>>"$LOG_FILE"; then
      log "FAIL ($id): could not read fuses — reseat the programmer and try again."
      return 1
    fi

    if [ "$FUSE5_UNEXPECTED" -eq 1 ]; then
      log "    WARNING ($id): fuse5=$CUR_FUSE5 is neither target nor the known pre-EESAVE value — leaving fuse5 untouched."
    fi

    if [ "$NEED_FUSE1" -eq 0 ] && [ "$NEED_FUSE5" -eq 0 ]; then
      log "    Fuses already correct (fuse1=$CUR_FUSE1, fuse5=$CUR_FUSE5) — skipping fuse write."
    else
      log "    Writing fuses: $([ "$NEED_FUSE1" -eq 1 ] && echo "fuse1 $CUR_FUSE1->$FUSE1_TARGET ") $([ "$NEED_FUSE5" -eq 1 ] && echo "fuse5 $CUR_FUSE5->$FUSE5_TARGET")"
      if ! apply_planned_fuses "$port" 2>>"$LOG_FILE"; then
        if [ -n "$SNAPSHOT_PATH" ]; then
          log "FAIL ($id): fuse write did not verify — see $LOG_FILE. EEPROM/USERROW backup is safe at ${SNAPSHOT_PATH}."
        else
          log "FAIL ($id): fuse write did not verify — see $LOG_FILE. No backup was taken (--skip-backup)."
        fi
        return 1
      fi
      log "    Fuses confirmed: fuse1=$FUSE1_TARGET fuse5=$([ "$NEED_FUSE5" -eq 1 ] && echo "$FUSE5_TARGET" || echo "$CUR_FUSE5")"
    fi
  fi

  log "    Uploading firmware ..."
  if ! "$ARDUINO_CLI_BIN" upload --profile ATtiny1616 --port "$port" "$SKETCH_DIR/hexagons.ino" >>"$LOG_FILE" 2>&1; then
    log "FAIL ($id): firmware upload failed — see $LOG_FILE."
    return 1
  fi

  local after_id
  if ! after_id="$(read_eeprom_byte0_hex "$port" 2>>"$LOG_FILE")"; then
    log "FAIL ($id): could not re-read EEPROM after upload — reseat and check the unit by hand."
    return 1
  fi
  if [ "$after_id" != "$BEFORE_ID_HEX" ]; then
    if [ -n "$SNAPSHOT_PATH" ]; then
      log "FAIL ($id): EEPROM 0x00 changed during this pass — was $BEFORE_ID_HEX, now $after_id. Restore from ${SNAPSHOT_PATH}.eeprom.hex"
    else
      log "FAIL ($id): EEPROM 0x00 changed during this pass — was $BEFORE_ID_HEX, now $after_id. No backup was taken (--skip-backup) — recover the ID manually via tools/set-unit-id.sh."
    fi
    return 1
  fi

  local fuse_status="fuse1=$FUSE1_TARGET"
  [ "$SKIP_FUSES" -eq 1 ] && fuse_status="fuses skipped"
  log "PASS ($id): $fuse_status, firmware uploaded, EEPROM ID unchanged."
  return 0
}

if [ "$LOOP" -eq 0 ]; then
  if [ "$AUTO_YES" -ne 1 ]; then
    read -r -p "Update unit on $PORT now? [y/N] " reply
    case "$reply" in
      y|Y|yes|YES) ;;
      *) echo "Aborted."; exit 1 ;;
    esac
  fi
  run_pass "$PORT"
  exit $?
fi

# --loop mode -----------------------------------------------------------
PASS_IDS=()
FAIL_IDS=()

# Terminal bell (ASCII BEL) so a helper who isn't watching the screen still
# gets an audible cue that a pass finished, pass or fail. Plain printf to
# stdout: rings the bell on a real terminal, harmless no-op byte if stdout
# is redirected to a file/pipe.
ding() {
  printf '\a'
}

print_summary() {
  log ""
  log "==> Session summary"
  log "    Passed: ${#PASS_IDS[@]}  (${PASS_IDS[*]:-none})"
  log "    Failed: ${#FAIL_IDS[@]}  (${FAIL_IDS[*]:-none})"
  log "    Log: $LOG_FILE"
}

on_interrupt() {
  echo
  print_summary
  exit 130
}
trap on_interrupt INT

echo
echo "Loop mode on port $PORT. Seat the programmer, then press ENTER to run a pass."
echo "Press 'q' then ENTER to stop and see the summary."
echo

while true; do
  read -r -p "> " key || { print_summary; exit 0; }
  case "$key" in
    q|Q) print_summary; exit 0 ;;
  esac

  if run_pass "$PORT"; then
    PASS_IDS+=("$HEX_ID")
  else
    FAIL_IDS+=("${HEX_ID:-??}")
  fi
  ding
  echo
  echo "Next: seat the programmer for the next unit and press ENTER (or 'q' to stop)."
done
