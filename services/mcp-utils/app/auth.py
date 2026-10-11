"""A tiny ASGI guard: every request except /healthz must carry the expected credential.

The server refuses to start without a credential (see server.py), so it can never be deployed open by
accident. The comparison is constant-time.
"""
import hmac
import json


class RequireCredential:
    def __init__(self, app, header: str, expected: str, prefix: str = "") -> None:
        self.app = app
        self.header = header.lower().encode()
        self.expected = (prefix + expected).encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            given = next((v for k, v in scope["headers"] if k == self.header), b"")
            if not hmac.compare_digest(given, self.expected):
                body = json.dumps({"error": "missing or wrong credential"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
