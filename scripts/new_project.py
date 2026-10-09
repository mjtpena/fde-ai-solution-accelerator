"""Generate an independent project; the manifest uses JSON-compatible YAML.

Usage: make new-project NAME=my-solution TITLE="My Solution" [DEST=absolute-path]
TITLE is required. (It is not called DISPLAY: that is the X11 display variable, set in
most desktop shells, and would silently become the project title.)
The default destination is a sibling of the accelerator checkout. Existing destinations
are never overwritten. A failed dependency install/check leaves the output for diagnosis
and exits nonzero. Only a successful make check counts as a generated project.

Fixture tests live here to keep issue #37 within its three-file scope.
"""

from __future__ import annotations

import argparse
import ast
import json
import keyword
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
BEGIN_GENERATOR = "# BEGIN PROJECT GENERATOR"
END_GENERATOR = "# END PROJECT GENERATOR"
# Lines between these markers (any text file) exist only in the accelerator. Markdown
# uses the HTML-comment form, which renders as nothing instead of a heading.
BEGIN_ACCELERATOR_ONLY = "# BEGIN ACCELERATOR ONLY"
END_ACCELERATOR_ONLY = "# END ACCELERATOR ONLY"
BEGIN_ACCELERATOR_ONLY_MARKERS = frozenset(
    {BEGIN_ACCELERATOR_ONLY, "<!-- BEGIN ACCELERATOR ONLY -->"}
)
END_ACCELERATOR_ONLY_MARKERS = frozenset({END_ACCELERATOR_ONLY, "<!-- END ACCELERATOR ONLY -->"})
WORKFLOWS = Path(".github/workflows")
THREAT_MODEL_CONTROLS = Path("threat-model/controls.yml")
TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".json",
    ".jsonl",
    ".lock",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".md",
    ".yml",
    ".yaml",
    ".css",
    ".txt",
    ".example",
    ".ini",
    ".cfg",
}


def relative_path(value: str) -> Path:
    """Use portable manifest paths and reject drive, UNC, traversal and ADS paths."""
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"Unsafe manifest path: {value!r}")
    return Path(*path.parts)


def string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Manifest {field} must be an array of strings")
    return tuple(value)


@dataclass(frozen=True)
class Manifest:
    version_file: Path
    copy: tuple[Path, ...]
    exclude_names: frozenset[str]
    remove: tuple[Path, ...]
    renames: Mapping[str, str]
    templates: Path
    project_docs: Path
    dataset: Path
    starter_row: Mapping[str, object]
    workflows: frozenset[str]

    @classmethod
    def load(cls, source: Path) -> Manifest:
        raw: object = json.loads((source / "accelerator.manifest.yml").read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Expected a JSON-compatible YAML manifest with schema_version 1")

        def path_field(field: str) -> Path:
            value = raw.get(field)
            if not isinstance(value, str):
                raise ValueError(f"Manifest {field} must be a relative path")
            return relative_path(value)

        renames = raw.get("renames")
        if (
            not isinstance(renames, dict)
            or not renames
            or not all(
                isinstance(key, str) and key and isinstance(value, str)
                for key, value in renames.items()
            )
        ):
            raise ValueError("Manifest renames must map nonempty strings to strings")
        excludes = string_list(raw.get("exclude_names"), "exclude_names")
        for name in excludes:
            if len(relative_path(name).parts) != 1:
                raise ValueError("exclude_names entries must be single path components")
        row = raw.get("starter_row")
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "id",
                "category",
                "query",
                "scope_id",
                "expected_answer",
                "expected_evidence_ids",
                "expected_tool",
                "expected_abstain",
                "tags",
            }
            or not all(
                isinstance(row[key], str) and row[key]
                for key in (
                    "id",
                    "category",
                    "query",
                    "scope_id",
                )
            )
            or row["category"] != "unsupported"
            or row["expected_answer"] is not None
            or row["expected_tool"] is not None
            or row["expected_evidence_ids"] != []
            or row["expected_abstain"] is not True
        ):
            raise ValueError("starter_row must be a schema-valid unsupported/abstention fixture")
        string_list(row["tags"], "starter_row.tags")
        manifest = cls(
            version_file=path_field("version_file"),
            copy=tuple(relative_path(p) for p in string_list(raw.get("copy"), "copy")),
            exclude_names=frozenset(excludes),
            remove=tuple(relative_path(p) for p in string_list(raw.get("remove"), "remove")),
            renames=renames,
            templates=path_field("templates"),
            project_docs=path_field("project_docs"),
            dataset=path_field("dataset"),
            starter_row=row,
            workflows=frozenset(string_list(raw.get("workflows"), "workflows")),
        )
        if any(len(relative_path(name).parts) != 1 for name in manifest.workflows):
            raise ValueError("workflows entries must be file names in .github/workflows")
        if not manifest.copy or len(set(manifest.copy)) != len(manifest.copy):
            raise ValueError("Manifest copy paths must be nonempty and unique")
        return manifest


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def validate_name(name: str) -> str:
    module = name.replace("-", "_")
    if (
        not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name)
        or len(name) > 64
        or keyword.iskeyword(module)
        or module in sys.stdlib_module_names
        or module == "accelerator"
        or name.split("-")[0].upper() in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"(?:com|lpt)[1-9]", name)
    ):
        raise ValueError("NAME must be a non-reserved lowercase kebab-case name (max 64 chars)")
    return module


