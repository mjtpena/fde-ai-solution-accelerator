from collections.abc import Iterable

from ..parsing.contracts import ParsedDocument, ParsedSection
from .contracts import ChunkContent, ChunkingConfig


class TextChunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(self, document: ParsedDocument) -> tuple[ChunkContent, ...]:
        if self.config.heading_aware:
            content = self._chunk_sections(document.sections)
        else:
            content = self._split(document.text, heading=None)
        return tuple(content)

    def _chunk_sections(self, sections: Iterable[ParsedSection]) -> list[ChunkContent]:
        chunks: list[ChunkContent] = []
        for section in sections:
            chunks.extend(self._split(section.text, heading=section.heading))
        return chunks

    def _split(self, text: str, *, heading: str | None) -> list[ChunkContent]:
        chunks: list[ChunkContent] = []
        step = self.config.size - self.config.overlap
        for start in range(0, len(text), step):
            piece = text[start : start + self.config.size].strip()
            if piece:
                chunks.append(ChunkContent(text=piece, section_heading=heading))
            if start + self.config.size >= len(text):
                break
        return chunks
