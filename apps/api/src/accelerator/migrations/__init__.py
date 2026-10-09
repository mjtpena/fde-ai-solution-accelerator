"""Alembic migrations for the API's PostgreSQL schema.

Run with ``python -m accelerator.migrations upgrade head`` (containers) or
``make migrate`` (local). Migrations run with the deployment identity; the runtime
identity only needs the grants documented in ``apps/api/README.md``.
"""