def replacement_function(
    manifest: Manifest,
    name: str,
    module: str,
    display: str,
) -> Mapping[str, str]:
    return {
        old: new.format(name=name, module=module, display=display)
        for old, new in manifest.renames.items()
    }


def replace_text(text: str, replacements: Mapping[str, str]) -> str:
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True))
        + r")(?!\w)"
    )
    return re.sub(pattern, lambda match: replacements[match.group()], text)


def renamed_path(relative: Path, renames: Mapping[str, str]) -> Path:
    """Apply manifest renames whose keys are paths (contain ``/``) to a copied path."""
    for old, new in sorted(renames.items(), key=lambda item: len(item[0]), reverse=True):
        if "/" in old and relative.is_relative_to(old):
            return Path(new) / relative.relative_to(old)
    return relative


def relax_line_length(pyproject: Path, growth: int) -> None:
    """Allow E501 exactly the extra width the rename added to any Python line.

    Longer project names lengthen lines that fit the accelerator's limit; this keeps
    a generated project lint-clean without loosening anything else.
    """
    if growth <= 0 or not pyproject.is_file():
        return
    text = pyproject.read_text(encoding="utf-8")
    ruff = tomllib.loads(text).get("tool", {}).get("ruff", {})
    if not ruff:
        return
    limit = int(ruff.get("line-length", 88)) + growth
    pyproject.write_text(
        text.rstrip("\n")
        + "\n\n[tool.ruff.lint.pycodestyle]\n"
        + "# Widened by the project generator: the rename lengthened some lines.\n"
        + f"max-line-length = {limit}\n",
        encoding="utf-8",
    )


def strip_accelerator_only(text: str, path: Path) -> str:
    """Drop every BEGIN/END ACCELERATOR ONLY block, markers included."""
    if "ACCELERATOR ONLY" not in text:
        return text
    kept: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        marker = line.strip()
        if marker in BEGIN_ACCELERATOR_ONLY_MARKERS:
            if inside:
                raise ValueError(f"Nested accelerator-only block in {path}")
            inside = True
        elif marker in END_ACCELERATOR_ONLY_MARKERS:
            if not inside:
                raise ValueError(f"Unmatched accelerator-only end marker in {path}")
            inside = False
        elif not inside:
            kept.append(line)
    if inside:
        raise ValueError(f"Unclosed accelerator-only block in {path}")
    return "".join(kept)


def is_utf8_text(path: Path) -> bool:
    """Treat a file without a known text suffix (CODEOWNERS, .gitignore) as text if it decodes."""
    data = path.read_bytes()
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def generated_makefile(text: str) -> str:
    if text.count(BEGIN_GENERATOR) != 1 or text.count(END_GENERATOR) != 1:
        raise ValueError("Makefile must contain exactly one project generator block")
    before, block = text.split(BEGIN_GENERATOR, 1)
    _, after = block.split(END_GENERATOR, 1)
    return before.rstrip() + "\n" + after


def generated_spec(text: str) -> str:
    """Remove accelerator-generator instructions that do not apply to generated projects."""
    replacements = (
        (
            r"(?ms)^## 10\. Project generator\n.*?(?=^## 11\. Delivery milestones)",
            "## 10. Project setup\n\n"
            "This project was generated from a reusable accelerator baseline. "
            "The application scaffold and package layout here are the project-owned "
            "starting point.\n\n",
            "project generator section",
        ),
        (
            r"(?m)^.*\| 36 \|.*(?:accelerator\.manifest\.yml|new_project\.py).*\n",
            "",
            "generator roadmap entry",
        ),
        (
            r"(?m)^.*accelerator\.manifest\.yml.*\n",
            "",
            "manifest tree entry",
        ),
        (
            r"(?m)^.*new_project\.py.*\n",
            "",
            "generator tree entry",
        ),
        (
            r"(?m)^\s*make new-project.*(?:\n|$)",
            "",
            "generator command",
        ),
    )
    for pattern, replacement, description in replacements:
        text, count = re.subn(pattern, replacement, text)
        if count != 1:
            raise ValueError(f"Expected one {description} in docs/spec.md, found {count}")
    return text


