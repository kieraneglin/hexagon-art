#!/usr/bin/env python3
"""
hexagons-netcfg — runtime WiFi provisioning helper (Raspberry Pi)

Installed by deploy/pi-bootstrap.sh to /usr/local/sbin/hexagons-netcfg
(root:root, 0755). Backs server/routes/network.py's on-Pi "add a WiFi
network" tool (Tools page's WiFi section) — see AGENTS.md.

`scan` and `list` are read-only and run unprivileged, straight as `pi` —
nmcli's own read operations need no privilege. `add`, `forget`, and
`reboot` mutate NetworkManager state or reboot the box, so they're gated
by /etc/sudoers.d/hexagons-netcfg, which permits only these three literal
invocations — no wildcards. Every piece of user input (SSID, password,
priority) arrives as JSON on stdin rather than argv, both to keep the
password out of `ps` and because sudoers matches literally on the whole
argument list, so a wildcarded `nmcli ... *` sudo rule was the alternative
this avoids. server/routes/network.py always calls the three privileged
verbs via `sudo -n hexagons-netcfg <verb>`.

`add` replicates the exact nmcli recipe deploy/pi-bootstrap.sh step 6 uses
for boot_credentials.json's wifi_networks[] (con-name hexagon-wifi-<ssid>,
wifi-sec.key-mgmt wpa-psk) — keep the two in sync if you touch one. Unlike
that script, this one is reachable from JSON any pi user typed into the
Tools page — nmcli is invoked via subprocess with argv lists (no shell),
so there's no shell-injection surface, but validate here anyway rather
than trusting server/routes/network.py's own checks are the only line of
defense.

Usage:
    hexagons-netcfg scan
    hexagons-netcfg list
    echo '{"ssid": "...", "password": "...", "priority": 100}' | hexagons-netcfg add
    echo '{"ssid": "..."}' | hexagons-netcfg forget
    hexagons-netcfg reboot

Every subcommand prints exactly one JSON value on stdout and exits 0 on
success; a failure prints {"error": "..."} on stdout (not stderr) and
exits 1 — server/routes/network.py parses stdout as JSON regardless of
exit code, never a Python traceback.
"""

import json
import os
import subprocess
import sys

CON_PREFIX = "hexagon-wifi-"
PROTECTED_CONNECTIONS = {"hexagon-ap", "hexagon-usb"}


def fail(message):
    print(json.dumps({"error": message}))
    sys.exit(1)


def require_root(cmd_name):
    # Defense in depth, not the actual enforcement — that's
    # /etc/sudoers.d/hexagons-netcfg, which is the only thing that lets
    # `pi` reach these three verbs at all. This just turns a misconfigured
    # sudoers/polkit setup into a clear message instead of an opaque
    # NetworkManager D-Bus permission denial.
    if os.geteuid() != 0:
        fail(f"{cmd_name} requires root — invoke via 'sudo -n hexagons-netcfg {cmd_name}'")


def nmcli(*args, check=True):
    result = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "nmcli failed").strip())
    return result


def unescape_nmcli_field(raw):
    # nmcli -t escapes literal ":" and "\" inside field values as "\:"/
    # "\\" — undo that so a colon in an SSID (or a security string like
    # "WPA2 802.1X") doesn't get read as a field separator.
    out = []
    escaped = False
    for ch in raw:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    return "".join(out)


def split_nmcli_line(line, n):
    """Split one nmcli -t line into at most n fields, honouring its \\:
    escaping — the last field may itself contain further real ":" (e.g. a
    SECURITY value), so this stops splitting once n-1 separators are seen."""
    fields, buf, escaped = [], "", False
    for ch in line:
        if escaped:
            buf += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":" and len(fields) < n - 1:
            fields.append(unescape_nmcli_field(buf))
            buf = ""
        else:
            buf += ch
    fields.append(unescape_nmcli_field(buf))
    while len(fields) < n:
        fields.append("")
    return fields


