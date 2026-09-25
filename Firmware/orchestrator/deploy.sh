#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — push Hexagon Orchestrator files to the Raspberry Pi over SSH
#
# Usage:
#   ./deploy.sh                     one-shot deploy (rsync + restart)
#   ./deploy.sh --watch             watch for changes and auto-deploy
#   ./deploy.sh --bootstrap         one-time: OS-level bring-up of a fresh Pi
#                                   (UART, USB gadget SSH, WiFi/AP fallback,
#                                   tailscaled) — see PI_SETUP.md
#   ./deploy.sh --install-service   one-time: install + enable the app's systemd unit
#   ./deploy.sh --install-nginx     one-time: install nginx + Flask/Waitress + the site config
#   ./deploy.sh --host 10.0.0.5     use a specific IP instead of mDNS
#
# Auth:
#   Key-based SSH only for routine deploys — set it up once with:
#     ssh-copy-id pi@hexagons.local
#   (deploy_all/watch_mode never prompt for a password; every call there
#   needs to be non-interactive, especially --watch. --bootstrap and
#   --install-nginx are the exceptions — they install apt packages and/or
#   edit system config, which isn't worth open-ending the sudo allowlist
#   for, so they use `ssh -t` and prompt for your sudo password
#   interactively. One-time setup only, never part of a routine deploy.)
#
# Prerequisites for --watch:
#   macOS:   brew install fswatch
#   Linux:   apt install inotify-tools
# ---------------------------------------------------------------------------

set -euo pipefail

HOST="${HEXAGON_HOST:-hexagons.local}"
REMOTE_USER="${HEXAGON_USER:-pi}"
REMOTE_DIR="${HEXAGON_DIR:-/opt/hexagons}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE="$REMOTE_USER@$HOST"

# ---------------------------------------------------------------------------
# Build the UI (dist/ is deployed as static files, same as before)
# ---------------------------------------------------------------------------

build_ui() {
    echo "Building UI..."
    cd "$SCRIPT_DIR"
    if ! npm run build --silent; then
        echo "ERROR: Vite build failed. Aborting deploy."
        exit 1
    fi
    echo "  ✓ build complete"
    echo ""
}

# ---------------------------------------------------------------------------
# Sync the repo to the Pi over rsync/SSH
#
# --delete keeps the remote tree in sync with local deletes (e.g. this
# port's removal of boot.py/code.py) — but state.json (and the legacy
# macros.json it migrated from) and log.txt live under HEXAGON_FS_ROOT,
# which main.py points at this same directory, so they must be excluded or
# every deploy would wipe saved macros/settings. state.json.tmp is
# server/store.py's atomic-write staging file — never a real artifact to
# sync, but excluded defensively in case a save was ever interrupted
# between the write and the rename.
#
# boot_credentials.json is excluded for a different reason: it holds
# WiFi/AP/SSH passwords and is consumed once, locally, by `--bootstrap` —
# it has no business being copied onto the Pi at all.
# ---------------------------------------------------------------------------

sync_files() {
    echo "Syncing to $REMOTE:$REMOTE_DIR ..."
    ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"
    rsync -az --delete \
        --exclude '.git' \
        --exclude '.venv' \
        --exclude 'node_modules' \
        --exclude '__pycache__' \
        --exclude '.ruff_cache' \
        --exclude '.claude' \
        --exclude 'src' \
        --exclude 'state.json' \
        --exclude 'state.json.tmp' \
        --exclude 'macros.json' \
        --exclude 'log.txt' \
        --exclude 'errors.txt' \
        --exclude 'boot_credentials.json' \
        "$SCRIPT_DIR/" "$REMOTE:$REMOTE_DIR/"
    echo "  ✓ synced"
    echo ""
}

# ---------------------------------------------------------------------------
# Full deploy cycle
# ---------------------------------------------------------------------------
deploy_all() {
    echo ""
    echo "══ Deploying to $REMOTE:$REMOTE_DIR ══"
    echo ""

    build_ui
    sync_files
    restart_and_wait
}