def generated_controls(text: str, present: Callable[[str], bool]) -> str:
    """Downgrade controls whose implementation the generated project did not receive.

    A control with some implementation paths missing (infrastructure/, deployment
    workflows) is at most ``partial``; with all of them missing it is ``planned``.
    Each downgrade explains itself in ``notes``.
    """
    blocks = re.split(r"(?m)^(?=  - id: )", text)
    for index, block in enumerate(blocks):
        status = re.search(r"(?m)^    status: (\w+)\n", block)
        implemented = re.search(r"(?m)^    implemented_in:\n((?:      - .+\n)+)", block)
        if status is None or implemented is None:
            continue
        paths = [line.strip().removeprefix("- ") for line in implemented[1].splitlines()]
        missing = [path for path in paths if not present(path)]
        new_status = (
            "planned"
            if len(missing) == len(paths)
            else "partial"
            if missing and status[1] == "implemented"
            else status[1]
        )
        if not missing or status[1] == "planned":
            continue
        note = textwrap.fill(
            f"Generated project: {len(missing)} of {len(paths)} implementation paths "
            f"(including {missing[0]}) were not generated, so that part of the control "
            "is not in force here.",
            width=88,
            initial_indent="      ",
            subsequent_indent="      ",
        )
        notes = re.search(r"(?m)^    notes:(.*)\n((?:      .+\n)*)", block)
        if notes is None:
            block = (
                block[: status.start()]
                + f"    status: {new_status}\n    notes: >-\n{note}\n"
                + block[status.end() :]
            )
        elif notes[1].strip() == ">-":
            block = block[: notes.end()] + note + "\n" + block[notes.end() :]
            block = re.sub(r"(?m)^    status: \w+$", f"    status: {new_status}", block, count=1)
        else:
            raise ValueError(f"Unsupported notes style in {THREAT_MODEL_CONTROLS}: {notes[0]!r}")
        blocks[index] = block
    return "".join(blocks)


