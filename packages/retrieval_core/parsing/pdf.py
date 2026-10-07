from io import BytesIO

from pypdf import PdfReader

from .contracts import ParsedDocument, ParsedSection


class PdfParser:
    def parse(self, content: bytes) -> ParsedDocument:
        reader = PdfReader(BytesIO(content))
        text = "\n\n".join(
            page_text
            for page in reader.pages
            if (page_text := page.extract_text()) is not None and page_text.strip()
        )
        if not text:
            return ParsedDocument(text="", sections=())
        return ParsedDocument(text=text, sections=(ParsedSection(heading=None, text=text),))
