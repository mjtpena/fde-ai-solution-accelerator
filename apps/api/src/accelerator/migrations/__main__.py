"""Run Alembic against the packaged migrations without an alembic.ini file."""

import logging
import sys

from alembic.config import CommandLine, Config

from accelerator.migrations import alembic_config


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    command_line = CommandLine(prog="python -m accelerator.migrations")
    options = command_line.parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not hasattr(options, "cmd"):
        command_line.parser.error("too few arguments")
    config = alembic_config(Config(cmd_opts=options))
    command_line.run_cmd(config, options)


if __name__ == "__main__":
    main()