def generate(source: Path, destination: Path, name: str, display: str) -> None:
    module = validate_name(name)
    if not display.strip() or any(ord(char) < 32 for char in display):
        raise ValueError("TITLE must be nonempty text without control characters")
    source = source.resolve(strict=True)
    # Reject aliases before resolve() can hide a symlink or junction.
    destination = destination.absolute()
    if ".." in destination.parts:
        raise ValueError("Destination must not contain traversal")
    for component in (destination, *destination.parents):
        if is_link(component):
            raise ValueError(f"Destination contains a link: {component}")
    destination = destination.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Destination must not overlap the source checkout")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise ValueError("Destination parent must be an existing directory")
    manifest_path = source / "accelerator.manifest.yml"
    if is_link(manifest_path):
        raise ValueError("Manifest must not be a link")
    manifest = Manifest.load(source)
    replacements = replacement_function(manifest, name, module, display)
    files: list[tuple[Path, Path]] = []
    directories: set[Path] = set()
    targets: set[Path] = set()

    def collect(relative: Path) -> None:
        if any(
            part in manifest.exclude_names
            or part.endswith((".egg-info", ".pyc", ".pyo", ".tsbuildinfo"))
            or (part.startswith(".env") and part != ".env.example")
            for part in relative.parts
        ) or any(relative.is_relative_to(removed) for removed in manifest.remove):
            return
        if relative.parent == WORKFLOWS and relative.name not in manifest.workflows:
            # Deployment and accelerator-maintenance workflows do not apply to a
            # generated project; it keeps only the checks listed in the manifest.
            return
        original = source / relative
        for component in (original, *original.parents):
            if component == source:
                break
            if is_link(component):
                raise ValueError(f"Source contains a link: {relative}")
        target = renamed_path(relative, manifest.renames)
        target = Path(*(module if part == "accelerator" else part for part in target.parts))
        if target in targets:
            raise ValueError(f"Overlapping copy paths or rename collision: {target}")
        targets.add(target)
        if original.is_dir():
            directories.add(target)
            for child in sorted(original.iterdir()):
                collect(child.relative_to(source))
        elif original.is_file():
            files.append((relative, target))
        else:
            raise ValueError(f"Missing or unsupported source entry: {relative}")

    for entry in manifest.copy:
        collect(entry)
    copies_workflows = any(WORKFLOWS.is_relative_to(entry) for entry in manifest.copy)
    for workflow in sorted(manifest.workflows) if copies_workflows else ():
        if not (source / WORKFLOWS / workflow).is_file():
            raise ValueError(f"Manifest workflow does not exist: {workflow}")
    version_path = source / manifest.version_file
    if is_link(version_path) or not version_path.resolve().is_relative_to(source):
        raise ValueError("Version file must be inside the source and not a link")
    version: object = tomllib.loads(version_path.read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version
    ):
        raise ValueError("Accelerator version must be a semantic version string")

    copied_files = [original for original, _ in files]

    def copied(path: str) -> bool:
        wanted = relative_path(path.rstrip("/"))
        return any(original.is_relative_to(wanted) for original in copied_files)

    destination.mkdir()
    line_growth = 0
    for directory in sorted(directories, key=lambda path: len(path.parts)):
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for original, target in files:
        output = destination / target
        output.parent.mkdir(parents=True, exist_ok=True)
        if (
            original.suffix in TEXT_SUFFIXES
            or original.name in {"Makefile", "Dockerfile"}
            or is_utf8_text(source / original)
        ):
            text = (source / original).read_text(encoding="utf-8")
            text = strip_accelerator_only(text, original)
            if original == Path("Makefile"):
                text = generated_makefile(text)
            text_replacements = dict(replacements)
            if original.suffix in {
                ".json",
                ".jsonl",
                ".toml",
                ".lock",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".mjs",
            }:
                text_replacements["FDE AI Solution Accelerator"] = json.dumps(
                    display, ensure_ascii=False
                )[1:-1]
            elif original.suffix == ".py":
                text_replacements["FDE AI Solution Accelerator"] = (
                    display.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                )
            if original == Path("docs/spec.md"):
                text = generated_spec(text)
            if original == THREAT_MODEL_CONTROLS:
                text = generated_controls(text, copied)
            renamed = replace_text(text, text_replacements)
            if original.suffix == ".py":
                line_growth = max(
                    line_growth,
                    max(
                        (
                            len(new) - len(old)
                            for old, new in zip(
                                text.splitlines(), renamed.splitlines(), strict=True
                            )
                        ),
                        default=0,
                    ),
                )
            output.write_text(renamed, encoding="utf-8")
            shutil.copymode(source / original, output)
        else:
            shutil.copy2(source / original, output)
    docs = destination / manifest.project_docs
    docs.mkdir(parents=True, exist_ok=True)
    templates = destination / manifest.templates
    for template in sorted(templates.rglob("*.md")):
        output = docs / template.relative_to(templates)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Preserve template headings and instructions, not fictional example answers.
        output.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    dataset = destination / manifest.dataset
    dataset.parent.mkdir(parents=True, exist_ok=True)
    if dataset.exists():
        raise FileExistsError(f"Starter dataset would overwrite an existing file: {dataset}")
    dataset.write_text(json.dumps(manifest.starter_row) + "\n", encoding="utf-8")
    (destination / "ACCELERATOR_VERSION").write_text(version + "\n", encoding="utf-8")
    (destination / "README.md").write_text(
        f"# {display}\n\nGenerated from FDE AI Solution Accelerator {version}.\n\n"
        f"Python namespace: `{module}`. This is a scaffold, not a production certification.\n\n"
        "## Development\n\nRequires Python 3.12, uv, Node.js 22, npm and GNU Make.\n\n"
        "```sh\nuv sync --all-packages --frozen\nnpm ci\nmake check\nmake eval-smoke\n```\n\n"
        f"Fill in `{manifest.project_docs.as_posix()}` from the engagement templates. "
        f"Replace `{manifest.dataset.as_posix()}` with project-owned evaluation cases; "
        "its synthetic scope is fixture data, never an authorization source.\n\n"
        "The evaluation smoke gate and package capabilities depend on the accelerator "
        "version used. Inspect the checked-in implementation before relying on them.\n",
        encoding="utf-8",
    )
    relax_line_length(destination / "pyproject.toml", line_growth)
    for command in (
        ["uv", "sync", "--all-packages", "--frozen"],
        # Renamed imports sort and wrap differently; let isort settle them once.
        ["uv", "run", "--all-packages", "ruff", "check", "--fix", "--select", "I", "--quiet"],
        ["npm.cmd" if os.name == "nt" else "npm", "ci"],
        ["make", "check"],
    ):
        subprocess.run(command, cwd=destination, check=True)
    LOGGER.info("Project generated and checked: %s", destination)


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "my-solution"
        self.manifest = json.loads((ROOT / "accelerator.manifest.yml").read_text(encoding="utf-8"))
        self.manifest["copy"] = [
            "pyproject.toml",
            "Makefile",
            "apps",
            "packages",
            "engagement",
            "evaluations",
            "docs",
            "scripts",
            "tests",
            "package.json",
            "package-lock.json",
            "uv.lock",
        ]
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        self.write(
            "pyproject.toml", '[project]\nname = "fde-ai-solution-accelerator"\nversion = "0.1.0"\n'
        )
        self.write("Makefile", (ROOT / "Makefile").read_text(encoding="utf-8"))
        self.write("apps/api/src/accelerator/api.py", "from accelerator import api\n")
        self.write("apps/api/pyproject.toml", '[project]\nname = "fde-accelerator-api"\n')
        self.write("apps/web/package.json", '{"name": "fde-accelerator-web"}\n')
        self.write("apps/web/app/page.tsx", 'const title = "FDE AI Solution Accelerator";\n')
        self.write(
            "apps/api/src/accelerator/display.py",
            'DISPLAY = "FDE AI Solution Accelerator"\n',
        )
        self.write(
            "docs/spec.md",
            "# Generated project\n\n"
            "├── accelerator.manifest.yml # what the generator copies\n"
            "├── scripts/new_project.py # project generator\n\n"
            "## 10. Project generator\n\n"
            'Run `make new-project NAME=solution TITLE="Solution"`.\n\n'
            "scripts/new_project.py reads accelerator.manifest.yml.\n\n"
            "## 11. Delivery milestones\n\n"
            "| 36 | accelerator.manifest.yml + new_project.py | generated project |\n\n"
            "## 13. Developer commands\n\n"
            'make new-project NAME=... TITLE="..."\n',
        )
        self.write(
            "packages/agent_core/pyproject.toml",
            'name = "fde-agent-core"\npackages = ["accelerator.agent_core"]\n',
        )
        self.write("package.json", '{"name": "fde-ai-solution-accelerator"}\n')
        self.write(
            "package-lock.json",
            '{"name": "fde-ai-solution-accelerator", '
            '"packages": {"node_modules/fde-accelerator-web": {}}}\n',
        )
        self.write("uv.lock", 'name = "fde-agent-core"\n')
        self.write("engagement/templates/plan.md", "# Plan\n\n<!-- Complete this section -->\n")
        self.write("engagement/examples/fictional-engagement/plan.md", "fictional answer")
        self.write("evaluations/example-datasets/example.jsonl", '{"example": true}')
        self.write("evaluations/rubrics/.gitkeep", "")
        self.write("scripts/new_project.py", "generator")
        self.write("scripts/.gitkeep", "")
        self.write(
            "tests/test_scaffold.py",
            'directories = ("evaluations/example-datasets", '
            '"engagement/examples/fictional-engagement")\n',
        )
        self.write("tests/test_runtime.py", "import accelerator.api\n")
        self.write("apps/.env", "must not copy")
        self.write("apps/.env.production", "must not copy")
        self.write("apps/.env.example", "safe placeholder")
        self.write("apps/node_modules/ignored.txt", "must not copy")
        (self.source / "apps" / "asset.bin").write_bytes(b"\x00\xff\x80")

    def write(self, relative: str, text: str) -> None:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_generator(self) -> None:
        generate(self.source, self.destination, "my-solution", "My Solution")

    def test_generation_renames_removes_seeds_and_checks_without_changing_source(self) -> None:
        before = {
            path.relative_to(self.source): path.read_bytes()
            for path in self.source.rglob("*")
            if path.is_file()
        }
        with patch("subprocess.run") as run:
            self.run_generator()
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["uv", "sync", "--all-packages", "--frozen"],
                [
                    "uv", "run", "--all-packages", "ruff", "check", "--fix", "--select", "I",
                    "--quiet",
                ],
                ["npm.cmd" if os.name == "nt" else "npm", "ci"],
                ["make", "check"],
            ],
        )
        for call in run.call_args_list:
            self.assertEqual(call.kwargs, {"cwd": self.destination, "check": True})
        self.assertEqual(
            (self.destination / "apps/api/src/my_solution/api.py").read_text(),
            "from my_solution import api\n",
        )
        self.assertIn(
            "my-solution-agent-core",
            (self.destination / "packages/agent_core/pyproject.toml").read_text(),
        )
        self.assertIn(
            "my_solution.agent_core",
            (self.destination / "packages/agent_core/pyproject.toml").read_text(),
        )
        self.assertIn("my-solution-agent-core", (self.destination / "uv.lock").read_text())
        self.assertIn(
            "node_modules/my-solution-web", (self.destination / "package-lock.json").read_text()
        )
        self.assertIn("My Solution", (self.destination / "apps/web/app/page.tsx").read_text())
        generated_spec_text = (self.destination / "docs/spec.md").read_text()
        for omitted in ("accelerator.manifest.yml", "new_project.py", "make new-project"):
            self.assertNotIn(omitted, generated_spec_text)
        self.assertIn("## 10. Project setup", generated_spec_text)
        self.assertEqual(
            (self.destination / "tests/test_scaffold.py").read_text(),
            'directories = ("evaluations/datasets", "engagement/project")\n',
        )
        for removed in self.manifest["remove"] + [
            "accelerator.manifest.yml",
            "apps/.env",
            "apps/.env.production",
            "apps/node_modules",
        ]:
            self.assertFalse((self.destination / removed).exists(), removed)
        self.assertEqual((self.destination / "apps/.env.example").read_text(), "safe placeholder")
        self.assertEqual((self.destination / "apps/asset.bin").read_bytes(), b"\x00\xff\x80")
        self.assertNotIn("new-project", (self.destination / "Makefile").read_text())
        self.assertNotIn("check-generator", (self.destination / "Makefile").read_text())
        self.assertEqual((self.destination / "ACCELERATOR_VERSION").read_text(), "0.1.0\n")
        self.assertEqual(
            (self.destination / "engagement/project/plan.md").read_text(),
            (self.source / "engagement/templates/plan.md").read_text(),
        )
        row = json.loads((self.destination / "evaluations/datasets/starter.jsonl").read_text())
        self.assertEqual(row, self.manifest["starter_row"])
        # Kept example datasets move with their path rename, like the text that names them.
        self.assertEqual(
            (self.destination / "evaluations/datasets/example.jsonl").read_text(),
            '{"example": true}',
        )
        self.assertFalse((self.destination / "evaluations/example-datasets").exists())
        self.assertEqual(
            before,
            {
                path.relative_to(self.source): path.read_bytes()
                for path in self.source.rglob("*")
                if path.is_file()
            },
        )

    def test_rejects_existing_destination_and_overlapping_source(self) -> None:
        self.destination.mkdir()
        sentinel = self.destination / "keep"
        sentinel.write_text("unchanged")
        with self.assertRaises(FileExistsError):
            self.run_generator()
        self.assertEqual(sentinel.read_text(), "unchanged")
        for path in (self.source / "nested", self.source, self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                generate(self.source, path, "my-solution", "My Solution")

    def test_rejects_unsafe_names_and_manifest_paths_before_creating_output(self) -> None:
        for name in ("../escape", "UPPER", "a/b", "a\\b", "class", "con", "com1", "json", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                generate(self.source, self.destination, name, "Display")
        for path in ("../escape", "/absolute", "C:/escape", "a/../b", "a\\b", "a:stream", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path)
        self.manifest["project_docs"] = "../escape"
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            self.run_generator()
        self.assertFalse(self.destination.exists())

    def test_rejects_destination_traversal_before_creating_output(self) -> None:
        with self.assertRaises(ValueError):
            generate(self.source, self.root / "unused" / ".." / "escape", "my-solution", "Display")
        self.assertFalse((self.root / "escape").exists())

    def test_display_quotes_and_backslashes_preserve_string_literals(self) -> None:
        self.write("apps/web/title.json", '{"title": "FDE AI Solution Accelerator"}')
        display = "My \"Quoted\" and 'Single' Solution \\ Team"
        with patch("subprocess.run"):
            generate(self.source, self.destination, "my-solution", display)
        self.assertEqual(
            json.loads((self.destination / "apps/web/title.json").read_text())["title"],
            display,
        )
        self.assertIn(
            json.dumps(display)[1:-1], (self.destination / "apps/web/app/page.tsx").read_text()
        )
        python_source = (self.destination / "apps/api/src/my_solution/display.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(ast.literal_eval(python_source.split("=", 1)[1].strip()), display)

    def test_line_limit_grows_only_by_what_the_rename_added(self) -> None:
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
        relax_line_length(pyproject, 0)
        self.assertNotIn("pycodestyle", pyproject.read_text())

        relax_line_length(pyproject, 7)
        settings = tomllib.loads(pyproject.read_text())
        self.assertEqual(settings["tool"]["ruff"]["line-length"], 100)
        self.assertEqual(settings["tool"]["ruff"]["lint"]["pycodestyle"]["max-line-length"], 107)

        # Same-length names leave the generated configuration untouched.
        with patch("subprocess.run"):
            self.run_generator()
        self.assertNotIn("pycodestyle", (self.destination / "pyproject.toml").read_text())

    def test_generated_projects_keep_only_listed_workflows_without_accelerator_blocks(
        self,
    ) -> None:
        self.manifest["copy"] = [*self.manifest["copy"], ".github"]
        self.manifest["workflows"] = ["pull-request.yml"]
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        self.write(
            ".github/workflows/pull-request.yml",
            "jobs:\n  quality: {}\n  # BEGIN ACCELERATOR ONLY\n  generator: {}\n"
            "  # END ACCELERATOR ONLY\n",
        )
        self.write(".github/workflows/deploy-dev.yml", "jobs: {}\n")
        # Extensionless text files are processed too, and paths in them are renamed.
        self.write(
            ".github/CODEOWNERS",
            "* @owner\n/apps/api/src/accelerator/ @owner\n"
            "# BEGIN ACCELERATOR ONLY\n/infrastructure/ @owner\n# END ACCELERATOR ONLY\n",
        )
        # Markdown uses the HTML-comment markers, indented or not.
        self.write(
            ".github/GUIDE.md",
            "Shared.\n<!-- BEGIN ACCELERATOR ONLY -->\nAccelerator status.\n"
            "<!-- END ACCELERATOR ONLY -->\n1. Item\n   <!-- BEGIN ACCELERATOR ONLY -->\n"
            "   Accelerator note.\n   <!-- END ACCELERATOR ONLY -->\n   Shared note.\n",
        )
        # Valid UTF-8 with a NUL byte is binary and copied byte for byte.
        (self.source / ".github" / "data.bin").write_bytes(b"accelerator\x00")
        with patch("subprocess.run"):
            self.run_generator()
        workflows = self.destination / ".github/workflows"
        self.assertEqual(sorted(path.name for path in workflows.iterdir()), ["pull-request.yml"])
        self.assertEqual((workflows / "pull-request.yml").read_text(), "jobs:\n  quality: {}\n")
        self.assertEqual(
            (self.destination / ".github/CODEOWNERS").read_text(),
            "* @owner\n/apps/api/src/my_solution/ @owner\n",
        )
        self.assertEqual(
            (self.destination / ".github/GUIDE.md").read_text(),
            "Shared.\n1. Item\n   Shared note.\n",
        )
        self.assertEqual((self.destination / ".github/data.bin").read_bytes(), b"accelerator\x00")

    def test_rejects_missing_workflows_and_unbalanced_accelerator_blocks(self) -> None:
        self.manifest["copy"] = [*self.manifest["copy"], ".github"]
        self.manifest["workflows"] = ["missing.yml"]
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        self.write(".github/workflows/pull-request.yml", "jobs: {}\n")
        with self.assertRaisesRegex(ValueError, "workflow does not exist"):
            self.run_generator()
        for text, error in (
            ("# BEGIN ACCELERATOR ONLY\nx\n", "Unclosed"),
            ("x\n# END ACCELERATOR ONLY\n", "Unmatched"),
            ("# BEGIN ACCELERATOR ONLY\n# BEGIN ACCELERATOR ONLY\n", "Nested"),
            ("<!-- BEGIN ACCELERATOR ONLY -->\nx\n", "Unclosed"),
            ("x\n<!-- END ACCELERATOR ONLY -->\n", "Unmatched"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                strip_accelerator_only(text, Path("file"))

    def test_title_is_required_and_the_x11_display_variable_is_ignored(self) -> None:
        environment = {"NAME": "my-solution", "DISPLAY": ":0"}
        with patch.dict(os.environ, environment, clear=False), patch(
            "sys.argv", ["new_project.py"]
        ), patch.dict(os.environ, {"TITLE": ""}), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(raised.exception.code, 2)
        with patch.dict(os.environ, {**environment, "TITLE": "My Solution"}), patch(
            "sys.argv", ["new_project.py", "--destination", str(self.destination)]
        ), patch(__name__ + ".generate") as generate_mock:
            self.assertEqual(main(), 0)
        self.assertEqual(generate_mock.call_args.args[2:], ("my-solution", "My Solution"))

    def test_controls_without_generated_implementation_are_downgraded(self) -> None:
        controls = (
            "controls:\n"
            "  - id: C-001\n    status: implemented\n    implemented_in:\n"
            "      - apps/a.py\n    verified_by: []\n\n"
            "  - id: C-002\n    status: implemented\n    implemented_in:\n"
            "      - apps/a.py\n      - infrastructure/b.bicep\n    verified_by: []\n\n"
            "  - id: C-003\n    status: partial\n    notes: >-\n      Existing gap.\n"
            "    implemented_in:\n      - infrastructure/c.bicep\n    verified_by: []\n"
        )
        output = generated_controls(controls, lambda path: not path.startswith("infra"))
        blocks = output.split("  - id: ")
        self.assertEqual(blocks[1], controls.split("  - id: ")[1])
        self.assertIn(
            "    status: partial\n    notes: >-\n      Generated project: 1 of 2", blocks[2]
        )
        self.assertIn("(including infrastructure/b.bicep)", blocks[2])
        self.assertIn("    status: planned\n    notes: >-\n      Existing gap.\n", blocks[3])
        self.assertIn("      Generated project: 1 of 1", blocks[3])
        with self.assertRaisesRegex(ValueError, "Unsupported notes style"):
            generated_controls(
                controls.replace("notes: >-\n      Existing gap.", "notes: Existing gap."),
                lambda path: False,
            )
        # A generation run applies it to the copied threat model.
        self.manifest["copy"] = [*self.manifest["copy"], "threat-model"]
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        self.write(
            "threat-model/controls.yml",
            controls.replace("apps/a.py", "apps/api/src/accelerator/api.py"),
        )
        with patch("subprocess.run"):
            self.run_generator()
        generated = (self.destination / "threat-model/controls.yml").read_text()
        self.assertIn("apps/api/src/my_solution/api.py", generated)
        self.assertEqual(generated.count("Generated project:"), 2)

    def test_real_manifest_copies_the_files_inherited_checks_read(self) -> None:
        # .github/tests/test_security_configuration.py reads the root SECURITY.md.
        copied = set(Manifest.load(ROOT).copy)
        self.assertIn(Path(".github"), copied)
        self.assertIn(Path("SECURITY.md"), copied)

    def test_rejects_invalid_starter_schema(self) -> None:
        self.manifest["starter_row"]["expected_abstain"] = False
        self.write("accelerator.manifest.yml", json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            self.run_generator()
        self.assertFalse(self.destination.exists())

    def test_rejects_missing_entries_and_copy_collisions(self) -> None:
        for entry in ("missing", "apps/api"):
            with self.subTest(entry=entry):
                manifest = dict(self.manifest)
                manifest["copy"] = [*self.manifest["copy"], entry]
                self.write("accelerator.manifest.yml", json.dumps(manifest))
                with self.assertRaises(ValueError):
                    self.run_generator()
                self.assertFalse(self.destination.exists())

    def test_rejects_package_directory_rename_collisions(self) -> None:
        self.write("apps/api/src/my_solution/keep.py", "must not overwrite")
        with self.assertRaises(ValueError):
            self.run_generator()
        self.assertFalse(self.destination.exists())

    def test_rejects_links_without_touching_their_targets(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("unchanged")
        link = self.source / "apps" / "link"
        # Mock link detection so the safety test also runs without Windows symlink privileges.
        link.mkdir()
        original = is_link
        with patch(__name__ + ".is_link", side_effect=lambda path: path == link or original(path)):
            with self.assertRaises(ValueError):
                self.run_generator()
        self.assertEqual(sentinel.read_text(), "unchanged")
        self.assertFalse(self.destination.exists())
        with patch(__name__ + ".is_link", side_effect=lambda path: path == self.root):
            with self.assertRaises(ValueError):
                self.run_generator()

    def test_check_failure_is_propagated_and_output_is_preserved_for_diagnosis(self) -> None:
        error = subprocess.CalledProcessError(9, ["make", "check"])
        with patch("subprocess.run", side_effect=[None, None, None, error]):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_generator()
        self.assertTrue((self.destination / "ACCELERATOR_VERSION").exists())

    def test_install_failure_stops_before_make_check(self) -> None:
        error = subprocess.CalledProcessError(1, ["uv", "sync"])
        with patch("subprocess.run", side_effect=error) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_generator()
        self.assertEqual(run.call_count, 1)

    def test_replacements_are_simultaneous_and_do_not_change_substrings(self) -> None:
        self.assertEqual(
            replace_text(
                "accelerator accelerator_extra fde-accelerator-api",
                {"accelerator": "test_accelerator", "fde-accelerator-api": "test-api"},
            ),
            "test_accelerator accelerator_extra test-api",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=os.environ.get("NAME"))
    # Never read DISPLAY: it is the X11 display (for example ":0") in desktop shells.
    parser.add_argument(
        "--title", "--display", dest="title", default=os.environ.get("TITLE") or None
    )
    parser.add_argument("--destination", type=Path, default=os.environ.get("DEST") or None)
    parser.add_argument("--self-test", action="store_true", help="Run temporary-tree fixture tests")
    parser.add_argument(
        "--check-generated",
        action="store_true",
        help="Generate from the checked-in layout and run its make check in a temporary directory",
    )
    arguments = parser.parse_args()
    if arguments.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(GeneratorTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if not arguments.check_generated and (not arguments.name or not arguments.title):
        parser.error(
            'Supply both NAME and TITLE: make new-project NAME=my-solution TITLE="My Solution"'
        )
    try:
        if arguments.check_generated:
            with tempfile.TemporaryDirectory(prefix="accelerator-generator-") as temporary:
                subprocess.run(
                    [
                        "make",
                        "new-project",
                        "NAME=generated-solution",
                        "TITLE=Generated Solution",
                        f"DEST={Path(temporary) / 'generated-solution'}",
                    ],
                    cwd=ROOT,
                    check=True,
                )
        else:
            destination = arguments.destination or ROOT.parent / arguments.name
            generate(ROOT, destination, arguments.name, arguments.title)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        LOGGER.error("Generation failed: %s; any output is retained for diagnosis", error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
