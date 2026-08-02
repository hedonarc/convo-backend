"""WebSocket close codes and the frames that carry them.

Mirrored on the client in `socketEvents.ts`. Lives in `utils` because both
`apps.authentication` (the handshake middleware) and `apps.conversations` (the
consumers) need them, and neither should import the other.
"""

import json

NO_TOKEN = 4001
INVALID_TOKEN = 4002
NOT_PARTICIPANT = 4003

REASONS = {
    NO_TOKEN: "authentication required",
    INVALID_TOKEN: "token invalid or expired",
    NOT_PARTICIPANT: "not a participant in this conversation",
}

# Sent immediately after a successful accept. Without it a client cannot tell
# an accepted connection from one accepted only to deliver a rejection, since
# both look identical until something else arrives.
CONNECTED_FRAME = json.dumps({"type": "connected", "data": {}})


def rejection_frame(code: int) -> str:
    """Carry *code* as ordinary data, not only in the close frame.

    Proxies do not reliably relay a close frame sent immediately after the
    handshake — Cloudflare, in front of this app in production, drops it
    entirely and the client waits out a socket that is already dead. A data
    frame survives, so the reason reaches the client either way.
    """
    return json.dumps(
        {
            "type": "error",
            "code": code,
            "message": REASONS.get(code, "connection rejected"),
        }
    )
