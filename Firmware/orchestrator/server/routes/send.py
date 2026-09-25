from flask import jsonify, request

import orchestrator


def post_send():
    """Split body on \\r\\n / \\n and write each line to the UART bus.

    Body:  text/plain, one command per line (e.g. "01A0\\r\\n02A180\\r\\nFFG")
    Response: {"sent": N, "commands": [...]}
    """
    body_str = request.get_data(as_text=True) or ""
    sent_cmds = []
    for line in body_str.replace("\r\n", "\n").split("\n"):
        line = line.strip().upper()
        if not line:
            continue
        orchestrator.send(line)
        sent_cmds.append(line)
    return jsonify({"sent": len(sent_cmds), "commands": sent_cmds})
