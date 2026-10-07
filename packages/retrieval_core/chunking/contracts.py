from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    size: int = 1000
    overlap: int = 100
    heading_aware: bool = False

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("size must be greater than zero")
        if self.overlap < 0:
            raise ValueError("overlap cannot be negative")
        if self.overlap >= self.size:
            raise ValueError("overlap must be smaller than size")


@dataclass(frozen=True, slots=True)
class ChunkContent:
    text: str
    section_heading: str | None = None
