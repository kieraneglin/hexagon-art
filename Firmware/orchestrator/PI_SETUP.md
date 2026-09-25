# Raspberry Pi Setup Log — Hexagon Orchestrator

A record of exactly what it takes to turn a blank Raspberry Pi Zero W into a
working orchestrator host, so this can be redone from scratch if the SD card
ever needs to be re-flashed. Written 2026-08-19 while bringing up a
replacement Pi after the previous one died.

This is a **log of what was actually done, including the dead ends** —
`README.md`'s "First-Time Setup" is the clean, current instructions; this
file is the "why" and "what broke" behind them, plus the one-time OS
bring-up script (`deploy/pi-bootstrap.sh`, run via `./deploy.sh --bootstrap`)
that now does most of this automatically.

Hardware: Raspberry Pi Zero W, Rev 1.1 (onboard **BCM43430** WiFi/BT combo
chip, `brcmfmac` driver — this specific chip's AP-mode limitation is the
whole reason §6 below exists). OS: Raspberry Pi OS Lite (Raspbian 13
"trixie", 32-bit `armhf`), flashed via Raspberry Pi Imager with hostname
`hexagons`, SSH enabled (password auth, since `--bootstrap` needs
`ssh-copy-id` to already have run — see §1), WiFi pre-configured with at
least one network so first boot can reach the internet for `apt`.

---

## 0. What `./deploy.sh --bootstrap` actually does

`deploy/pi-bootstrap.sh` (scp'd over and run via `ssh -t` so `sudo` can
prompt interactively) is idempotent — safe to re-run after a failure, after
changing `boot_credentials.json`, or to add/remove WiFi networks. It:

1. Installs `python3-serial`, `rsync`, `avahi-daemon`, `iw`, `hostapd`.
2. Creates `/opt/hexagons` (owned `pi`) and installs the scoped deploy
   sudoers rule (`deploy/hexagons-deploy.sudoers`).
3. Hands the PL011 UART back from Bluetooth, disables the serial console.
4. Stages GPIO fan control (`dtoverlay=gpio-fan,gpiopin=23,temp=50000`) — see §2a.
5. Stages USB gadget SSH (`dtoverlay=dwc2` + `g_ether`).
6. Creates one NetworkManager profile per network in
   `boot_credentials.json`'s `wifi_networks` array (named
   `hexagon-wifi-<ssid>`), plus the open `hexagon-ap` fallback profile —
   and retires the Imager's own netplan-generated WiFi profile once the
   primary network is confirmed to connect.
7. Installs and enables `hexagons-netwatch.service`, plus the
   `hexagons-netcfg` runtime WiFi provisioning helper and its own scoped
   sudoers rule (see §6).
8. Installs and enables `hexagons-tzdata.timer` — refreshes `tzdata` at
   boot and weekly thereafter, best-effort (see §6a). No sudoers rule:
   unlike `hexagons-netcfg`, systemd runs this as root directly, nothing
   unprivileged calls into it.
9. Installs Tailscale and enables `tailscaled` (not authenticated — see §7).
10. Prints a summary and tells you if a reboot is required (it is, the
    first time — UART/USB-gadget/fan config only takes effect after one).

It does **not** install nginx/Flask/Waitress or the app itself — that's
`./deploy.sh --install-service && ./deploy.sh --install-nginx && ./deploy.sh`,
run after bootstrap and after the reboot it requires.

`boot_credentials.json` (gitignored, repo root) is the input:

```json
{
  "wifi_networks": [
    { "ssid": "...", "password": "...", "priority": 100 },
    { "ssid": "...", "password": "..." }
  ],
  "ssh": { "username": "pi", "password": "..." },
  "ap": { "ssid": "hexagon-ap" }
}
```

- `priority` is optional — higher wins; defaults to descending array order
  (first entry = most preferred) if omitted.
- `ssh` is for your own reference only — `ssh-copy-id` still has to be run
  by hand first (§1); nothing in the scripts reads this field.
- `ap` has no password — see §6, this hardware can't do WPA2 in AP mode.

---

## 1. First boot & SSH access

Imaged with Raspberry Pi Imager's advanced options: hostname `hexagons`, SSH
enabled with password auth, WiFi credentials pre-filled. Booted headless —
no monitor/keyboard ever needed.

