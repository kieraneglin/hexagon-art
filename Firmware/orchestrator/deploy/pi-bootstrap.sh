#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# pi-bootstrap.sh — one-time OS bring-up for a fresh Hexagon Orchestrator Pi
#
# Run ON THE PI as `pi`, via `./deploy.sh --bootstrap` from the dev machine
# (which scp's this file plus its companions and credentials, then runs it
# over `ssh -t` so sudo can prompt interactively). Idempotent — safe to
# re-run after a failure or to pick up credential/network-list changes in
# boot_credentials.json.
#
# Does NOT install nginx/Flask/Waitress or the hexagons app itself — that's
# `./deploy.sh --install-service` / `--install-nginx` / `./deploy.sh`,
# run after this script and after the reboot it requires. See PI_SETUP.md
# for the full narrative and gotchas.
#
# Expects, next to this script in /tmp:
#   hexagons-networks.json   (0600) { wifi_networks: [{ssid,password,priority?}, ...],
#                             ssh: {username,password}, ap: {ssid,password} }
#   hexagons-deploy.sudoers
#   hexagons-netwatch.sh
#   hexagons-netwatch.service
#   hexagons-netcfg.py
#   hexagons-netcfg.sudoers
#   hexagons-tzdata.sh
#   hexagons-tzdata.service
#   hexagons-tzdata.timer
# ---------------------------------------------------------------------------

set -uo pipefail

TMP=/tmp
NETWORKS_JSON="$TMP/hexagons-networks.json"
CONFIG_TXT=/boot/firmware/config.txt
CMDLINE_TXT=/boot/firmware/cmdline.txt
STAMP=$(date +%Y%m%d-%H%M%S)
NEED_REBOOT=false

