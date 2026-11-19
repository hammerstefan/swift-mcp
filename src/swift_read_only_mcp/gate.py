"""Fail-closed ASGI gate in front of every MCP request."""

import asyncio
import logging

from generateapikey.integrations import APIKeyAuth, AuthError
from generateapikey.keystore import KeyStore
from generateapikey.mfa import AttemptLimiter
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 8
REQUEST_TIMEOUT_SECONDS = 30.0
UNAUTHENTICATED_RATE_MULTIPLIER = 10

_SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


class _BodyTooLarge(Exception):
    pass


class _ClientDisconnected(Exception):
    pass


class CallerKeyGate:
    """Authenticate, bound and time-limit each HTTP request before any MCP handler runs."""

    def __init__(self, app: ASGIApp, store: KeyStore, pepper: str, rate_limit: int, port: int):
        self.app = app
        self.store = store
        self.port = port
        self.max_concurrency = MAX_CONCURRENT_REQUESTS
        self.request_timeout = REQUEST_TIMEOUT_SECONDS
        self.active_requests = 0
        self.rate = AttemptLimiter(max_attempts=rate_limit, window=60, max_keys=4096)
        # Every caller arrives through the tunnel from the same loopback address,
        # so a per-IP limit would be one shared limit anyway. Keep it explicitly global.
        self.unauthenticated_rate = AttemptLimiter(
            max_attempts=rate_limit * UNAUTHENTICATED_RATE_MULTIPLIER, window=60, max_keys=1
        )
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}
        self.auth = APIKeyAuth(
            store.lookup,
            pepper=pepper,
            header="X-API-Key",
            bearer=False,
            query_param=None,
            prefix="smcp-",
            require_checksum=True,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            try:
                await self.app(scope, receive, send)
            finally:
                self.store.close()
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            raise RuntimeError("Unsupported ASGI scope type")

        response_started = False

        async def secure_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message = {
                    **message,
                    "headers": [*message.get("headers", []), *_SECURITY_HEADERS],
                }
            await send(message)

        async def reject(status_code: int, error: str) -> None:
            await JSONResponse({"error": error}, status_code=status_code)(
                scope, receive, secure_send
            )

        if self.active_requests >= self.max_concurrency:
            await reject(503, "Server busy")
            return
        self.active_requests += 1
        try:
            try:
                async with asyncio.timeout(self.request_timeout):
                    rejection = await self._check(scope)
                    if rejection is not None:
                        await reject(*rejection)
                        return
                    try:
                        body = await _read_body(receive)
                    except _BodyTooLarge:
                        await reject(413, "Request body too large")
                        return
                    except _ClientDisconnected:
                        return
                    await self.app(scope, _replay(body, receive), secure_send)
            except TimeoutError:
                logging.getLogger(__name__).warning("MCP request exceeded its time limit")
                if not response_started:
                    await reject(504, "Request timed out")
        finally:
            self.active_requests -= 1

    async def _check(self, scope: Scope) -> tuple[int, str] | None:
        connection = HTTPConnection(scope)
        if connection.headers.get("host", "") not in self.allowed_hosts:
            return 421, "Invalid host"
        origin = connection.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return 403, "Invalid origin"
        if not self.unauthenticated_rate.hit("all"):
            return 429, "Rate limit exceeded"
        content_length = connection.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return 400, "Invalid request length"
            if declared_length < 0:
                return 400, "Invalid request length"
            if declared_length > MAX_REQUEST_BODY_BYTES:
                return 413, "Request body too large"
        try:
            record = await self.auth.aauthenticate(connection.headers.get("X-API-Key"))
        except AuthError:
            return 401, "Unauthorized"
        except Exception:
            logging.getLogger(__name__).exception("MCP caller authentication failed")
            return 503, "Authentication service unavailable"
        if not self.rate.hit(str(record.id)):
            return 429, "Rate limit exceeded"
        return None


async def _read_body(receive: Receive) -> bytes:
    """Read the whole request body, whatever its transfer encoding, up to the limit."""

    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        body.extend(message.get("body", b""))
        if len(body) > MAX_REQUEST_BODY_BYTES:
            raise _BodyTooLarge
        if not message.get("more_body", False):
            return bytes(body)


def _replay(body: bytes, receive: Receive) -> Receive:
    delivered = False

    async def replay() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
