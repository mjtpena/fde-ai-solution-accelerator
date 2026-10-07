from .contracts import ParsedDocument, ParsedSection


class TextParser:
    def parse(self, content: bytes) -> ParsedDocument:
        text = content.decode("utf-8-sig")
        if not text:
            return ParsedDocument(text="", sections=())
        return ParsedDocument(text=text, sections=(ParsedSection(heading=None, text=text),))