# ---------------------------------------------------------------------------
# Restart the systemd service and wait for the server to come back up
# ---------------------------------------------------------------------------
restart_and_wait() {
    echo "Restarting hexagons.service..."
    ssh "$REMOTE" "sudo systemctl restart hexagons"

    echo "  Waiting for server to come back up..."
    # Through nginx on port 80 — main.py binds Flask/Waitress to
    # 127.0.0.1:8080 only, so that port isn't reachable from here at all;
    # this doubles as confirmation nginx is actually proxying correctly.
    local i http_code
    for i in $(seq 1 20); do
        sleep 1
        # -4: mDNS (.local) resolution can return an AAAA with no live
        # responder (e.g. the Pi's WiFi has IPv6 disabled) — dual-stack
        # curl then burns the whole --max-time just resolving, never
        # reaching a real connection attempt, so every retry "fails" even
        # though the server is up. Forcing IPv4 sidesteps that.
        http_code=$(curl -4s -o /dev/null -w "%{http_code}" \
            --max-time 3 \
            "http://$HOST/api/health" 2>/dev/null) || true
        if [[ "$http_code" == "200" ]]; then
            echo "  ✓ Server is up  (${i}s)"
            echo ""
            echo "══ Done ══"
            echo ""
            return 0
        fi
    done

    echo "  ✗ Server did not respond after 20s"
    echo "    Check: ssh $REMOTE journalctl -u hexagons -n 50"
    return 1
}

# ---------------------------------------------------------------------------
# One-time: OS-level bring-up of a fresh Pi — UART, USB gadget SSH,
# WiFi/AP fallback, tailscaled. Reads credentials from boot_credentials.json
# (gitignored, never sent anywhere but this scp) and hands off to
# deploy/pi-bootstrap.sh, which does the actual work on the Pi. Interactive
# (ssh -t) for the same reason as --install-nginx — see the Auth note above.
# Idempotent: safe to re-run after a failure, a credentials change, or to
# add/remove WiFi networks.
# ---------------------------------------------------------------------------
bootstrap() {
    local creds="$SCRIPT_DIR/boot_credentials.json"
    [[ -f "$creds" ]] || {
        cat <<EOF
ERROR: $creds not found. Create it with:
{
  "wifi_networks": [
    { "ssid": "...", "password": "...", "priority": 100 },
    { "ssid": "...", "password": "..." }
  ],
  "ssh": { "username": "pi", "password": "..." },
  "ap": { "ssid": "..." }
}
"priority" is optional (higher wins; defaults to array order, first highest).
"ssh" is for your own reference — ssh-copy-id still needs to be run by hand.
"ap" has no password: this hardware's onboard WiFi chip can't do WPA2 in AP
mode (see PI_SETUP.md), so the fallback hotspot is intentionally open.
EOF
        exit 1
    }

    python3 -c "import json; json.load(open('$creds'))" 2>/dev/null \
        || { echo "ERROR: $creds is not valid JSON."; exit 1; }

    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE" true 2>/dev/null; then
        echo "ERROR: key-based SSH to $REMOTE isn't working yet."
        echo "  Run 'ssh-copy-id $REMOTE' first (password is in boot_credentials.json's ssh.password)."
        exit 1
    fi

    echo "Copying bootstrap files to $REMOTE:/tmp ..."
    scp -q "$creds" "$REMOTE:/tmp/hexagons-networks.json"
    scp -q "$SCRIPT_DIR/deploy/pi-bootstrap.sh" \
           "$SCRIPT_DIR/deploy/hexagons-deploy.sudoers" \
           "$SCRIPT_DIR/deploy/hexagons-netwatch.sh" \
           "$SCRIPT_DIR/deploy/hexagons-netwatch.service" \
           "$SCRIPT_DIR/deploy/hexagons-netcfg.py" \
           "$SCRIPT_DIR/deploy/hexagons-netcfg.sudoers" \
           "$SCRIPT_DIR/deploy/hexagons-tzdata.sh" \
           "$SCRIPT_DIR/deploy/hexagons-tzdata.service" \
           "$SCRIPT_DIR/deploy/hexagons-tzdata.timer" \
           "$REMOTE:/tmp/"
    ssh "$REMOTE" "chmod 600 /tmp/hexagons-networks.json"

    echo "Running pi-bootstrap.sh on $REMOTE (will prompt for the sudo password)..."
    # Preserve pi-bootstrap.sh's exit code past the cleanup rm — otherwise a
    # failure partway through would be masked and this function would report
    # success.
    ssh -t "$REMOTE" 'bash /tmp/pi-bootstrap.sh; rc=$?; rm -f /tmp/pi-bootstrap.sh; exit $rc'
}

