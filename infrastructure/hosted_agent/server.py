"""Foundry Invocations adapter; application logic and identity live behind a port."""

import logging
from importlib import import_module
from uuid import uuid4

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from accelerator.agent_core.hosting.contracts import (
    HostedApplication,
    InvocationRequest,
    InvocationUnauthorized,
)
from infrastructure.hosted_agent.configuration import RuntimeSettings

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 65536


def create_host(application: HostedApplication) -> InvocationAgentServerHost:
    host = InvocationAgentServerHost()

    @host.invoke_handler
    async def invoke(request: Request) -> Response:
        correlation_id = str(uuid4())
        headers = {"x-correlation-id": correlation_id}
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BYTES:
                logger.warning("hosted_request_too_large", extra={"correlation_id": correlation_id})
                return JSONResponse(
                    {"error": "request_too_large"}, status_code=413, headers=headers
                )
        try:
            invocation = InvocationRequest.model_validate_json(body)
        except ValidationError:
            logger.warning("hosted_request_invalid", extra={"correlation_id": correlation_id})
            return JSONResponse({"error": "invalid_request"}, status_code=422, headers=headers)
        try:
            result = await application.invoke(
                invocation.query, request.headers.get("authorization")
            )
        except InvocationUnauthorized:
            logger.warning("hosted_request_unauthorized", extra={"correlation_id": correlation_id})
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)
        logger.info("hosted_invocation_completed", extra={"correlation_id": correlation_id})
        return JSONResponse(result.model_dump(mode="json"), headers=headers)

    return host


def load_application(settings: RuntimeSettings) -> HostedApplication:
    module_name, factory_name = settings.application_factory.split(":")
    factory = getattr(import_module(module_name), factory_name)
    application: HostedApplication = factory()
    if not callable(getattr(application, "invoke", None)):
        raise TypeError("HOSTED_APPLICATION_FACTORY must return a HostedApplication")
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = RuntimeSettings()
    # The hosted-agent container must accept traffic from the platform ingress.
    create_host(load_application(settings)).run(host="0.0.0.0", port=8088)  # noqa: S104


if __name__ == "__main__":
    main()