```bash
ssh-copy-id pi@hexagons.local
```

After that, `ssh pi@hexagons.local` works with no password — every
subsequent step, including `--bootstrap`, assumes this.

**Gotcha:** if you set SSH to "public-key only" in the Imager and don't
paste a key in at image time, you'll need physical/serial access to add one
later — password auth has to be available for `ssh-copy-id` to work after
the fact.

**Gotcha:** `sudo` on a freshly-imaged Pi often rides a long-lived
first-boot timestamp (rpi-imager's cloud-init datasource leaves one) that
makes `sudo` passwordless for a while with **no NOPASSWD rule actually
present** — `sudo -l` will show plain `(ALL : ALL) ALL`, not `NOPASSWD:`.
The very first `sudo reboot` you run (e.g. after UART bring-up) clears that
timestamp, and every `sudo` call after that genuinely needs the password
again. Don't be fooled by early passwordless `sudo` into thinking the image
ships that way permanently — it doesn't, and scripts/automation that call
`sudo` must be prepared to supply the password (this is exactly why
`--install-nginx` and `--bootstrap` use `ssh -t` and prompt interactively).

---

## 2. Serial port (UART) configuration

**This is the step most likely to need redoing exactly, and the one that
can make the Pi fail to boot if botched.** `pi-bootstrap.sh` backs up
`/boot/firmware/config.txt` and `cmdline.txt` to `.bak-<timestamp>` before
touching either.

The Zero W's PL011 UART (the good one — stable baud rate) is wired to the
Bluetooth chip by default. Without disabling that, `/dev/serial0` resolves
to the mini-UART (`ttyS0`) instead, whose baud rate is tied to the VPU core
clock and drifts under load — unreliable at 115200. Also, by default Linux
runs a login console on the serial port, which would spray boot/login text
down the hexagon bus.

What `pi-bootstrap.sh` does (all idempotent, grep-guarded):

```bash
sudo raspi-config nonint do_serial_hw 0    # enable_uart=1
sudo raspi-config nonint do_serial_cons 1  # disable console on serial
# + append to config.txt's [all] section:
#   dtoverlay=disable-bt
sudo systemctl disable --now bluetooth.service
```

(No `hciuart` unit exists on this trixie image — harmless if the disable
call errors on an image that lacks it.)

**Verify after the reboot** (this Pi's build): `/dev/serial0` →
`/dev/ttyAMA0`, and `dmesg | grep -i bluetooth` shows no `hci0` — Bluetooth's
kernel driver never even claims the chip once `disable-bt` is active. Before
that reboot, the live kernel is still running with Bluetooth on the PL011
even though the config files are already edited — **config.txt/cmdline.txt
edits are inert until the next reboot.** Don't trust `/dev/serial0` checks
against a Pi that hasn't been rebooted since the edit.

Then, with a real hexagon bus (or a loopback jumper GPIO14 pin 8 → GPIO15
pin 10):

```bash
python3 -c "import serial; s=serial.Serial('/dev/serial0',115200,timeout=1); s.write(b'FFG\r\n'); print(s.read(5))"
# must print exactly: b'FFG\r\n'
```

Garbage or nothing back → the `disable-bt` overlay didn't take (still on
`ttyS0`, or no reboot yet). Boot/login text appearing → the console is
still enabled on the serial port.

---

## 2a. Fan control (GPIO23)

A fan is wired to GPIO23 (via a transistor/MOSFET switch — the pin isn't
driving the fan's coil directly). Handled entirely by the kernel's own
`gpio-fan` devicetree overlay (`/boot/firmware/overlays/gpio-fan.dtbo`,
shipped stock on this trixie image) — no daemon, no polling script; the
kernel watches the SoC thermal zone itself and toggles the pin:

```
dtoverlay=gpio-fan,gpiopin=23,temp=50000
```

`temp` is millicelsius (50000 = 50°C). Left at the overlay's default 10°C
hysteresis (`hyst`) — fan switches on at 50°C, off again at 40°C. Confirmed
working 2026-09-02: driving GPIO23 high by hand (`gpioset`) spun the fan up
before the overlay was added at all, so the wiring itself was verified
independently of the overlay.

Verify after reboot:

```bash
ssh pi@hexagons.local 'gpioinfo | grep "line  23"'
# should show a consumer (e.g. "gpio-fan") once the overlay has claimed it,
# not a bare unclaimed "input"
```

Like the UART/USB-gadget overlays below, this is inert until the next
reboot after being added to `config.txt`.

---

## 3. USB gadget SSH (recovery path)

`pi-bootstrap.sh` also stages `dtoverlay=dwc2` (config.txt) and
`modules-load=dwc2,g_ether` (inserted into cmdline.txt right after
`rootwait`), plus an NM profile (`hexagon-usb`) with `ipv4.method=shared`.
After the same reboot that UART needs, plugging the Pi's USB port (data,
not power-only) into a Mac brings up a `usb0` gadget-ethernet interface —
the Pi is reachable at **10.42.0.1** regardless of WiFi state. This is the
"break glass" path if WiFi setup ever goes sideways.

---

## 4. Networking model: NetworkManager keyfiles, not netplan

Raspberry Pi Imager's headless WiFi setup writes netplan YAML
(`/etc/netplan/90-NM-*.yaml`) that NetworkManager renders into its own
connection profiles at boot. `pi-bootstrap.sh` replaces the WiFi side of
that with NM connection profiles created directly via `nmcli con add`
(one per network in `boot_credentials.json`, named `hexagon-wifi-<ssid>`) —
one source of truth, and `autoconnect-priority` isn't at the mercy of a
netplan round-trip. The Imager's netplan WiFi file is only deleted **after**
the new primary profile is confirmed to actually connect — if it fails to
connect, the script leaves the old netplan profile alone and aborts, so a
bad `boot_credentials.json` can't strand the Pi off WiFi entirely. The
`eth0` netplan file is left untouched (nothing about wired networking
changed).

---

## 5. Base packages, install directory, scoped sudoers

Handled by `pi-bootstrap.sh` steps 1–2: `python3-serial`, `rsync`,
`avahi-daemon`, `iw`, `hostapd` via apt; `/opt/hexagons` created and owned
by `pi`; `/etc/sudoers.d/hexagons-deploy` installed from
`deploy/hexagons-deploy.sudoers` (validated with `visudo -c -f` before
being moved into place).

Flask, Waitress, and nginx are **not** installed here — see §9
(`--install-nginx`).

**Gotcha:** sudoers command matching is **literal on the argument list** —
`systemctl status hexagons` in the rule does NOT match `systemctl status
hexagons --no-pager`. Adding flags when testing `sudo -n ...` by hand will
falsely look like the rule is broken. Use the exact command form when
testing.

`--install-nginx` and `--bootstrap` deliberately do **not** get added to
this allowlist — apt installs and system config edits are one-time,
interactive (`ssh -t`, typed sudo password), not worth open-ending the
passwordless list for.

---

## 6. AP fallback — and the WPA2 dead end

`hexagons-netwatch.service` (installed by `pi-bootstrap.sh`, source in
`deploy/hexagons-netwatch.sh`) runs once at boot: it waits up to
`WIFI_TIMEOUT` (default 60s, `/etc/hexagons-netwatch.conf`) for `wlan0` to
associate to any `hexagon-wifi-*` profile. If none show up, it runs
`nmcli con up hexagon-ap`. **This is a boot-time decision only** — once in
AP mode, it does not retry; the AP stays up until the next reboot or a
manual `sudo systemctl restart hexagons-netwatch`.

**The `hexagon-ap` profile is an OPEN network (no password) — not a config
oversight.** The Zero W's onboard **BCM43430** chip (`brcmfmac` driver)
cannot reliably do WPA2-PSK in AP mode. Discovered live on this build:

- `nmcli con up hexagon-ap` with `wifi-sec.key-mgmt=wpa-psk` hung for ~26s
  then failed with `Hotspot network creation took too long, failing
  activation` (NetworkManager log) / `802.1X supplicant took too long to
  authenticate` (nmcli's generic error text — misleading, this has nothing
  to do with 802.1X).
- The real failure is at the driver level, in `journalctl -k`:
  `nl80211: kernel reports: key setting validation failed` and
  `brcmf_vif_set_mgmt_ie: vndr ie set error : -52`, right as hostapd tries
  to install the WPA key.
- Installing `hostapd` explicitly (NetworkManager shells out to it when
  present) did not fix it.
- Updating `firmware-brcm80211` to the latest available package (a
  `cyfmac43430-sdio.bin` blob over a year newer) did not fix it either.
- An **open** AP (`nmcli con add ... 802-11-wireless.mode ap` with no
  `wifi-sec` block at all) activates in about a second, cleanly, every
  time. This isolates the bug specifically to WPA2 key installation on this
  chip/firmware combination, not AP mode in general.

Given the AP is a recovery-only network (get back in touch with the Pi to
fix WiFi, or use the Touch UI in a pinch) on a physically-installed art
piece — not something exposed to the internet — an open network was judged
an acceptable trade for "actually works reliably." If this ever needs
revisiting: the next thing to try would be a hand-tuned standalone
`hostapd.conf` (bypassing NetworkManager's built-in hotspot generator
entirely, with `wlan0` set `nmcli device set wlan0 managed no`) trying
`ieee80211w=0` (disable protected management frames) and forcing
`wpa_pairwise=CCMP` only — both are common community workarounds for this
exact chip's AP+WPA2 bug, neither guaranteed to work, not yet tried here.

`hostapd` itself stays installed but its standalone `hostapd.service` is
**masked** by the Debian package's own postinst (no `/etc/hostapd/hostapd.conf`
shipped) — that's correct and expected; NetworkManager invokes the `hostapd`
binary directly per-connection, it never runs as an always-on service here.

