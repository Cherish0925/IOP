"""Streamable HTTP for hosts without a local MCP process."""

import asyncio
import hmac
import os
import sys


async def _unauthorized(receive, send):
    body = b"Unauthorized"
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"www-authenticate", b"Bearer"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"content-type", b"text/plain; charset=utf-8"),
        ],
    })
    # Return the complete reply immediately, then finish receiving an in-flight
    # upload before closing. Closing over unread data can reset a Windows socket.
    await send({"type": "http.response.body", "body": body, "more_body": True})

    async def discard():
        while True:
            message = await receive()
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                return

    try:
        await asyncio.wait_for(discard(), timeout=1.0)
    except asyncio.TimeoutError:
        pass
    await send({"type": "http.response.body", "body": b""})


def application(client, token, selection=None):
    if not token or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Set IOP_MCP_TOKEN to a nonempty ASCII bearer token without spaces.")
    from .mcp import create_server

    server = create_server(client, http=True, selection=selection)
    options = dict(stateless_http=True, json_response=True) if sys.version_info >= (3, 11) else {}
    app = server.streamable_http_app(**options)
    expected = ("Bearer " + token).encode("ascii")

    async def authenticated(scope, receive, send):
        if scope["type"] == "http":
            headers = [v for k, v in scope.get("headers", ()) if k == b"authorization"]
            if len(headers) != 1 or not hmac.compare_digest(headers[0], expected):
                await _unauthorized(receive, send)
                return
        await app(scope, receive, send)

    return authenticated


def run(home, endpoint, port, selection=None):
    token = os.environ.get("IOP_MCP_TOKEN")
    if not token:
        raise ValueError("Set IOP_MCP_TOKEN in the gateway environment before starting it.")
    import uvicorn
    from .desktop import ensure_service
    from .mcp import toolsets

    toolsets(selection)
    app = application(ensure_service(home, endpoint), token, selection)
    uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=False, access_log=False)
