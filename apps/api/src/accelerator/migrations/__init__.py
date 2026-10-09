"""Alembic migrations for the API's PostgreSQL schema.

Run with ``python -m accelerator.migrations upgrade head`` (containers) or
``make migrate`` (local). Migrations run with the deployment identity; the runtime
identity only needs the grants documented in ``apps/api/README.md``.
"""

from importlib.resources import files

from alembic.config import Config


def alembic_config(config: Config | None = None) -> Config:
    """Point an Alembic config at the packaged migration scripts."""
    config = config if config is not None else Config()
    config.set_main_option("script_location", str(files("accelerator.migrations")))
    return config