**Verifying AP fallback** (disruptive — it will drop your SSH session if
you're connected over WiFi, since bringing the AP up necessarily takes
`wlan0` off the station network you're using):

```bash
# From a machine SSH'd in over WiFi, detach so the test survives the
# session dying when wlan0 leaves station mode:
ssh pi@hexagons.local 'sudo -v'   # cache a sudo ticket first (interactive)
ssh pi@hexagons.local '
setsid nohup bash -c "
  sleep 1
  sudo -n nmcli con down \"hexagon-wifi-<ssid>\"
  sudo -n systemctl restart hexagons-netwatch
" </dev/null >/tmp/ap-test.log 2>&1 &
disown; echo LAUNCHED
'
# wait ~65s, then from a phone/laptop: join "hexagon-ap" (open, no
# password), confirm http://10.42.0.1 loads the dashboard, then power-cycle
# the Pi (or reconnect it to WiFi range) to bring it back — AP stays up
# until reboot by design.
```

**Recovering from AP mode without SSH:** join `hexagon-ap`, open
`http://10.42.0.1`, and use the Tools page's WiFi section — it's built for
exactly this. It POSTs to `GET`/`POST`/`DELETE /api/network`
(`server/routes/network.py`, see `AGENTS.md`), which shells out to
`hexagons-netcfg` (`/usr/local/sbin/hexagons-netcfg`, installed by this
script from `deploy/hexagons-netcfg.py`). That helper is what actually
runs `nmcli` as root: `scan`/`list` are unprivileged reads; `add`,
`forget`, and `reboot` are gated by `/etc/sudoers.d/hexagons-netcfg`
(`deploy/hexagons-netcfg.sudoers` — same install pattern as
`hexagons-deploy.sudoers` in §5, its own file since this is app-runtime
privilege rather than a dev-machine deploy operation), permitting only
those three literal invocations — no wildcards, since sudoers matches the
whole argument list literally and a wildcarded `nmcli` rule would open up
dispatcher scripts and `connection.permissions`. Saving a network reboots
the Pi so `hexagons-netwatch` tries it at the next boot — there's no live
retry path, matching §6's "boot-time decision only" behaviour above. A
network added this way is **not** written into `boot_credentials.json` on
your dev machine (that file never reaches the Pi at all — see `deploy.sh`
`sync_files()`'s exclude list), so it won't survive a re-image; add it
there too if it should. **A Pi bootstrapped before this feature shipped
needs one `./deploy.sh --bootstrap` re-run** to pick up the helper and its
sudoers file — routine `./deploy.sh` can't install either, by design.

Scan results pre-filling the SSID field degrade in AP mode specifically
(the same reason `hexagon-ap` itself can't be WPA2, above, also means
`wlan0` can't scan while it's the one serving the AP) — you'll see
whatever NetworkManager cached before the AP came up, which is usually
populated but not guaranteed. The SSID field stays free-text for exactly
that reason.

---

## 6a. tzdata refresh

The Pi has **no RTC** — its clock comes entirely from `systemd-timesyncd`
over the network (on by default in Raspberry Pi OS), which the calm UI's
`/settings` daily on/off schedule (`server/schedule.py`) depends on being
correct: `schedule.clock_synced()` refuses to enforce the schedule at all
until `/run/systemd/timesync/synchronized` exists, so an unsynced clock
just means the piece keeps running rather than going dark at the wrong
time — "fail on," not "fail off." Nothing in this repo sets the system
timezone or touches `timedatectl`; the schedule interprets its configured
IANA zone (default `America/Vancouver`) entirely at the application level
via Python's `zoneinfo`, so the OS stays on UTC.

That still leaves the OS's own `tzdata` package, which is what `zoneinfo`
reads its rules from — and it can go stale. British Columbia's move to
permanent DST is exactly the kind of change that lands in a `tzdata`
release, not in this repo. `hexagons-tzdata.timer` (`OnBootSec=2min`,
`OnUnitActiveSec=1w`) runs `hexagons-tzdata.sh`, which does a best-effort
`apt-get install --only-upgrade tzdata` and — only if the installed
version actually changed — a `systemctl try-restart hexagons` so the
running process picks up the new rules without waiting for an unrelated
deploy or reboot. A timer rather than a boot-only oneshot (unlike
`hexagons-netwatch`) because this Pi is meant to run for months without
rebooting; a release landing mid-uptime is the normal case, not an edge
case. Always exits 0 — a Pi sitting on the open `hexagon-ap` fallback
with no internet must never fail this unit or hold up boot.

Check it any time with `systemctl status hexagons-tzdata.timer` /
`journalctl -u hexagons-tzdata`.

---

## 7. Tailscale

Installed via the official `install.sh` (resolves to the raspbian/trixie
apt repo) and `tailscaled` enabled at boot — but `tailscale up` is
deliberately **not** run by the bootstrap script. Authenticate it yourself:

```bash
ssh pi@hexagons.local
sudo tailscale up
```

`sudo tailscale status` showing `Logged out.` after bootstrap is expected,
not an error.

**Architecture note:** this is `armv6l` (Zero W). If a future Tailscale
release drops armv6 apt packages, `pi-bootstrap.sh`'s `curl | sh` install
will fail — it warns and continues rather than aborting the rest of
bootstrap. Fallback: the static `arm` binary from
`https://pkgs.tailscale.com/stable/#static` plus a hand-written systemd
unit.

---

## 8. App + nginx install

Not part of `--bootstrap`. From the dev machine (this repo), after the
reboot bootstrap requires:

```bash
./deploy.sh --install-service   # installs deploy/hexagons.service, systemctl enable
./deploy.sh --install-nginx     # apt install python3-flask python3-waitress nginx-light,
                                 # installs deploy/nginx.hexagons.conf, removes the
                                 # stock default site, systemctl enable + restart nginx
./deploy.sh                     # builds the UI, rsyncs, restarts hexagons.service
```

`--install-service` runs non-interactively (covered by the scoped sudoers
rule from §5). `--install-nginx` is interactive (asks for the sudo password
once via `ssh -t`) — see `deploy.sh`'s top-of-file comment for why.

**Gotcha found during setup:** the nginx package's post-install script
starts nginx immediately with its stock config (the "Welcome to nginx"
default site) *before* our site config is dropped in. A `systemctl reload
nginx` afterward did **not** reliably pick up the new `sites-enabled/hexagons`
config in testing — it kept serving the stock page. A full `systemctl
restart nginx` did. `deploy.sh` uses `restart`, not `reload`, for exactly
this reason.

**Known harmless quirk:** immediately after a reboot, nginx can come up and
start accepting connections a few seconds before `hexagons.service` has
finished starting (no explicit ordering dependency between the two units) —
a request in that narrow window gets a `502 Bad Gateway` from nginx. It
self-resolves within well under a minute once Flask finishes booting; not
worth adding unit ordering for.

---

## 9. Verifying it all worked

```bash
curl http://hexagons.local/api/health
# {"ok":true,"uart":"/dev/serial0","uart_error":null,"uart_ok":true,"clock_synced":true}

curl --max-time 3 http://hexagons.local:8080/api/health   # from the DEV machine, not the Pi
# must FAIL to connect — Flask binds 127.0.0.1:8080 only, nginx (port 80)
# is the sole public entry point. If this succeeds, something's wrong.

ssh pi@hexagons.local "systemctl is-enabled hexagons nginx tailscaled hexagons-netwatch hexagons-tzdata.timer"
# enabled (x5)

ssh pi@hexagons.local "nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY con show"
# hexagon-wifi-<ssid> entries per boot_credentials.json, hexagon-ap
# (autoconnect no), hexagon-usb

ssh pi@hexagons.local "timedatectl show -p NTPSynchronized -p Timezone"
# NTPSynchronized=yes (server/schedule.py's daily on/off schedule won't
# enforce until this is yes — see §6a); Timezone=UTC is expected and
# correct, the schedule's own zone is app-level, not the OS's
```

Then a real reboot test (`sudo reboot`) — all five services should come
back on their own; re-check `/api/health` (now also reporting
`clock_synced`) and that saved macros/settings/schedule survived (they
live in `/opt/hexagons/state.json`, excluded from `rsync --delete` by
`deploy.sh` specifically so a deploy can never wipe them).

---

## Quick reference: what lives where on the Pi

| Path | What |
|---|---|
| `/opt/hexagons/` | The whole repo, rsynced by `deploy.sh` |
| `/opt/hexagons/state.json`, `log.txt` | Runtime state (macros, settings, schedule) — excluded from rsync `--delete` |
| `/etc/systemd/system/hexagons.service` | The app's systemd unit (from `deploy/hexagons.service`) |
| `/etc/systemd/system/hexagons-netwatch.service` | Boot-time WiFi/AP fallback decision (from `deploy/hexagons-netwatch.service`) |
| `/usr/local/sbin/hexagons-netwatch` | The fallback decision script (from `deploy/hexagons-netwatch.sh`) |
| `/etc/hexagons-netwatch.conf` | `WIFI_TIMEOUT`, `AP_CONNECTION` |
| `/usr/local/sbin/hexagons-netcfg` | Runtime WiFi provisioning helper the app shells out to (from `deploy/hexagons-netcfg.py`) — see §6 |
| `/etc/sudoers.d/hexagons-netcfg` | Passwordless sudo for `hexagons-netcfg`'s `add`/`forget`/`reboot` verbs only |
| `/etc/systemd/system/hexagons-tzdata.timer` + `.service` | Boot + weekly `tzdata` refresh (from `deploy/hexagons-tzdata.timer`/`.service`) — see §6a |
| `/usr/local/sbin/hexagons-tzdata` | The refresh script (from `deploy/hexagons-tzdata.sh`); no sudoers rule — systemd runs it as root directly |
| `/etc/nginx/sites-available/hexagons` (symlinked into `sites-enabled/`) | nginx config (from `deploy/nginx.hexagons.conf`) |
| `/etc/sudoers.d/hexagons-deploy` | Passwordless sudo for routine deploy commands only |
| `/boot/firmware/config.txt` | `enable_uart=1`, `dtoverlay=disable-bt`, `dtoverlay=dwc2`, `dtoverlay=gpio-fan,gpiopin=23,temp=50000` |
| `/boot/firmware/cmdline.txt` | No `console=serial0,...`; has `modules-load=dwc2,g_ether` |
| `/dev/serial0` → `/dev/ttyAMA0` | The hexagon bus, 115200 baud |
| NetworkManager profiles | `hexagon-wifi-<ssid>` (per network), `hexagon-ap` (open, autoconnect off), `hexagon-usb` (10.42.0.1 over USB gadget) |
| `boot_credentials.json` (repo root, gitignored) | WiFi/AP/SSH credentials — consumed once by `./deploy.sh --bootstrap`, never uploaded anywhere else |
