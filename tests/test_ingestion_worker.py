import asyncio
import io
import json
import logging
import unittest

from accelerator.ingestion.__main__ import run_worker


class IngestionWorkerTests(unittest.TestCase):
    def test_worker_logs_structured_start_and_stop_events(self) -> None:
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        worker_logger = logging.getLogger("ingestion_worker")
        original_level = worker_logger.level
        worker_logger.setLevel(logging.INFO)
        worker_logger.addHandler(handler)
        stop_event = asyncio.Event()

        async def run_and_stop() -> None:
            task = asyncio.create_task(run_worker(stop_event))
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
