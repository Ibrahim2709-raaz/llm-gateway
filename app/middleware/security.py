import json
from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope.get("headers", []))
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(scope, send)
            return

        messages: list[Message] = []
        body_bytes = 0
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body_bytes += len(message.get("body", b""))
            if body_bytes > self.max_body_bytes:
                await self._reject(scope, send)
                return
            more_body = message.get("more_body", False)

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            # Streaming and file responses may listen for the real disconnect
            # after the buffered request body has been replayed. Forwarding to
            # the original receive channel preserves that ASGI lifecycle.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(headers: Sequence[tuple[bytes, bytes]]) -> int | None:
        for name, value in headers:
            if name.lower() != b"content-length":
                continue
            try:
                return max(0, int(value))
            except ValueError:
                return None
        return None

    async def _reject(self, scope: Scope, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id")
        body = json.dumps(
            {
                "error": {
                    "message": "Request body exceeds the configured size limit.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "request_too_large",
                    "request_id": request_id,
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, include_hsts: bool = False) -> None:
        self.app = app
        self.include_hsts = include_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                self._append(headers, existing, b"x-content-type-options", b"nosniff")
                self._append(headers, existing, b"x-frame-options", b"DENY")
                self._append(headers, existing, b"referrer-policy", b"no-referrer")
                self._append(
                    headers,
                    existing,
                    b"permissions-policy",
                    b"camera=(), microphone=(), geolocation=()",
                )
                self._append(headers, existing, b"cache-control", b"no-store")
                if self.include_hsts:
                    self._append(
                        headers,
                        existing,
                        b"strict-transport-security",
                        b"max-age=31536000; includeSubDomains",
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    @staticmethod
    def _append(
        headers: list[tuple[bytes, bytes]],
        existing: set[bytes],
        name: bytes,
        value: bytes,
    ) -> None:
        if name not in existing:
            headers.append((name, value))
            existing.add(name)
