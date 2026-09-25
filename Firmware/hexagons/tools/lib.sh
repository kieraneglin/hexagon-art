#!/usr/bin/env bash
# Shared helpers for the tools/ scripts. Sourced, never executed directly.
#
# Single source of truth for the fuse target values and the avrdude
# invocations that read/write them, so fuses-read.sh, fuses-fix-bod.sh, and
# update-unit.sh can't drift apart from each other.
#
# NVM SAFETY: nothing in this file ever passes -e (chip erase) to avrdude,
# and nothing here erases or rewrites flash. Fuse writes touch exactly the
# one byte named (fuses are genuine 1-byte memories — see apply_fuse()).
# EEPROM/USERROW writes (write_eeprom_byte0/write_usersig_byte0) are paged,
# multi-byte memories instead, so those go through a read-modify-write of
# the FULL memory rather than trusting an immediate-mode partial write to
# leave neighboring bytes alone — see write_mem_byte()'s comment for why.
# See AGENTS.md and the plan note "Does any of this erase the chip?" for
# the full reasoning.

if [ -n "${HEX_TOOLS_LIB_SOURCED:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
HEX_TOOLS_LIB_SOURCED=1

# ---------------------------------------------------------------------------
# Target fuse values (ATtiny1616, megaTinyCore 2.6.12 boards.txt)
# ---------------------------------------------------------------------------
#
# fuse1 (BODCFG) = 0b{bodlevbits}{bodmodebits}
#   0x65 = 0b01100101 -> level 011 = 2.9V, mode 00101 = Enabled/Enabled
#
# fuse5 (SYSCFG0) = 0b1100{resetpinbits}0{eesavebit}
#   resetpinbits=01 (UPDI, no reset pin) is fixed by the board — never change.
#   0xc5 = 0b11000101 -> RSTPINCFG=UPDI, EESAVE=1
#   0xc4 = 0b11000100 -> RSTPINCFG=UPDI, EESAVE=0  (the only value we'll fix
#          automatically — anything else means an assumption above is wrong
#          and the script should stop rather than guess)
#
# Lowercase deliberately: read_fuse_hex()/read_eeprom_byte0_hex() always
# return lowercase hex (od -tx1's native case), and these constants are
# compared against those reads with plain string equality in plan_fuses().
# Uppercase here would silently never match — decode_fuse1/5() are immune
# to this (bash arithmetic is case-insensitive for hex literals) but the
# plan/compare logic is not.
FUSE1_TARGET="0x65"
FUSE5_TARGET="0xc5"
FUSE5_PRE_EESAVE="0xc4"

PART="t1616"
PROGRAMMER="jtag2updi"
PROG_BAUD="115200"

# ---------------------------------------------------------------------------
# Toolchain resolution
# ---------------------------------------------------------------------------

# _hex_die: hard, unconditional exit. Reserved for resolve_toolchain, which
# runs once before any hardware is touched — a broken toolchain can't be
# retried per-unit, so failing the whole process there is correct even in
# --loop mode.
_hex_die() {
  echo "ERROR: $*" >&2
  exit 1
}

# _hex_err: soft failure for per-unit operations (reads/writes/snapshots).
# Prints to stderr and returns 1 — never exits — so update-unit.sh's --loop
# can catch a flaky connection on one unit and keep going to the next,
# instead of the whole session dying on the first bad programmer seat.
_hex_err() {
  echo "ERROR: $*" >&2
  return 1
}

resolve_toolchain() {
  local arduino15="$HOME/Library/Arduino15"
  # HEX_AVRDUDE_BIN_OVERRIDE / HEX_AVRDUDE_CONF_OVERRIDE: test-only escape
  # hatch so a fake avrdude can be injected without redirecting $HOME (which
  # would also break arduino-cli's ability to find its real cores/config).
  # Never set outside of tests/tools/test-update-unit.sh.
  AVRDUDE_BIN="${HEX_AVRDUDE_BIN_OVERRIDE:-$arduino15/packages/arduino/tools/avrdude/6.3.0-arduino17/bin/avrdude}"
  # Pinned to 6.3.0-arduino17 deliberately: 8.x is also commonly installed
  # alongside it but uses different avrdude.conf memory naming and would
  # silently mismatch megaTinyCore's conf below.
  AVRDUDE_CONF="${HEX_AVRDUDE_CONF_OVERRIDE:-$arduino15/packages/megaTinyCore/hardware/megaavr/2.6.12/avrdude.conf}"

  [ -x "$AVRDUDE_BIN" ] || _hex_die "avrdude not found at $AVRDUDE_BIN (expected arduino:avrdude 6.3.0-arduino17 — install megaTinyCore + the AVR core via arduino-cli/Boards Manager)"
  [ -f "$AVRDUDE_CONF" ] || _hex_die "megaTinyCore avrdude.conf not found at $AVRDUDE_CONF (expected megaTinyCore 2.6.12 — check the installed version and update this path if it differs)"

  ARDUINO_CLI_BIN="$(command -v arduino-cli || true)"
  [ -n "$ARDUINO_CLI_BIN" ] || _hex_die "arduino-cli not found on PATH"
}

# ---------------------------------------------------------------------------
# avrdude wrapper
# ---------------------------------------------------------------------------

# run_avrdude <port> <extra avrdude args...>
# Always includes: -C conf -p t1616 -c jtag2updi -P port -b 115200
# Never includes -e. avrdude verifies the device signature against -p on
# connect and aborts on a mismatch — that's our "wrong part" guardrail, no
# extra code needed.
run_avrdude() {
  local port="$1"
  shift
  "$AVRDUDE_BIN" -C "$AVRDUDE_CONF" -p "$PART" -c "$PROGRAMMER" \
    -P "$port" -b "$PROG_BAUD" -q -u "$@"
}

# read_fuse_hex <port> <memtype>
# Prints the single-byte fuse value as "0xNN" on stdout. Returns 1 (nothing
# printed) on any failure to reach/read the chip.
read_fuse_hex() {
  local port="$1" mem="$2"
  local tmp
  tmp="$(mktemp)"
  if ! run_avrdude "$port" -U "${mem}:r:${tmp}:r" >/dev/null; then
    rm -f "$tmp"
    _hex_err "could not read ${mem} from ${port} — check the programmer is seated"
    return 1
  fi
  local val
  val="$(od -An -tx1 "$tmp" | tr -d ' \n')"
  rm -f "$tmp"
  if [ -z "$val" ]; then
    _hex_err "read ${mem} but got no data — check the programmer is seated"
    return 1
  fi
  echo "0x${val}"
}

# read_eeprom_byte_hex <port> <offset>
# Prints the EEPROM byte at <offset> (decimal) as "0xNN". Returns 1 (nothing
# printed) on any failure to reach/read the chip.
read_eeprom_byte_hex() {
  local port="$1" offset="$2"
  local tmp
  tmp="$(mktemp)"
  if ! run_avrdude "$port" -U "eeprom:r:${tmp}:r" >/dev/null; then
    rm -f "$tmp"
    _hex_err "could not read eeprom from ${port} — check the programmer is seated"
    return 1
  fi
  local val
  val="$(od -An -tx1 -j "$offset" -N1 "$tmp" | tr -d ' \n')"
  rm -f "$tmp"
  if [ -z "$val" ]; then
    _hex_err "read eeprom but got no data at offset ${offset}"
    return 1
  fi
  echo "0x${val}"
}

# read_usersig_byte_hex <port> <offset>
# Prints the USERROW byte at <offset> (decimal) as "0xNN". Returns 1
# (nothing printed) on any failure to reach/read the chip.
#
# Deliberately NOT read_fuse_hex(), even though the avrdude call looks the
# same: read_fuse_hex dumps every byte the read returns with no length
# limit, which is fine for fuses (genuinely 1 byte each in avrdude.conf) but
# wrong for usersig, which is 32 bytes — an unbounded dump there would
# concatenate all 32 bytes into one garbled value instead of the one byte
# wanted. This mirrors read_eeprom_byte_hex's -N1-limited extraction instead.
read_usersig_byte_hex() {
  local port="$1" offset="$2"
  local tmp
  tmp="$(mktemp)"
  if ! run_avrdude "$port" -U "usersig:r:${tmp}:r" >/dev/null; then
    rm -f "$tmp"
    _hex_err "could not read usersig from ${port} — check the programmer is seated"
    return 1
  fi
  local val
  val="$(od -An -tx1 -j "$offset" -N1 "$tmp" | tr -d ' \n')"
  rm -f "$tmp"
  if [ -z "$val" ]; then
    _hex_err "read usersig but got no data at offset ${offset}"
    return 1
  fi
  echo "0x${val}"
}

# read_usersig_byte0_hex <port>
# Prints USERROW byte 0 (the OSCCAL clock calibration value — see
# clockcal.cpp) as "0xNN". Returns 1 (nothing printed) on any failure to
# reach/read the chip.
read_usersig_byte0_hex() {
  read_usersig_byte_hex "$1" 0
}

# read_eeprom_byte0_hex <port>
# Prints EEPROM address 0x00 (the unit ID) as "0xNN". Returns 1 (nothing
# printed) on any failure to reach/read the chip.
read_eeprom_byte0_hex() {
  read_eeprom_byte_hex "$1" 0
}

# apply_fuse <port> <memtype> <hex value>
# Writes exactly the one byte named. No -e anywhere in this call. Returns 1
# on failure rather than exiting.
apply_fuse() {
  local port="$1" mem="$2" value="$3"
  if ! run_avrdude "$port" -U "${mem}:w:${value}:m"; then
    _hex_err "failed writing ${mem}=${value} to ${port}"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Safe single-byte writes into paged, multi-byte NVM regions (eeprom,
# usersig) — read-modify-write, never an immediate-mode partial write.
# ---------------------------------------------------------------------------
#
# Fuses (fuse0/1/2/.../lock) are genuinely 1-byte memories in avrdude.conf
# (size = 1, no page_size) — apply_fuse()'s "-U fuse1:w:0x65:m" immediate
# write is unambiguous there, nothing else in that memory to disturb.
#
# eeprom and usersig are NOT: per avrdude.conf, eeprom is size=0x100
# (256 bytes) with page_size=0x20 (32-byte pages), and usersig is
# size=page_size=0x20 (32 bytes, one page). A single-byte immediate write
# ("-U eeprom:w:0x1a:m") into either would depend on avrdude's internal
# per-page write algorithm actually merging with existing page content
# rather than blindly erasing-then-writing only the byte given — behavior
# this project has no way to verify without risking exactly the kind of
# corruption it exists to prevent (see the EEPROM corruption root-cause
# session note). So instead: read the ENTIRE declared memory, patch exactly
# the one target byte in that buffer locally, write the WHOLE buffer back.
# Every byte avrdude ends up writing is one we explicitly supplied — nothing
# is left for avrdude to have to guess about or merge on its own.
EEPROM_SIZE=256
USERSIG_SIZE=32

# _read_full_mem_raw <port> <memtype> <size>
# Reads the full named memory into a temp file (raw binary), verifying it's
# exactly <size> bytes. Echoes the temp file path on success — caller must
# rm it. Returns 1 (nothing echoed) on any failure.
_read_full_mem_raw() {
  local port="$1" mem="$2" size="$3"
  local tmp
  tmp="$(mktemp)"
  if ! run_avrdude "$port" -U "${mem}:r:${tmp}:r" >/dev/null; then
    rm -f "$tmp"
    _hex_err "could not read ${mem} from ${port} — check the programmer is seated"
    return 1
  fi
  local got
  got="$(wc -c < "$tmp" | tr -d ' ')"
  if [ "$got" != "$size" ]; then
    rm -f "$tmp"
    _hex_err "${mem} read returned ${got} bytes, expected ${size} — refusing to write with an incomplete picture of the chip"
    return 1
  fi
  echo "$tmp"
}

# write_mem_byte <port> <memtype> <full_size> <offset> <hex value>
# Read-modify-write: reads the full <full_size>-byte memory, patches exactly
# one byte at <offset> (decimal), writes the whole buffer back. No -e
# anywhere in this call. Returns 1 on failure rather than exiting.
write_mem_byte() {
  local port="$1" mem="$2" full_size="$3" offset="$4" value="$5"
  local tmp
  tmp="$(_read_full_mem_raw "$port" "$mem" "$full_size")" || return 1
  local byte
  byte="$(printf '%02x' "$(( value ))")"
  if ! printf "\\x${byte}" | dd of="$tmp" bs=1 seek="$offset" count=1 conv=notrunc status=none 2>/dev/null; then
    rm -f "$tmp"
    _hex_err "could not patch local buffer for ${mem} offset ${offset} — nothing was written to the chip"
    return 1
  fi
  if ! run_avrdude "$port" -U "${mem}:w:${tmp}:r"; then
    rm -f "$tmp"
    _hex_err "failed writing ${mem} (patched at offset ${offset}) to ${port}"
    return 1
  fi
  rm -f "$tmp"
}

# write_eeprom_byte0 <port> <hex value>
# Read-modify-writes EEPROM address 0x00 (the unit ID) via write_mem_byte —
# see the safety note above. Leaves the rest of EEPROM, notably clockcal's
# backup byte at address 0x01, untouched — verify with mem_tail_hex below,
# not just assumed. Returns 1 on failure.
write_eeprom_byte0() {
  write_mem_byte "$1" eeprom "$EEPROM_SIZE" 0 "$2"
}

# write_usersig_byte0 <port> <hex value>
# Read-modify-writes USERROW byte 0 (the OSCCAL clock calibration value —
# see clockcal.cpp) via write_mem_byte. Bytes 1-31 of USERROW are currently
# unused/reserved but are still read back and rewritten unchanged, not
# erased — verify with mem_tail_hex below, not just assumed. Returns 1 on
# failure.
write_usersig_byte0() {
  write_mem_byte "$1" usersig "$USERSIG_SIZE" 0 "$2"
}

# mem_tail_hex <port> <memtype> <full_size> <skip>
# Reads the full memory and prints a hex dump of everything AFTER the first
# <skip> bytes, as one unbroken hex string — for an exact before/after
# comparison around a write_mem_byte call (byte-for-byte, not just spot-
# checking one neighboring byte). Callers use this to confirm the untouched
# region of a paged write actually survived, rather than trusting
# write_mem_byte's read-modify-write logic blindly. Returns 1 (nothing
# printed) on any failure to reach/read the chip.
mem_tail_hex() {
  local port="$1" mem="$2" size="$3" skip="$4"
  local tmp
  tmp="$(_read_full_mem_raw "$port" "$mem" "$size")" || return 1
  local val
  val="$(od -An -tx1 -j "$skip" "$tmp" | tr -d ' \n')"
  rm -f "$tmp"
  echo "$val"
}

# snapshot_nvm <port> <out_dir> <label>
# Dumps eeprom + usersig (USERROW) to timestamped Intel-hex files before any
# write, so a snapshot exists to fall back to. Sets HEX_ID (2 hex chars,
# uppercase, no "0x" — for display/filenames) and BEFORE_ID_HEX (e.g.
# "0xa3", lowercase, as returned by read_eeprom_byte0_hex — for exact
# before/after comparison) to the id found at EEPROM 0x00. Returns 1 on any
# failure to reach the chip or write the backup files, without exiting.
snapshot_nvm() {
  local port="$1" out_dir="$2" label="$3"
  mkdir -p "$out_dir" || { _hex_err "could not create $out_dir"; return 1; }
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  BEFORE_ID_HEX="$(read_eeprom_byte0_hex "$port")" || return 1
  HEX_ID="$(printf '%02X' "$BEFORE_ID_HEX")"  # e.g. "A3", for display/filenames
  local base="${out_dir}/${stamp}-id${HEX_ID}${label:+-$label}"
  if ! run_avrdude "$port" \
    -U "eeprom:r:${base}.eeprom.hex:i" \
    -U "usersig:r:${base}.usersig.hex:i"; then
    _hex_err "snapshot failed for ${port} — refusing to write fuses without a backup"
    return 1
  fi
  SNAPSHOT_PATH="$base"
}

# ---------------------------------------------------------------------------
# Fuse decision logic — the one place fuses-fix-bod.sh and update-unit.sh
# both call, so their write rules cannot drift apart.
# ---------------------------------------------------------------------------

# plan_fuses <port>
# Reads fuse1/fuse5 and decides what (if anything) needs writing. Never
# writes. Sets: CUR_FUSE1, CUR_FUSE5, NEED_FUSE1 (0/1), NEED_FUSE5 (0/1),
# FUSE5_UNEXPECTED (0/1 — set when fuse5 is neither the target nor the one
# pre-EESAVE value we know how to fix; caller should warn and not write it).
# Returns 1 on any failure to reach the chip, without exiting.
plan_fuses() {
  local port="$1"
  CUR_FUSE1="$(read_fuse_hex "$port" fuse1)" || return 1
  CUR_FUSE5="$(read_fuse_hex "$port" fuse5)" || return 1

  NEED_FUSE1=0
  [ "$CUR_FUSE1" = "$FUSE1_TARGET" ] || NEED_FUSE1=1

  NEED_FUSE5=0
  FUSE5_UNEXPECTED=0
  if [ "$CUR_FUSE5" = "$FUSE5_TARGET" ]; then
    NEED_FUSE5=0
  elif [ "$CUR_FUSE5" = "$FUSE5_PRE_EESAVE" ]; then
    NEED_FUSE5=1
  else
    FUSE5_UNEXPECTED=1
  fi
}

# apply_planned_fuses <port>
# Writes whichever of fuse1/fuse5 plan_fuses marked as needed, then reads
# both back and confirms they now match target. Call plan_fuses first.
# Returns 1 on any write/verify failure, without exiting.
apply_planned_fuses() {
  local port="$1"
  if [ "$NEED_FUSE1" -eq 1 ]; then
    apply_fuse "$port" fuse1 "$FUSE1_TARGET" || return 1
  fi
  if [ "$NEED_FUSE5" -eq 1 ]; then
    apply_fuse "$port" fuse5 "$FUSE5_TARGET" || return 1
  fi

  local verify1 verify5
  verify1="$(read_fuse_hex "$port" fuse1)" || return 1
  verify5="$(read_fuse_hex "$port" fuse5)" || return 1
  if [ "$verify1" != "$FUSE1_TARGET" ]; then
    _hex_err "fuse1 write did not take — read back ${verify1}, expected ${FUSE1_TARGET}"
    return 1
  fi
  if [ "$NEED_FUSE5" -eq 1 ] && [ "$verify5" != "$FUSE5_TARGET" ]; then
    _hex_err "fuse5 write did not take — read back ${verify5}, expected ${FUSE5_TARGET}"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Decoding for human-readable output
# ---------------------------------------------------------------------------

decode_fuse1() {
  local v=$(( $1 ))
  local lev=$(( (v >> 5) & 0x07 ))
  local mode=$(( v & 0x1F ))
  local levstr mode_str
  case "$lev" in
    0) levstr="1.8V" ;;
    1) levstr="2.1V" ;;
    2) levstr="2.6V" ;;
    3) levstr="2.9V" ;;
    4) levstr="3.3V" ;;
    5) levstr="3.7V" ;;
    6) levstr="4.0V" ;;
    7) levstr="4.2V" ;;
    *) levstr="unknown" ;;
  esac
  case "$mode" in
    0) mode_str="Disabled/Disabled" ;;
    5) mode_str="Enabled/Enabled" ;;
    *) mode_str="mode 0x$(printf '%02x' "$mode") (see boards.txt menu.bodmode for decode)" ;;
  esac
  printf '%s, %s' "$levstr" "$mode_str"
}

decode_fuse5() {
  local v=$(( $1 ))
  local eesave=$(( v & 0x01 ))
  local rstpin=$(( (v >> 2) & 0x03 ))
  local rstpin_str eesave_str
  case "$rstpin" in
    1) rstpin_str="UPDI (no reset pin)" ;;
    2) rstpin_str="RESET  <-- DANGER, verify this is intended" ;;
    0) rstpin_str="GPIO  <-- DANGER, verify this is intended" ;;
    *) rstpin_str="unknown ($rstpin)" ;;
  esac
  [ "$eesave" -eq 1 ] && eesave_str="EEPROM retained across chip erase" \
                       || eesave_str="EEPROM NOT retained across chip erase"
  printf 'RSTPINCFG=%s, EESAVE=%s' "$rstpin_str" "$eesave_str"
}
