import importlib
import importlib.metadata
import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = {
    "apps/api": ("fde-accelerator-api", "accelerator.api"),
    "packages/agent_core": ("fde-agent-core", "accelerator.agent_core.tools"),
    "packages/retrieval_core": ("fde-retrieval-core", "accelerator.retrieval_core.models"),
    "packages/evaluation_core": ("fde-evaluation-core", "accelerator.evaluation_core.runners"),
    "packages/observability_core": ("fde-observability-core", "accelerator.observability_core"),
    "packages/security_core": ("fde-security-core", "accelerator.security_core.tool_policy"),
    "workers/ingestion": ("fde-ingestion-worker", "accelerator.ingestion"),
}


class ScaffoldTests(unittest.TestCase):
    def test_workspace_members_are_installed_and_importable(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(set(config["tool"]["uv"]["workspace"]["members"]), set(MEMBERS))
        self.assertEqual(config["project"]["requires-python"], "==3.12.*")
        for member, (distribution, module) in MEMBERS.items():
            with self.subTest(member=member):
                project = tomllib.loads(
                    (ROOT / member / "pyproject.toml").read_text(encoding="utf-8")
                )["project"]
                self.assertEqual(project["name"], distribution)
                self.assertEqual(project["requires-python"], "==3.12.*")
                self.assertEqual(importlib.metadata.version(distribution), project["version"])
                importlib.import_module(module)

    def test_spec_scaffold_directories_exist(self) -> None:
        for directory in (
            "apps/web", "contracts", "evaluations/example-datasets",
            "evaluations/rubrics", "threat-model/test-cases", "scripts", "docs/adr",
            "engagement/templates", "engagement/examples/fictional-engagement",
        ):
            with self.subTest(directory=directory):
                self.assertTrue((ROOT / directory).is_dir())

    def test_npm_lock_contains_web_workspace(self) -> None:
        manifest = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["private"])
        self.assertEqual(manifest["workspaces"], ["apps/web"])
        self.assertEqual(lock["packages"][""]["workspaces"], manifest["workspaces"])
        self.assertIn("apps/web", lock["packages"])

    def test_license_and_evaluation_smoke_entrypoint_are_preserved(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Michael John Pe\u00f1a", license_text)
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("python -m accelerator.evaluation_core.reporting.smoke", makefile)


if __name__ == "__main__":
    unittest.main()
