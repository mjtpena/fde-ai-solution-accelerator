from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ParsedSection:
    heading: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: str
    sections: tuple[ParsedSection, ...]


class DocumentParser(Protocol):
    def parse(self, content: bytes) -> ParsedDocument: ...
