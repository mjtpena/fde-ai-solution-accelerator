import json
from pathlib import Path

from pydantic import ValidationError

from .models import DatasetRow


class DatasetValidationError(ValueError):
    def __init__(self, path: Path, line_number: int, reason: str) -> None:
        self.path = path
        self.line_number = line_number
        super().__init__(f"{path}:{line_number}: {reason}")


def load_dataset(path: str | Path) -> list[DatasetRow]:
    """Load UTF-8 JSONL, rejecting the entire dataset if any row is invalid."""
    dataset_path = Path(path)
    rows: list[DatasetRow] = []
    with dataset_path.open("rb") as dataset:
        for line_number, line in enumerate(dataset, start=1):
            try:
                text = line.decode("utf-8")
                value = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DatasetValidationError(
                    dataset_path, line_number, "invalid UTF-8 JSON"
                ) from error
            try:
                rows.append(DatasetRow.model_validate(value))
            except ValidationError as error:
                details = "; ".join(
                    f"{'.'.join(str(part) for part in item['loc']) or 'row'}: {item['msg']}"
                    for item in error.errors(include_input=False, include_url=False)
                )
                raise DatasetValidationError(dataset_path, line_number, details) from error
    return rows
