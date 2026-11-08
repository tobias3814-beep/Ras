"""Only the app itself may talk to the backend. CORS alone doesn't stop a web page: a page can still send a POST (it just
can't read the reply), and rebinding its domain to 127.0.0.1 gets round it altogether. So requests carrying a browser
Origin must come from the app, and the Host must be this machine or its private Tailscale name."""
from typing import Any

# the Mac/iPhone app (Tauri, dev server) and the Windows portable build, whose dashboard this backend serves itself
ORIGINS = {"http://localhost:1420", "tauri://localhost", "http://tauri.localhost", "http://127.0.0.1:18765", "http://localhost:18765"}
HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def allowed(host: str | None, origin: str | None) -> bool:
    name = (host or "").rsplit(":", 1)[0] if not (host or "").endswith("]") else host
    return (name in HOSTS or (name or "").endswith(".ts.net")) and (origin is None or origin in ORIGINS)


class LocalOnly:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            h = {k: v.decode("latin-1") for k, v in scope["headers"] if k in (b"host", b"origin")}
            if not allowed(h.get(b"host"), h.get(b"origin")):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"forbidden"})
                return
        await self.app(scope, receive, send)
