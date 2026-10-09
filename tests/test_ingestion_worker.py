import asyncio
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from accelerator.ingestion import __main__ as worker_main
from accelerator.ingestion import healthcheck
from accelerator.ingestion.__main__ import heartbeat_is_fresh, run_worker


class IngestionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.heartbeat = Path(directory.name) / "heartbeat"

    def test_worker_logs_structured_start_and_stop_events(self) -> None:
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        worker_logger = logging.getLogger("ingestion_worker")
        original_level = worker_logger.level
        worker_logger.setLevel(logging.INFO)
        worker_logger.addHandler(handler)
        stop_event = asyncio.Event()

        async def run_and_stop() -> None:
            task = asyncio.create_task(run_worker(stop_event, heartbeat_file=self.heartbeat))
            await asyncio.sleep(0)
            stop_event.set()
            await task

        try:
            asyncio.run(run_and_stop())
        finally:
            worker_logger.removeHandler(handler)
            worker_logger.setLevel(original_level)

        entries = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([entry["event"] for entry in entries], ["worker_started", "worker_stopped"])
        self.assertTrue(all(entry["worker"] == "ingestion" for entry in entries))
        self.assertTrue(all(entry["level"] == "INFO" for entry in entries))

    def test_running_worker_writes_a_fresh_heartbeat(self) -> None:
        stop_event = asyncio.Event()

        async def run_briefly() -> bool:
            task = asyncio.create_task(run_worker(stop_event, heartbeat_file=self.heartbeat))
            await asyncio.sleep(0.05)
            fresh = heartbeat_is_fresh(self.heartbeat)
            stop_event.set()
            await task
            return fresh

        self.assertTrue(asyncio.run(run_briefly()))

    def test_heartbeat_keeps_refreshing_while_the_worker_runs(self) -> None:
        stop_event = asyncio.Event()

        async def run_and_age() -> bool:
            with patch.object(worker_main, "HEARTBEAT_INTERVAL_SECONDS", 0.01):
                task = asyncio.create_task(
                    run_worker(stop_event, heartbeat_file=self.heartbeat)
                )
                await asyncio.sleep(0.02)
                os.utime(self.heartbeat, (0, 0))  # pretend the loop stalled long ago
                self.assertFalse(heartbeat_is_fresh(self.heartbeat))
                await asyncio.sleep(0.05)  # several intervals
                refreshed = heartbeat_is_fresh(self.heartbeat)
                stop_event.set()
                await task
            return refreshed

        self.assertTrue(asyncio.run(run_and_age()))

    def test_stale_or_missing_heartbeat_fails_the_healthcheck(self) -> None:
        with patch.dict(os.environ, {"INGESTION_HEARTBEAT_FILE": str(self.heartbeat)}):
            self.assertEqual(healthcheck.main(), 1)
            self.heartbeat.touch()
            self.assertEqual(healthcheck.main(), 0)
            os.utime(self.heartbeat, (0, 0))
            self.assertEqual(healthcheck.main(), 1)