die() { echo "FATAL: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }
step() { echo; echo "── $* ──"; }

[[ -f "$NETWORKS_JSON" ]] || die "$NETWORKS_JSON not found — run this via ./deploy.sh --bootstrap, not directly."

# Validate structure once up front, then pull out the AP fields + optional
# timeout override as shell variables (shlex-quoted so eval is safe even if
# a password contains shell metacharacters). Captured into a var first, not
# eval'd directly off the command substitution — otherwise a validation
# failure (empty stdout) would eval to a no-op and the `|| die` below would
# never fire, since `eval ""` itself exits 0 regardless of python3's status.
PY_OUT=$(python3 - "$NETWORKS_JSON" <<'PYEOF'
import json, shlex, sys
path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception as e:
    print(f"invalid JSON in {path}: {e}", file=sys.stderr)
    sys.exit(1)
nets = d.get("wifi_networks") or []
if not nets:
    print("wifi_networks is empty or missing", file=sys.stderr)
    sys.exit(1)
for i, n in enumerate(nets):
    if not n.get("ssid") or not n.get("password"):
        print(f"wifi_networks[{i}] is missing ssid or password", file=sys.stderr)
        sys.exit(1)
ap = d.get("ap") or {}
if not ap.get("ssid"):
    print("ap.ssid missing", file=sys.stderr)
    sys.exit(1)
print(f"AP_SSID={shlex.quote(ap['ssid'])}")
print(f"WIFI_TIMEOUT={shlex.quote(str(d.get('wifi_timeout_seconds', 60)))}")
PYEOF
) || die "boot_credentials.json failed validation (see above)"
eval "$PY_OUT"

# ---------------------------------------------------------------------------
step "1/10 — base packages"
# ---------------------------------------------------------------------------
# hostapd: NetworkManager shells out to it for AP-mode connections when
# present. Its own standalone hostapd.service gets masked by the package's
# postinst (no /etc/hostapd/hostapd.conf shipped) — that's correct, don't
# unmask it; NM invokes the binary directly, it never runs as its own
# always-on service here.
sudo apt-get update -qq
sudo apt-get install -y -qq python3-serial rsync avahi-daemon iw hostapd \
    || die "apt-get install failed"

# ---------------------------------------------------------------------------
step "2/10 — install directory + scoped deploy sudoers"
# ---------------------------------------------------------------------------
sudo mkdir -p /opt/hexagons
sudo chown pi:pi /opt/hexagons

[[ -f "$TMP/hexagons-deploy.sudoers" ]] || die "$TMP/hexagons-deploy.sudoers missing"
sudo visudo -c -f "$TMP/hexagons-deploy.sudoers" || die "hexagons-deploy.sudoers failed validation"
sudo install -o root -g root -m 440 "$TMP/hexagons-deploy.sudoers" /etc/sudoers.d/hexagons-deploy
echo "  ✓ /etc/sudoers.d/hexagons-deploy installed"

# ---------------------------------------------------------------------------
step "3/10 — UART: hand the PL011 back from Bluetooth, disable serial console"
# ---------------------------------------------------------------------------
sudo cp -n "$CONFIG_TXT" "$CONFIG_TXT.bak-$STAMP"
sudo cp -n "$CMDLINE_TXT" "$CMDLINE_TXT.bak-$STAMP"

grep -q '^\[all\]' "$CONFIG_TXT" || warn "$CONFIG_TXT has no [all] section — appended lines may land in the wrong section, check by hand."

add_config_line() {
    local line="$1"
    if ! grep -qxF "$line" "$CONFIG_TXT"; then
        echo "$line" | sudo tee -a "$CONFIG_TXT" >/dev/null
        echo "  + $line"
        NEED_REBOOT=true
    fi
}

# raspi-config's own toggles first (matches PI_SETUP.md's old procedure;
# no-ops if already set) — enable_uart=1, and remove the login console
# from cmdline.txt.
if [[ "$(sudo raspi-config nonint get_serial_hw)" != "0" ]]; then
    sudo raspi-config nonint do_serial_hw 0
    NEED_REBOOT=true
fi
if [[ "$(sudo raspi-config nonint get_serial_cons)" != "1" ]]; then
    sudo raspi-config nonint do_serial_cons 1
    NEED_REBOOT=true
fi

# The actual fix raspi-config doesn't do: stop Bluetooth claiming the PL011.
add_config_line "dtoverlay=disable-bt"

sudo systemctl disable --now bluetooth.service 2>/dev/null || true
if systemctl list-unit-files 2>/dev/null | grep -q '^hciuart\.service'; then
    sudo systemctl disable --now hciuart.service || true
fi
echo "  ✓ UART config staged (takes effect after reboot)"

# ---------------------------------------------------------------------------
step "4/10 — GPIO fan control"
# ---------------------------------------------------------------------------
# GPIO23 drives a fan (via a transistor/MOSFET switch, not directly off the
# GPIO's ~16mA source limit). Handled by the kernel's own gpio-fan overlay —
# no daemon needed, the kernel polls the SoC thermal zone and toggles the
# pin itself. temp is millicelsius; default 10C hysteresis (fan drops out
# below 40C) is left at its default.
add_config_line "dtoverlay=gpio-fan,gpiopin=23,temp=50000"
echo "  ✓ fan control staged (takes effect after reboot)"

# ---------------------------------------------------------------------------
step "5/10 — USB gadget SSH (dwc2 + g_ether, recovery path over USB)"
# ---------------------------------------------------------------------------
add_config_line "dtoverlay=dwc2"

if ! grep -q 'modules-load=dwc2,g_ether' "$CMDLINE_TXT"; then
    sudo sed -i 's/\brootwait\b/rootwait modules-load=dwc2,g_ether/' "$CMDLINE_TXT"
    grep -q 'modules-load=dwc2,g_ether' "$CMDLINE_TXT" || warn "couldn't insert modules-load into $CMDLINE_TXT — check it by hand (no 'rootwait' token found?)."
    NEED_REBOOT=true
    echo "  + modules-load=dwc2,g_ether"
fi

# usb0 appears once g_ether loads after reboot; the connection profile can
# be created ahead of time, NM binds it when the interface shows up.
sudo nmcli con delete hexagon-usb >/dev/null 2>&1 || true
sudo nmcli con add type ethernet ifname usb0 con-name hexagon-usb \
    autoconnect yes ipv4.method shared >/dev/null \
    || warn "failed to create hexagon-usb NM profile"
echo "  ✓ USB gadget staged — Pi will be reachable at 10.42.0.1 over USB after reboot"

# ---------------------------------------------------------------------------
step "6/10 — WiFi station profiles + AP fallback profile"
# ---------------------------------------------------------------------------
# Belt-and-suspenders cleanup for Pis bootstrapped before the array-of-
# networks rewrite — those hardcoded two profiles named hexagon-home /
# hexagon-shed. Harmless no-op once migrated.
sudo nmcli con delete hexagon-home >/dev/null 2>&1 || true
sudo nmcli con delete hexagon-shed >/dev/null 2>&1 || true

# One con-name per network in boot_credentials.json's wifi_networks array,
# named after its SSID so `nmcli con show` stays self-explanatory no matter
# how many networks are configured. Priority defaults to descending array
# order (first = most preferred) unless a network sets its own.
PRIMARY_CONN=$(python3 - "$NETWORKS_JSON" <<'PYEOF'
import json, subprocess, sys

def sh(*args):
    subprocess.run(["sudo", *args], check=True)

with open(sys.argv[1]) as f:
    creds = json.load(f)

primary = None
for i, net in enumerate(creds["wifi_networks"]):
    name = f"hexagon-wifi-{net['ssid']}"
    priority = net.get("priority", 100 - i * 10)
    subprocess.run(["sudo", "nmcli", "con", "delete", name], capture_output=True)
    sh("nmcli", "con", "add", "type", "wifi", "ifname", "wlan0",
       "con-name", name, "autoconnect", "yes", "ssid", net["ssid"])
    sh("nmcli", "con", "modify", name,
       "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", net["password"],
       "connection.autoconnect-priority", str(priority))
    print(f"  ✓ {name} (priority {priority})", file=sys.stderr)
    if primary is None:
        primary = name

ap = creds["ap"]
subprocess.run(["sudo", "nmcli", "con", "delete", "hexagon-ap"], capture_output=True)
sh("nmcli", "con", "add", "type", "wifi", "ifname", "wlan0",
   "con-name", "hexagon-ap", "autoconnect", "no", "ssid", ap["ssid"])
sh("nmcli", "con", "modify", "hexagon-ap",
   "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
   "ipv4.method", "shared")
print("  ✓ hexagon-ap — OPEN network (see PI_SETUP.md: this Pi's onboard BCM43430", file=sys.stderr)
print("    chip can't do WPA2 in AP mode — key install fails at the driver/firmware", file=sys.stderr)
print("    level, confirmed not fixed by the latest firmware-brcm80211). Autoconnect", file=sys.stderr)
print("    disabled — only hexagons-netwatch brings it up.", file=sys.stderr)

print(primary)
PYEOF
) || die "failed to create WiFi/AP profiles from $NETWORKS_JSON (see above)"
[[ -n "$PRIMARY_CONN" ]] || die "no WiFi networks were created — check wifi_networks in boot_credentials.json"

echo "  Verifying '$PRIMARY_CONN' actually associates before retiring the imager's netplan profile..."
if sudo nmcli con up "$PRIMARY_CONN" >/dev/null 2>&1; then
    echo "  ✓ $PRIMARY_CONN connected"
    OLD_WLAN_CONN=$(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-11-wireless" && $1 ~ /^netplan-wlan0-/ {print $1}')
    if [[ -n "$OLD_WLAN_CONN" ]]; then
        sudo nmcli con delete "$OLD_WLAN_CONN" >/dev/null 2>&1 || true
        echo "  ✓ retired old NM profile: $OLD_WLAN_CONN"
    fi
    WLAN_NETPLAN=$(grep -l 'wifis:' /etc/netplan/*.yaml 2>/dev/null | head -1 || true)
    if [[ -n "$WLAN_NETPLAN" ]]; then
        sudo rm -f "$WLAN_NETPLAN"
        echo "  ✓ removed imager netplan file: $WLAN_NETPLAN"
    fi
else
    warn "'$PRIMARY_CONN' did NOT connect — leaving the imager's netplan WiFi profile in place untouched."
    warn "Check its ssid/password in boot_credentials.json, and 'nmcli con show \"$PRIMARY_CONN\"'."
fi

# ---------------------------------------------------------------------------
step "7/10 — netwatch (boot-time WiFi/AP fallback decision) + netcfg helper"
# ---------------------------------------------------------------------------
for f in hexagons-netwatch.sh hexagons-netwatch.service; do
    [[ -f "$TMP/$f" ]] || die "$TMP/$f missing"
done
sudo install -o root -g root -m 755 "$TMP/hexagons-netwatch.sh" /usr/local/sbin/hexagons-netwatch
sudo install -o root -g root -m 644 "$TMP/hexagons-netwatch.service" /etc/systemd/system/hexagons-netwatch.service
sudo tee /etc/hexagons-netwatch.conf >/dev/null <<CONF
WIFI_TIMEOUT=$WIFI_TIMEOUT
AP_CONNECTION=hexagon-ap
CONF
sudo systemctl daemon-reload
sudo systemctl enable hexagons-netwatch.service
echo "  ✓ hexagons-netwatch installed and enabled (timeout ${WIFI_TIMEOUT}s)"

# Runtime "add a WiFi network" tool the app itself calls into (Tools page,
# server/routes/network.py) — see README.md's networking section. The
# helper's own scoped sudoers file (separate from hexagons-deploy.sudoers,
# which is dev-machine-only) is what lets the unprivileged `pi` user the
# app runs as create a NetworkManager profile and reboot.
for f in hexagons-netcfg.py hexagons-netcfg.sudoers; do
    [[ -f "$TMP/$f" ]] || die "$TMP/$f missing"
done
sudo install -o root -g root -m 755 "$TMP/hexagons-netcfg.py" /usr/local/sbin/hexagons-netcfg
sudo visudo -c -f "$TMP/hexagons-netcfg.sudoers" || die "hexagons-netcfg.sudoers failed validation"
sudo install -o root -g root -m 440 "$TMP/hexagons-netcfg.sudoers" /etc/sudoers.d/hexagons-netcfg
echo "  ✓ hexagons-netcfg installed (/usr/local/sbin/hexagons-netcfg + /etc/sudoers.d/hexagons-netcfg)"

# ---------------------------------------------------------------------------
step "8/10 — tzdata periodic refresh"
# ---------------------------------------------------------------------------
# Root comes from systemd running the timer/service, not from the app's own
# unprivileged process — no sudoers rule needed here, unlike hexagons-netcfg
# above. Timer, not a boot-only oneshot (like hexagons-netwatch): this Pi is
# a permanently-on installation that may not reboot for months, and a
# tzdata release can land mid-uptime — see deploy/hexagons-tzdata.timer.
for f in hexagons-tzdata.sh hexagons-tzdata.service hexagons-tzdata.timer; do
    [[ -f "$TMP/$f" ]] || die "$TMP/$f missing"
done
sudo install -o root -g root -m 755 "$TMP/hexagons-tzdata.sh" /usr/local/sbin/hexagons-tzdata
sudo install -o root -g root -m 644 "$TMP/hexagons-tzdata.service" /etc/systemd/system/hexagons-tzdata.service
sudo install -o root -g root -m 644 "$TMP/hexagons-tzdata.timer" /etc/systemd/system/hexagons-tzdata.timer
sudo systemctl daemon-reload
sudo systemctl enable --now hexagons-tzdata.timer
echo "  ✓ hexagons-tzdata installed and enabled (runs at boot, then weekly)"

# ---------------------------------------------------------------------------
step "9/10 — Tailscale (installed + running, NOT authenticated — that's on you)"
# ---------------------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
    if curl -fsSL https://tailscale.com/install.sh | sudo sh; then
        echo "  ✓ tailscale installed"
    else
        warn "tailscale install.sh failed — this Pi is armv6l (Zero W), which may not have a"
        warn "current apt package. Fall back to the static 'arm' binary from"
        warn "https://pkgs.tailscale.com/stable/#static and a hand-written systemd unit;"
        warn "see PI_SETUP.md. Continuing with the rest of bootstrap."
    fi
else
    echo "  ✓ tailscale already installed ($(tailscale --version | head -1))"
fi
if command -v tailscaled >/dev/null 2>&1 || systemctl list-unit-files 2>/dev/null | grep -q '^tailscaled\.service'; then
    sudo systemctl enable --now tailscaled
    echo "  ✓ tailscaled enabled at boot (run 'sudo tailscale up' yourself to authenticate)"
else
    warn "tailscaled not present — skipping enable. See warning above."
fi

# ---------------------------------------------------------------------------
step "10/10 — summary"
# ---------------------------------------------------------------------------
shred -u "$NETWORKS_JSON" 2>/dev/null || rm -f "$NETWORKS_JSON"
rm -f "$TMP/hexagons-deploy.sudoers" "$TMP/hexagons-netwatch.sh" "$TMP/hexagons-netwatch.service" \
      "$TMP/hexagons-netcfg.py" "$TMP/hexagons-netcfg.sudoers" \
      "$TMP/hexagons-tzdata.sh" "$TMP/hexagons-tzdata.service" "$TMP/hexagons-tzdata.timer"

echo
echo "══ Bootstrap done ══"
echo "  UART, USB gadget, WiFi/AP, netwatch, tzdata timer, tailscaled: staged."
echo "  Next: nginx + the app are NOT part of this script — from your dev machine run"
echo "    ./deploy.sh --install-service && ./deploy.sh --install-nginx && ./deploy.sh"
if $NEED_REBOOT; then
    echo
    echo "  A REBOOT IS REQUIRED for the UART/USB gadget config.txt + cmdline.txt changes to take effect."
    echo "  Run: ssh pi@hexagons.local 'sudo reboot'"
fi
