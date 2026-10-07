import re

from .contracts import ParsedDocument, ParsedSection

_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_PATTERN = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")


class MarkdownParser:
    def parse(self, content: bytes) -> ParsedDocument:
        markdown = content.decode("utf-8-sig")
        sections: list[ParsedSection] = []
        current_heading: str | None = None
        current_lines: list[str] = []
        fence_marker: str | None = None
        fence_length = 0

        def append_section() -> None:
            text = "\n".join(current_lines).strip()
            if current_heading is not None or text:
                sections.append(ParsedSection(heading=current_heading, text=text))

        for line in markdown.splitlines():
            fence_match = _FENCE_PATTERN.match(line)
            if fence_marker is not None:
                current_lines.append(line)
                if fence_match is not None:
                    marker = fence_match.group(1)
                    if marker[0] == fence_marker and len(marker) >= fence_length:
                        remainder = line[fence_match.end() :]
                        if not remainder.strip():
                            fence_marker = None
                            fence_length = 0
                continue

            if fence_match is not None:
                marker = fence_match.group(1)
                fence_marker = marker[0]
                fence_length = len(marker)
                current_lines.append(line)
                continue

            match = _HEADING_PATTERN.match(line)
            if match is None:
                current_lines.append(line)
                continue

            append_section()
            current_heading = match.group(1).strip()
            current_lines.clear()

        append_section()
        return ParsedDocument(text=markdown, sections=tuple(sections))