# ---------------------------------------------------------------------------
# One-time: install the systemd unit and enable it at boot
# ---------------------------------------------------------------------------
install_service() {
    echo "Installing hexagons.service on $REMOTE ..."
    ssh "$REMOTE" "sudo mkdir -p '$REMOTE_DIR'"
    scp "$SCRIPT_DIR/deploy/hexagons.service" "$REMOTE:/tmp/hexagons.service"
    ssh "$REMOTE" "
        sudo mv /tmp/hexagons.service /etc/systemd/system/hexagons.service &&
        sudo systemctl daemon-reload &&
        sudo systemctl enable hexagons
    "
    echo "  ✓ Service installed and enabled. Run ./deploy.sh to sync files and start it."
}

# ---------------------------------------------------------------------------
# One-time: install nginx + Flask/Waitress, and the site config
#
# Interactive (ssh -t) since it needs sudo for apt + touching nginx's
# config — see the Auth note at the top of this file for why that isn't
# added to the passwordless sudoers allowlist. Idempotent: safe to re-run
# after editing deploy/nginx.hexagons.conf.
# ---------------------------------------------------------------------------
install_nginx() {
    echo "Installing nginx + Flask/Waitress on $REMOTE ..."
    scp "$SCRIPT_DIR/deploy/nginx.hexagons.conf" "$REMOTE:/tmp/nginx.hexagons.conf"
    ssh -t "$REMOTE" "
        set -e
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3-flask python3-waitress nginx-light
        sudo mv /tmp/nginx.hexagons.conf /etc/nginx/sites-available/hexagons
        sudo ln -sf /etc/nginx/sites-available/hexagons /etc/nginx/sites-enabled/hexagons
        sudo rm -f /etc/nginx/sites-enabled/default
        sudo nginx -t
        sudo systemctl enable nginx
        sudo systemctl restart nginx
    "
    echo "  ✓ nginx installed and serving on port 80. Run ./deploy.sh to sync files."
}

# ---------------------------------------------------------------------------
# Watch mode — auto-deploy on file changes
# ---------------------------------------------------------------------------
watch_mode() {
    # Initial deploy
    deploy_all || true

    echo "Watching for changes (Ctrl-C to stop)..."
    echo ""

    # Watch the whole project directory; exclude generated/hidden paths
    WATCH_PATHS=("$SCRIPT_DIR")
    EXCLUDE_PATTERN="(\.git|\.venv|node_modules|dist|__pycache__)"

    if command -v fswatch &>/dev/null; then
        # macOS — exclude dist/ writes to avoid infinite loops
        fswatch -0 -r --exclude "$EXCLUDE_PATTERN" "${WATCH_PATHS[@]}" \
            | while read -d "" -r _changed; do
                deploy_all || true
            done
    elif command -v inotifywait &>/dev/null; then
        # Linux
        while inotifywait -r -e modify,create,delete \
            --exclude "$EXCLUDE_PATTERN" "${WATCH_PATHS[@]}" 2>/dev/null; do
            # Brief settle to batch rapid changes
            sleep 0.3
            deploy_all || true
        done
    else
        echo "ERROR: --watch requires fswatch (macOS) or inotifywait (Linux)."
        echo "  macOS:  brew install fswatch"
        echo "  Linux:  apt install inotify-tools"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
WATCH=false
BOOTSTRAP=false
INSTALL_SERVICE=false
INSTALL_NGINX=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --watch)            WATCH=true ;;
        --bootstrap)         BOOTSTRAP=true ;;
        --install-service)  INSTALL_SERVICE=true ;;
        --install-nginx)    INSTALL_NGINX=true ;;
        --host)              HOST="$2"; REMOTE="$REMOTE_USER@$HOST"; shift ;;
        *)                   echo "Unknown flag: $1"; exit 1 ;;
    esac
    shift
done

if $BOOTSTRAP; then
    bootstrap
elif $INSTALL_SERVICE; then
    install_service
elif $INSTALL_NGINX; then
    install_nginx
elif $WATCH; then
    watch_mode
else
    deploy_all
fi
