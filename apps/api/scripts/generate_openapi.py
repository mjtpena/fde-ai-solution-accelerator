import json
from pathlib import Path

from accelerator.api.app import create_app
from accelerator.configuration.settings import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    openapi_path = root / "contracts" / "api" / "openapi.json"
    openapi_path.parent.mkdir(parents=True, exist_ok=True)
    openapi_path.write_text(
        json.dumps(create_app(Settings(environment="test")).openapi(), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
