"""
server — Flask app + route registry for the hexagon orchestrator.

Imports handlers from server/routes/* and wires them to URLs. main.py (and
simhost/run.py) import this module and call server.serve(host, port).
"""

import os

from flask import Flask, send_from_directory

# settings imported under an alias: server/settings.py (the persisted
# default-speed module, imported all over server/ and server/patterns/ as
# `from server import settings`) and server/routes/settings.py share the
# bare name "settings". Binding the routes module to that same name here
# would set it as *this* module's (i.e. the `server` package's) `settings`
# attribute, clobbering whichever one — server.settings or
# server.routes.settings — some other module imported first. The alias
# sidesteps that entirely: no plain `settings` global is ever bound here.
from server.routes import macros, modes, network, patterns, send, sim, system
from server.routes import schedule as schedule_routes
from server.routes import settings as settings_routes

# See main.py for why HEXAGON_FS_ROOT exists — it must be set (main.py does
# this via os.environ.setdefault before importing this module) so dist/,
# macros.json, and log.txt resolve under the install directory rather than
# the real filesystem root.
_FS_ROOT = os.getenv("HEXAGON_FS_ROOT") or ""
_DIST_DIR = _FS_ROOT + "/dist"

app = Flask(__name__, static_folder=_DIST_DIR, static_url_path="")


# Every path src/App.jsx's <Router> matches. Flask's static handling
# serves /assets/* etc. automatically, but not these — the app is routed
# client-side by preact-router, so each one needs to resolve to index.html
# on a hard refresh or direct link, same as any other SPA. In production
# nginx's fallback (deploy/nginx.hexagons.conf) does this before Flask is
# even reached; these explicit routes exist so `simhost/run.py`, which
# serves Flask directly with no nginx in front, behaves the same way.
_SPA_ROUTES = (
    "/",
    "/touch",
    "/settings",
    "/admin",
    "/admin/tools",
    "/admin/patterns",
    "/admin/modes",
    "/admin/sim",
)


def index():
    """Werkzeug prefers these exact-string rules (registered per-path below,
    one per _SPA_ROUTES entry) over the dynamic static-file rule, so they
    don't shadow real files under dist/."""
    return send_from_directory(app.static_folder, "index.html")


for _route in _SPA_ROUTES:
    app.add_url_rule(_route, endpoint=f"index{_route.replace('/', '_')}", view_func=index)


# ---------------------------------------------------------------------------
# API routes (registered before the "/" catch above takes priority anyway —
# Flask matches by most-specific rule, not registration order, but this
# grouping mirrors the previous DIY router's layout for readability)
# ---------------------------------------------------------------------------

app.add_url_rule("/api/health", view_func=system.get_health)
app.add_url_rule("/api/send", view_func=send.post_send, methods=["POST"])
app.add_url_rule("/api/macros", view_func=macros.get_macros)
app.add_url_rule("/api/macros", view_func=macros.post_macros, methods=["POST"])
app.add_url_rule("/api/macros/<name>", view_func=macros.delete_macro, methods=["DELETE"])
app.add_url_rule("/api/patterns", view_func=patterns.get_patterns)
app.add_url_rule("/api/patterns/stop", view_func=patterns.post_stop, methods=["POST"])
app.add_url_rule("/api/patterns/<name>", view_func=patterns.post_pattern, methods=["POST"])
app.add_url_rule("/api/modes", view_func=modes.get_modes)
app.add_url_rule("/api/modes/stop", view_func=modes.post_mode_stop, methods=["POST"])
app.add_url_rule("/api/modes/status", view_func=modes.get_mode_status)
app.add_url_rule("/api/modes/<name>", view_func=modes.post_mode, methods=["POST"])
app.add_url_rule("/api/sim/tail", view_func=sim.get_sim_tail)
app.add_url_rule("/api/layout", view_func=sim.get_layout)
app.add_url_rule("/api/network", view_func=network.get_network)
app.add_url_rule("/api/network", view_func=network.post_network, methods=["POST"])
app.add_url_rule("/api/network/<path:ssid>", view_func=network.delete_network, methods=["DELETE"])
app.add_url_rule("/api/settings", view_func=settings_routes.get_settings)
app.add_url_rule("/api/settings", view_func=settings_routes.post_settings, methods=["POST"])
app.add_url_rule("/api/schedule", view_func=schedule_routes.get_schedule)
app.add_url_rule("/api/schedule", view_func=schedule_routes.post_schedule, methods=["POST"])


def serve(host, port, threads=4):
    """Serve *app* under Waitress (host, port) — blocking, forever.

    Waitress rather than gunicorn: gunicorn's default model is multiple
    worker *processes*, each of which would open its own handle on the
    single shared UART — a guaranteed way to interleave garbage onto the
    bus. Waitress is one process with a small thread pool instead, which
    is what orchestrator.py's _send_lock and server/macros.py's LOCK are
    written to make safe. Not Flask's own dev server, which is explicitly
    unfit for production and single-threaded anyway.
    """
    from waitress import serve as _serve

    _serve(app, host=host, port=port, threads=threads)
