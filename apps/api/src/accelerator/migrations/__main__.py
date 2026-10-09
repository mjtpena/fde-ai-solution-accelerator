"""Run Alembic against the packaged migrations without an alembic.ini file."""

import logging
import sys
from importlib.resources import files

from alembic.config import CommandLine, Config


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    command_line = CommandLine(prog="python -m accelerator.migrations")
    options = command_line.parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not hasattr(options, "cmd"):
        command_line.parser.error("too few arguments")
    config = Config(cmd_opts=options)
    config.set_main_option("script_location", str(files("accelerator.migrations")))
    command_line.run_cmd(config, options)


if __name__ == "__main__":
    main()
