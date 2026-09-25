#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hexagons-tzdata — periodic tzdata refresh (Raspberry Pi)
#
# Installed by deploy/pi-bootstrap.sh to /usr/local/sbin/hexagons-tzdata and
# run by hexagons-tzdata.service, itself triggered by hexagons-tzdata.timer
# (at boot, then weekly) — not a boot-only oneshot: this Pi is a
# permanently-on art installation that may not reboot for months, and a
# tzdata release (e.g. British Columbia's move to permanent DST) can land at
# any point in that uptime. See server/schedule.py for why the app cares.
#
# Root comes from systemd running the unit — unlike hexagons-netcfg, nothing
# here is ever invoked from the unprivileged `pi` process, so there's no
# sudoers rule to install.
#
# Best-effort by design: this Pi frequently has no internet at all (the
# hexagon-ap fallback is an isolated hotspot — see README.md's networking
# section), so apt-get failing here must never fail the unit or hold up
# boot. Always exits 0.
# ---------------------------------------------------------------------------

set -u

log() {
    echo "hexagons-tzdata: $*" | systemd-cat -t hexagons-tzdata -p info
    echo "hexagons-tzdata: $*"
}

before=$(dpkg-query -W -f='${Version}' tzdata 2>/dev/null || echo "")

# -o flags bound how long a network hiccup can hold up the timer's next
# run: Lock::Timeout in case unattended-upgrades is mid-transaction,
# http::Timeout so a half-open connection on a flaky AP uplink doesn't
# stall for the default several minutes.
if ! sudo apt-get -o DPkg::Lock::Timeout=60 -o Acquire::http::Timeout=15 update -qq 2>&1 | systemd-cat -t hexagons-tzdata -p info; then
    log "apt-get update failed (probably no internet right now) — leaving tzdata as-is"
    exit 0
fi

if ! sudo apt-get -o DPkg::Lock::Timeout=60 install -y -qq --only-upgrade tzdata 2>&1 | systemd-cat -t hexagons-tzdata -p info; then
    log "tzdata upgrade failed — leaving tzdata as-is"
    exit 0
fi

after=$(dpkg-query -W -f='${Version}' tzdata 2>/dev/null || echo "")

if [[ "$before" != "$after" ]]; then
    log "tzdata $before -> $after — restarting hexagons.service so it picks up the new rules"
    # try-restart, not restart: a no-op if the service isn't running for
    # some other reason (e.g. mid-deploy), rather than this timer being
    # what brings it up. Best-effort — a failed restart here still isn't
    # fatal to this unit; the next real deploy or reboot picks up the
    # change regardless.
    sudo systemctl try-restart hexagons.service || log "restarting hexagons.service failed — it will pick up the new tzdata on its next restart/reboot regardless"
else
    log "tzdata already up to date ($after)"
fi

exit 0
