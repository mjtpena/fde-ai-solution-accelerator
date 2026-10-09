"""deploy-dev.sh drives the Azure CLI in the right order with the right inputs.

Fake `az`, `curl` and `uv` executables record their calls; nothing reaches Azure.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infrastructure" / "scripts" / "deploy-dev.sh"
DIGEST = "sha256:" + "a" * 64

FAKE_AZ = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
with open(log, "a") as handle:
    handle.write(json.dumps({"tool": "az", "args": args,
        "deploy_applications": os.environ.get("DEPLOY_APPLICATIONS"),
        "api_image": os.environ.get("API_IMAGE")}) + "\n")
outputs = json.loads(os.environ["FAKE_OUTPUTS"])
joined = " ".join(args)
if "deployment sub show" in joined:
    query = args[args.index("--query") + 1]
    key = query.removeprefix("properties.outputs.").removesuffix(".value")
    if key == "containerAppNames.value.migrations":
        print("fde-dev-migrate")
    else:
        print(outputs[key])
elif "acr repository show" in joined:
    print(os.environ["FAKE_DIGEST"])
elif "containerapp job start" in joined:
    print("execution-1")
elif "containerapp job execution show" in joined:
    print(os.environ.get("FAKE_MIGRATION_STATUS", "Succeeded"))
"""

FAKE_RECORDER = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as handle:
    handle.write(json.dumps({"tool": os.path.basename(sys.argv[0]), "args": sys.argv[1:],
        "search": [os.environ.get(k) for k in (
            "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_INDEX_NAME", "AZURE_SEARCH_VECTOR_DIMENSIONS")]})
        + "\n")
is_curl = os.path.basename(sys.argv[0]) == "curl"
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")) if is_curl else 0)
"""


def install(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    install(bin_dir, "az", FAKE_AZ)
    install(bin_dir, "curl", FAKE_RECORDER)
    install(bin_dir, "uv", FAKE_RECORDER)
    values = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_LOG": str(tmp_path / "calls.jsonl"),
        "FAKE_DIGEST": DIGEST,
        "FAKE_OUTPUTS": json.dumps(
            {
                "registryName": "fdedevacr",
                "registryLoginServer": "fdedevacr.azurecr.io",
                "resourceGroupName": "fde-dev-rg",
                "searchEndpoint": "https://search.example.test",
                "searchIndexName": "chunks",
                "searchVectorDimensions": "1536",
                "webUrl": "https://web.example.test",
            }
        ),
        "DEPLOY_STATE_DIR": str(tmp_path / "state"),
        "DEPLOYMENT_NAME": "fde-dev-test",
        "SMOKE_ATTEMPTS": "1",
        "SMOKE_INTERVAL_SECONDS": "0",
        "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000001",
        "AZURE_LOCATION": "eastus",
        "AZURE_POSTGRES_ADMIN_OBJECT_ID": "00000000-0000-0000-0000-000000000002",
        "AZURE_POSTGRES_ADMIN_NAME": "db-admins",
        "AZURE_POSTGRES_ADMIN_PRINCIPAL_TYPE": "Group",
        "API_ENTRA_TENANT_ID": "00000000-0000-0000-0000-000000000003",
        "API_ENTRA_AUDIENCE": "api://accelerator",
        "WEB_ENTRA_CLIENT_ID": "00000000-0000-0000-0000-000000000004",
        "WEB_ENTRA_API_SCOPE": "api://accelerator/access_as_user",
    }
    values.update(overrides)
    return values


def run(tmp_path: Path, *args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env=environment(tmp_path, **overrides),
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def az_commands(tmp_path: Path) -> list[str]:
    return [
        " ".join(str(arg) for arg in call["args"][:3])  # type: ignore[index]
        for call in calls(tmp_path)
        if call["tool"] == "az"
    ]


def test_all_deploys_builds_migrates_provisions_and_smoke_tests_in_order(tmp_path: Path) -> None:
    result = run(tmp_path, "all")

    assert result.returncode == 0, result.stderr
    commands = az_commands(tmp_path)
    stages = [
        commands.index("deployment sub create"),
        commands.index("acr build --registry"),
        len(commands) - 1 - commands[::-1].index("deployment sub create"),
        commands.index("containerapp job start"),
    ]
    assert stages == sorted(stages)
    deployments = [c for c in calls(tmp_path) if c["args"][:3] == ["deployment", "sub", "create"]]
    assert [d["deploy_applications"] for d in deployments] == ["false", "true"]
    assert deployments[1]["api_image"] == f"fdedevacr.azurecr.io/api@{DIGEST}"
    builds = [c["args"] for c in calls(tmp_path) if c["args"][:2] == ["acr", "build"]]
    assert sorted(b[b.index("--image") + 1].split(":")[0] for b in builds) == [
        "api",
        "web",
        "worker",
    ]
    client_id = "NEXT_PUBLIC_ENTRA_CLIENT_ID=00000000-0000-0000-0000-000000000004"
    assert any(client_id in build for build in builds)
    [provision] = [c for c in calls(tmp_path) if c["tool"] == "uv"]
    assert provision["search"] == ["https://search.example.test", "chunks", "1536"]
    smoke = [c["args"][-1] for c in calls(tmp_path) if c["tool"] == "curl"]
    assert smoke == ["https://web.example.test/", "https://web.example.test/api/health"]


def test_missing_configuration_fails_before_touching_azure(tmp_path: Path) -> None:
    result = run(tmp_path, "infrastructure", AZURE_POSTGRES_ADMIN_NAME="")

    assert result.returncode != 0
    assert "AZURE_POSTGRES_ADMIN_NAME" in result.stderr
    assert calls(tmp_path) == []


@pytest.mark.parametrize("status", ["Failed", "Stopped"])
def test_a_failed_migration_fails_the_deployment(tmp_path: Path, status: str) -> None:
    result = run(tmp_path, "migrate", FAKE_MIGRATION_STATUS=status)

    assert result.returncode != 0
    assert status in result.stderr


def test_applications_require_immutable_image_digests(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        "applications",
        API_IMAGE="fdedevacr.azurecr.io/api:latest",
        WEB_IMAGE=f"fdedevacr.azurecr.io/web@{DIGEST}",
        WORKER_IMAGE=f"fdedevacr.azurecr.io/worker@{DIGEST}",
    )

    assert result.returncode != 0
    assert "sha256" in result.stderr
    assert "deployment sub create" not in az_commands(tmp_path)


def test_smoke_fails_when_the_web_or_api_is_unreachable(tmp_path: Path) -> None:
    result = run(tmp_path, "smoke", FAKE_CURL_EXIT="22")

    assert result.returncode != 0
    assert "smoke test failed" in result.stderr


def test_make_and_the_workflow_share_the_script() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")

    assert "deploy-dev:\n\tinfrastructure/scripts/deploy-dev.sh $(STAGE)" in makefile
    for stage in ("applications", "migrate", "index", "smoke"):
        assert f"infrastructure/scripts/deploy-dev.sh {stage}" in workflow
