import asyncio
import os
import unittest
from uuid import UUID
from unittest.mock import patch

from pydantic import ValidationError

from accelerator.api.app import create_app
from accelerator.api.health import HealthResponse, healthz, readyz
from accelerator.configuration.settings import Settings


def make_test_settings() -> Settings:
    return Settings(
        environment="test",
        entra_tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        entra_audience="api://test",
    )


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(make_test_settings())

    def test_healthz_returns_ok(self) -> None:
        operation = self.app.openapi()["paths"]["/healthz"]["get"]

        self.assertIn("200", operation["responses"])
        self.assertEqual(asyncio.run(healthz()), HealthResponse(status="ok"))

    def test_readyz_returns_ready(self) -> None:
        operation = self.app.openapi()["paths"]["/readyz"]["get"]

        self.assertIn("200", operation["responses"])
        self.assertEqual(asyncio.run(readyz()), HealthResponse(status="ready"))


class SettingsTests(unittest.TestCase):
    def test_missing_environment_fails_validation(self) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValidationError):
            Settings()

    def test_empty_environment_fails_validation(self) -> None:
        with patch.dict(os.environ, {"API_ENVIRONMENT": ""}, clear=True), self.assertRaises(
            ValidationError
        ):
            Settings()


if __name__ == "__main__":
    unittest.main()
