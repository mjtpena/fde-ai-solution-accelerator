import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime


logger = logging.getLogger("ingestion_worker")


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


async def run_worker(stop_event: asyncio.Event) -> None:
    log_event("worker_started")
    try:
        await stop_event.wait()
    finally:
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

    try:
        await run_worker(stop_event)
    finally:
        for handled_signal in registered_signals:
            loop.remove_signal_handler(handled_signal)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("INGESTION_LOG_LEVEL", "INFO").upper(), format="%(message)s")
    asyncio.run(main())
