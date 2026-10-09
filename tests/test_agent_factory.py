import unittest
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError

from accelerator.agent_core.agents.factory import AgentConfig, AgentFactory


class FakeRuntime:
    def __init__(self) -> None:
        self.arguments: dict[str, object] | None = None

    def create_agent(
        self,
        *,
        name: str,
        model: str,
        instructions: str,
        tools: Sequence[Any],
    ) -> object:
        self.arguments = {
            "name": name,
            "model": model,
            "instructions": instructions,
            "tools": tools,
        }
        return self.arguments


class AgentConfigTests(unittest.TestCase):
    def test_rejects_empty_required_fields_and_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfig(name=" ", model="deployment", instructions_file="instructions.md")
        with self.assertRaises(ValidationError):
            AgentConfig(
                name="assistant",
                model="deployment",
                instructions_file="instructions.md",
                tools=("",),
            )
        with self.assertRaises(ValidationError):
            AgentConfig(
                name="assistant",
                model="deployment",
                instructions_file="instructions.md",
                prompt="inline system prompt",
            )


class AgentFactoryTests(unittest.TestCase):
    def test_loads_instructions_and_resolves_tools_in_configured_order(self) -> None:
        with TemporaryDirectory() as directory:
            instructions_dir = Path(directory)
            (instructions_dir / "assistant.md").write_text(
                "Use the configured tools.", encoding="utf-8"
            )
            tool_map = {"lookup": object(), "search": object()}
            runtime = FakeRuntime()
            factory = AgentFactory(runtime, tool_map.__getitem__, instructions_dir)

            result = factory.create(
                AgentConfig(
                    name="assistant",
                    model="deployment",
                    instructions_file="assistant.md",
                    tools=("search", "lookup"),
                )
            )

            self.assertIs(result, runtime.arguments)
            self.assertEqual(
                runtime.arguments,
                {
                    "name": "assistant",
                    "model": "deployment",
                    "instructions": "Use the configured tools.",
                    "tools": (tool_map["search"], tool_map["lookup"]),
                },
            )

    def test_rejects_absolute_paths_and_parent_traversal(self) -> None:
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            root = temporary_root / "instructions"
            root.mkdir()
            (temporary_root / "outside.md").write_text("Do not load me.", encoding="utf-8")
            factory = AgentFactory(FakeRuntime(), lambda _: object(), root)
            for filename in (str(temporary_root / "outside.md"), "../outside.md"):
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    factory.create(
                        AgentConfig(
                            name="assistant",
                            model="deployment",
                            instructions_file=filename,
                        )
                    )

    def test_accepts_package_files_served_through_symlinks(self) -> None:
        # Strict editable installs expose each source file as a symlink.
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "source.md"
            source.write_text("Instructions.", encoding="utf-8")
            root = temporary_root / "instructions"
            root.mkdir()
            (root / "assistant.md").symlink_to(source)
            factory = AgentFactory(FakeRuntime(), lambda _: object(), root)

            factory.create(
                AgentConfig(name="assistant", model="deployment", instructions_file="assistant.md")
            )

            with self.assertRaisesRegex(ValueError, "stay inside"):
                factory.create(
                    AgentConfig(
                        name="assistant",
                        model="deployment",
                        instructions_file="nested/../../source.md",
                    )
                )

    def test_rejects_missing_or_empty_instructions_and_missing_tools(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.md").write_text("  \n", encoding="utf-8")
            (root / "valid.md").write_text("Instructions.", encoding="utf-8")
            factory = AgentFactory(FakeRuntime(), lambda name: {"known": object()}[name], root)

            for filename, tool_names, error in (
                ("missing.md", (), FileNotFoundError),
                ("empty.md", (), ValueError),
                ("valid.md", ("unknown",), KeyError),
            ):
                with self.subTest(filename=filename, tool_names=tool_names):
                    with self.assertRaises(error):
                        factory.create(
                            AgentConfig(
                                name="assistant",
                                model="deployment",
                                instructions_file=filename,
                                tools=tool_names,
                            )
                        )

    def test_rejects_non_markdown_instructions_before_resolving_tools(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assistant.txt").write_text("Not Markdown.", encoding="utf-8")
            runtime = FakeRuntime()
            factory = AgentFactory(runtime, lambda name: {"known": object()}[name], root)

            with self.assertRaisesRegex(ValueError, "relative Markdown"):
                factory.create(
                    AgentConfig(
                        name="assistant",
                        model="deployment",
                        instructions_file="assistant.txt",
                        tools=("unknown",),
                    )
                )
            self.assertIsNone(runtime.arguments)
