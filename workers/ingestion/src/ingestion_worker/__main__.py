import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from .composition import compose_consumer
from .settings import WorkerSettings


logger = logging.getLogger("ingestion_worker")

HEARTBEAT_INTERVAL_SECONDS = 10.0
# The container healthcheck fails once the heartbeat is older than this.
HEARTBEAT_MAX_AGE_SECONDS = 60.0


def heartbeat_path() -> Path:
    return Path(os.getenv("INGESTION_HEARTBEAT_FILE", "/tmp/ingestion-heartbeat"))


def heartbeat_is_fresh(path: Path, *, max_age_seconds: float = HEARTBEAT_MAX_AGE_SECONDS) -> bool:
    try:
        return time.time() - path.stat().st_mtime <= max_age_seconds
    except FileNotFoundError:
        return False


async def heartbeat(stop_event: asyncio.Event, path: Path) -> None:
    """Prove the event loop is still making progress, for the container healthcheck."""
    while not stop_event.is_set():
        path.touch()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            continue


def log_event(event: str) -> None:
    logger.info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": "INFO",
                "event": event,
                "worker": "ingestion",
            }
        )
    )


async def run_worker(
    stop_event: asyncio.Event,
    *,
    heartbeat_file: Path | None = None,
    consume: Callable[[asyncio.Event], Awaitable[None]] | None = None,
) -> None:
    log_event("worker_started")
    beat = asyncio.create_task(heartbeat(stop_event, heartbeat_file or heartbeat_path()))
    try:
        if consume is None:
            await stop_event.wait()
        else:
            await consume(stop_event)
    finally:
        beat.cancel()
        log_event("worker_stopped")


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []

    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(handled_signal, stop_event.set)
        except NotImplementedError:
            continue
        registered_signals.append(handled_signal)

    settings = WorkerSettings()
    try:
        async with compose_consumer(settings) as consumer:
            await run_worker(stop_event, consume=consumer.run if consumer else None)
    finally:
        for handled_signal in registered_signals:
            loop.remove_signal_handler(handled_signal)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("INGESTION_LOG_LEVEL", "INFO").upper(), format="%(message)s")
    asyncio.run(main())
