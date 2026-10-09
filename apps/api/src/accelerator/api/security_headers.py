"""Response security headers for a JSON/SSE API that is never rendered as a page."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-site",
}


def json_error_headers(**extra: str) -> dict[str, str]:
    """Headers for JSON responses produced outside ``SecurityHeadersMiddleware``."""
    return {**API_SECURITY_HEADERS, "Cache-Control": "no-store", **extra}


class SecurityHeadersMiddleware:
    """Add security headers to every response; JSON is never cached."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in API_SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if headers.get("content-type", "").startswith("application/json"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