def cmd_scan():
    # --rescan auto reuses a recent scan if nmcli already has one, rather
    # than forcing a fresh one on every call — a few seconds on this
    # hardware's chip, and unnecessary noise on a network that isn't
    # changing every second. In AP mode wlan0 can't scan at all (see
    # PI_SETUP.md §6) — this then just returns whatever nmcli cached from
    # before the AP came up, which may be empty; the caller treats an
    # empty result as "type the SSID by hand", not an error.
    result = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto", check=False)
    best = {}
    for line in result.stdout.splitlines():
        if not line:
            continue
        ssid, signal, security = split_nmcli_line(line, 3)
        if not ssid:
            continue  # hidden network — no SSID to pre-fill with
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        # Multiple BSSIDs can advertise the same SSID (mesh/multi-AP) —
        # collapse to the strongest signal seen rather than listing it
        # more than once.
        prev = best.get(ssid)
        if prev is None or signal > prev["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal, "secure": security not in ("", "--")}
    print(json.dumps(sorted(best.values(), key=lambda n: -n["signal"])))


def cmd_list():
    status = nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status", check=False)
    active = None
    for line in status.stdout.splitlines():
        if not line:
            continue
        device, state, conn = split_nmcli_line(line, 3)
        if device == "wlan0" and state == "connected":
            active = conn

    shown = nmcli("-t", "-f", "NAME,TYPE", "con", "show", check=False)
    saved = []
    for line in shown.stdout.splitlines():
        if not line:
            continue
        name, typ = split_nmcli_line(line, 2)
        if not name.startswith(CON_PREFIX) or typ != "802-11-wireless":
            continue
        priority = 0
        prio_result = nmcli("-t", "-f", "connection.autoconnect-priority", "con", "show", name, check=False)
        for prio_line in prio_result.stdout.splitlines():
            _, _, value = prio_line.partition(":")
            value = value.strip()
            if value.lstrip("-").isdigit():
                priority = int(value)
        saved.append({"ssid": name[len(CON_PREFIX) :], "priority": priority, "active": name == active})
    print(json.dumps(saved))


def cmd_add():
    require_root("add")
    if sys.stdin.isatty():
        fail("add requires a JSON body on stdin")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail("invalid JSON on stdin")

    ssid = (payload.get("ssid") or "").strip()
    if not ssid:
        fail("ssid is required")
    password = payload.get("password") or ""
    if password and len(password) < 8:
        # WPA2-PSK's own floor — nmcli/wpa_supplicant would reject it
        # later anyway, but failing here gives a real message instead of
        # an opaque nmcli stderr blob.
        fail("password must be at least 8 characters (or empty for an open network)")
    try:
        priority = int(payload.get("priority", 100))
    except (TypeError, ValueError):
        fail("priority must be an integer")

    name = CON_PREFIX + ssid
    nmcli("con", "delete", name, check=False)  # delete-then-recreate, same as pi-bootstrap.sh step 6
    try:
        nmcli("con", "add", "type", "wifi", "ifname", "wlan0", "con-name", name, "autoconnect", "yes", "ssid", ssid)
        if password:
            nmcli(
                "con",
                "modify",
                name,
                "wifi-sec.key-mgmt",
                "wpa-psk",
                "wifi-sec.psk",
                password,
                "connection.autoconnect-priority",
                str(priority),
            )
        else:
            nmcli("con", "modify", name, "connection.autoconnect-priority", str(priority))
    except RuntimeError as exc:
        nmcli("con", "delete", name, check=False)  # don't leave a half-configured profile behind
        fail(str(exc))

    print(json.dumps({"ok": True, "ssid": ssid}))


def cmd_forget():
    require_root("forget")
    if sys.stdin.isatty():
        fail("forget requires a JSON body on stdin")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail("invalid JSON on stdin")

    ssid = (payload.get("ssid") or "").strip()
    if not ssid:
        fail("ssid is required")
    name = CON_PREFIX + ssid
    if name in PROTECTED_CONNECTIONS:
        fail(f"refusing to delete protected connection {name}")
    try:
        nmcli("con", "delete", name)
    except RuntimeError as exc:
        fail(str(exc))
    print(json.dumps({"ok": True, "ssid": ssid}))


def cmd_reboot():
    require_root("reboot")
    # Respond before rebooting: print+flush now, then hand the actual
    # `systemctl reboot` to a detached grandchild that sleeps first, so
    # this process (and its caller, sudo -n, and server/routes/network.py's
    # subprocess.run) all exit normally and the HTTP response actually
    # reaches the browser before the box goes down. hexagons.service has
    # Restart=always, so there's nothing here that needs a graceful stop.
    print(json.dumps({"ok": True, "rebooting": True}))
    sys.stdout.flush()
    subprocess.Popen(
        ["sh", "-c", "sleep 3 && systemctl reboot"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


COMMANDS = {
    "scan": cmd_scan,
    "list": cmd_list,
    "add": cmd_add,
    "forget": cmd_forget,
    "reboot": cmd_reboot,
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        fail(f"usage: hexagons-netcfg <{'|'.join(COMMANDS)}>")
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
