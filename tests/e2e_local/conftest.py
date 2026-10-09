"""Opt-in gate for the real-process end-to-end suite (``make e2e-local``).

The suite starts the API and the ingestion worker as real processes against
PostgreSQL and Azurite, so it is collected only when ``E2E_LOCAL=1``. Once opted
in, missing services are an error, never a skip: CI sets ``E2E_LOCAL=1`` next to
the service containers, so a broken service fails the job instead of silently
passing it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REQUIRED = ("TEST_POSTGRES_DSN", "TEST_AZURITE_CONNECTION_STRING")
ENABLED = os.environ.get("E2E_LOCAL") == "1"

# Not collected at all unless opted in, so the default ``pytest`` run is unaffected.
collect_ignore_glob = [] if ENABLED else ["test_*.py"]


@pytest.fixture(scope="session", autouse=True)
def required_services() -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.fail(
            f"E2E_LOCAL=1 needs {', '.join(missing)}; start PostgreSQL and Azurite "
            "(see docs/testing-strategy.md). The suite never skips itself.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def server_dsn() -> str:
    return os.environ["TEST_POSTGRES_DSN"]


@pytest.fixture(scope="session")
def azurite_connection_string() -> str:
    return os.environ["TEST_AZURITE_CONNECTION_STRING"]


@pytest.fixture(scope="session")
def process_logs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Child-process logs; kept under ``E2E_LOCAL_LOG_DIR`` when set (CI uploads them)."""
    configured = os.environ.get("E2E_LOCAL_LOG_DIR")
    directory = Path(configured) if configured else tmp_path_factory.mktemp("e2e-logs")
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
