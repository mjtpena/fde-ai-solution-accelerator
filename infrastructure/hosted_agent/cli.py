"""Explicit data-plane deploy/smoke commands; no provisioning at import time."""

import argparse
import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from infrastructure.hosted_agent.azure_adapter import open_gateway
from infrastructure.hosted_agent.configuration import DeploymentSettings
from infrastructure.hosted_agent.service import deploy, smoke

logger = logging.getLogger(__name__)


async def execute(command: str, settings: DeploymentSettings) -> None:
    correlation_id = str(uuid4())
    async with open_gateway(settings) as gateway:
        if command == "deploy":
            version = await deploy(gateway, settings)
            logger.info(
                "hosted_version_active",
                extra={"correlation_id": correlation_id, "agent_version": version.version},
            )
        else:
            result = await smoke(gateway, settings.agent_name, settings.timeout_seconds)
            logger.info(
                "hosted_smoke_passed",
                extra={"correlation_id": correlation_id, "outcome": result.status},
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy or invoke a Foundry hosted agent")
    parser.add_argument("command", choices=["deploy", "smoke"])
    parser.add_argument("--config", type=Path, help="DeploymentSettings JSON file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = (
        DeploymentSettings.model_validate_json(args.config.read_text(encoding="utf-8"))
        if args.config
        else DeploymentSettings()
    )
    asyncio.run(execute(args.command, settings))


if __name__ == "__main__":
    main()
