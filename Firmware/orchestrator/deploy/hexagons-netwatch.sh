#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# hexagons-netwatch — WiFi/AP fallback decision (Raspberry Pi)
#
# Installed by deploy/pi-bootstrap.sh to /usr/local/sbin/hexagons-netwatch
# and run once at boot by hexagons-netwatch.service.
#
# Policy (see PI_SETUP.md): wait up to WIFI_TIMEOUT seconds for wlan0 to
# associate to one of the known station profiles (one per network in
# boot_credentials.json's wifi_networks, named hexagon-wifi-<ssid>, tried
# in NetworkManager's own autoconnect-priority order). If none shows up in
# time, bring up the hexagon-ap access point instead.
# This is a BOOT-TIME decision only — once in AP mode, this script does
# not retry; the AP stays up until the next reboot, or a manual
#   sudo systemctl restart hexagons-netwatch
# (which re-runs the same wait-then-decide logic on demand).
#
# Config: /etc/hexagons-netwatch.conf, written by pi-bootstrap.sh.
#   WIFI_TIMEOUT=60      seconds to wait for a station connection
#   AP_CONNECTION=hexagon-ap
# ---------------------------------------------------------------------------

set -uo pipefail

CONF=/etc/hexagons-netwatch.conf
WIFI_TIMEOUT=60
AP_CONNECTION=hexagon-ap
# shellcheck disable=SC1090
[[ -f "$CONF" ]] && source "$CONF"

log() {
    echo "hexagons-netwatch: $*" | systemd-cat -t hexagons-netwatch -p info
    echo "hexagons-netwatch: $*"
}

# True if wlan0 is "connected" to a *station* profile (i.e. not the AP
# profile itself — nmcli reports AP mode as "connected" too).
station_connected() {
    local conn
    conn=$(nmcli -t -f DEVICE,STATE,CONNECTION device status 2>/dev/null \
        | awk -F: '$1 == "wlan0" && $2 == "connected" {print $3}')
    [[ -n "$conn" && "$conn" != "$AP_CONNECTION" ]]
}

log "waiting up to ${WIFI_TIMEOUT}s for a known WiFi network..."

elapsed=0
while (( elapsed < WIFI_TIMEOUT )); do
    if station_connected; then
        conn=$(nmcli -t -f DEVICE,CONNECTION device status | awk -F: '$1=="wlan0"{print $2}')
        log "connected to '$conn' after ${elapsed}s — staying on WiFi"
        exit 0
    fi
    sleep 2
    elapsed=$(( elapsed + 2 ))
done

log "no known WiFi network after ${WIFI_TIMEOUT}s — starting AP ($AP_CONNECTION)"
if nmcli con up "$AP_CONNECTION"; then
    log "AP is up. It will stay up until the next reboot or a manual" \
        "'nmcli con down $AP_CONNECTION' / 'systemctl restart hexagons-netwatch'."
else
    log "ERROR: failed to bring up $AP_CONNECTION — check 'nmcli con show' and journalctl."
    exit 1
fi
