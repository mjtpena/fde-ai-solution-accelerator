import re

from .contracts import ParsedDocument, ParsedSection

_HEADING_PATTERN = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*#*\s*$")


class MarkdownParser:
    def parse(self, content: bytes) -> ParsedDocument:
        markdown = content.decode("utf-8-sig")
        sections: list[ParsedSection] = []
        current_heading: str | None = None
        current_lines: list[str] = []

        def append_section() -> None:
            text = "\n".join(current_lines).strip()
            if current_heading is not None or text:
                sections.append(ParsedSection(heading=current_heading, text=text))

        for line in markdown.splitlines():
            match = _HEADING_PATTERN.match(line)
            if match is None:
                current_lines.append(line)
                continue

            append_section()
            current_heading = match.group(1).strip()
            current_lines.clear()

        append_section()
        return ParsedDocument(text=markdown, sections=tuple(sections))
