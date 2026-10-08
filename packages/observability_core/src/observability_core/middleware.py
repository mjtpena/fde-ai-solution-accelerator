from contextlib import ExitStack
from uuid import uuid4

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    ASGISendEvent,
    Scope,
)
from opentelemetry.context import Context
from opentelemetry.trace import Span, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .tracing import Telemetry


class TracingMiddleware:
    def __init__(self, app: ASGI3Application, telemetry: Telemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Propagate only W3C trace IDs, never baggage, URLs, headers, or request content.
        headers = [
            value.decode("ascii", errors="replace")
            for key, value in scope["headers"]
            if key.lower() == b"traceparent" and len(value) <= 512
        ]
        parent = Context()
        if len(headers) == 1:
            parent = TraceContextTextMapPropagator().extract(
                {"traceparent": headers[0]}, context=Context()
            )
        correlation_id = uuid4()
        scope.setdefault("state", {})["correlation_id"] = str(correlation_id)
        with (
            self.telemetry.request(
                correlation_id, parent=parent, method=scope["method"]
            ) as request_span,
            ExitStack() as responses,
        ):
            response_span: Span = responses.enter_context(self.telemetry.response(request_span))

            async def traced_send(message: ASGISendEvent) -> None:
                if message["type"] == "http.response.start":
                    status = message["status"]
                    request_span.set_attribute("http.response.status_code", status)
                    response_span.set_attribute("http.response.status_code", status)
                    if status >= 500:
                        request_span.set_status(StatusCode.ERROR)
                        response_span.set_status(StatusCode.ERROR)
                    message = {
                        **message,
                        "headers": [
                            (key, value)
                            for key, value in message.get("headers", [])
                            if key.lower() != b"x-correlation-id"
                        ] + [(b"x-correlation-id", str(correlation_id).encode("ascii"))],
                    }
                await send(message)

            await self.app(scope, receive, traced_send)
