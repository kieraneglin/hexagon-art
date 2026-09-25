from flask import jsonify, request

from server import macros as macro_store


def get_macros():
    return jsonify(macro_store.load())


def post_macros():
    """Create or update a macro. JSON body: {name, commands, group?}"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    name = str(payload.get("name", "")).strip()
    commands = payload.get("commands", "")
    group = str(payload.get("group", "")).strip()

    if not name or not commands:
        return jsonify({"error": "name and commands required"}), 400

    with macro_store.LOCK:
        data = macro_store.load()
        data["macros"][name] = {"commands": commands, "group": group}
        macro_store.save(data)
    return jsonify({"ok": True})


def delete_macro(name=""):
    """Delete a macro by name."""
    if not name:
        return jsonify({"error": "Macro name required"}), 400

    with macro_store.LOCK:
        data = macro_store.load()
        if name in data.get("macros", {}):
            del data["macros"][name]
            macro_store.save(data)
            return jsonify({"ok": True})

    return jsonify({"error": f"Macro '{name}' not found"}), 404
