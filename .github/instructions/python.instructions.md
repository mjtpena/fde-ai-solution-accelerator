---
applyTo: "**/*.py"
---
# Python rules
- Python 3.12, fully typed. `mypy --strict` and `ruff` must pass.
- Pydantic v2 models for all API, tool and event contracts. No untyped dicts crossing boundaries.
- Async all the way for I/O. No blocking calls inside async functions.
- Use `pydantic-settings` for configuration; fail fast on missing settings.
- Structured logging with `correlation_id`. No `print`. No bare `except`; never swallow errors.
- Dependency injection via FastAPI `Depends`; no module-level clients created at import time.
- Tests: `pytest` + `pytest-asyncio`. Unit tests next to the feature in `tests/`; name tests by behaviour.
- Domain layer must not import Azure SDKs, FastAPI or SQLAlchemy.
