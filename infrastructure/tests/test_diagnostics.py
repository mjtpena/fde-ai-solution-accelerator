"""Storage services send their logs and transaction metrics to Log Analytics."""

import re
from pathlib import Path

import pytest

STORAGE = Path(__file__).resolve().parents[1] / "modules" / "storage.bicep"


def diagnostic_settings() -> dict[str, str]:
    """Each diagnostic setting in storage.bicep, keyed by the resource it is scoped to."""
    storage = STORAGE.read_text(encoding="utf-8")
    declarations = re.split(r"^resource ", storage, flags=re.MULTILINE)[1:]
    settings = {}
    for declaration in declarations:
        if "Microsoft.Insights/diagnosticSettings" in declaration.splitlines()[0]:
            scope = re.search(r"^  scope: (\w+)$", declaration, flags=re.MULTILINE)
            assert scope, declaration
            settings[scope.group(1)] = declaration
    return settings


@pytest.mark.parametrize("service", ["blobService", "queueService"])
def test_storage_service_logs_and_transactions_reach_log_analytics(service: str) -> None:
    setting = diagnostic_settings()[service]

    assert "workspaceId: logAnalyticsWorkspaceId" in setting
    assert "categoryGroup: 'allLogs'" in setting
    assert "category: 'Transaction'" in setting


def test_storage_diagnostic_setting_names_are_unique() -> None:
    names = re.findall(
        r"^  name: '([^']+)'$",
        "\n".join(diagnostic_settings().values()),
        flags=re.MULTILINE,
    )

    assert len(names) == len(set(names)) == 2
