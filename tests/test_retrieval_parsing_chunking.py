from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from accelerator.retrieval_core.chunking import ChunkingConfig, TextChunker
from accelerator.retrieval_core.parsing import MarkdownParser, PdfParser, TextParser

FIXTURES = Path(__file__).parent / "fixtures" / "retrieval"


@pytest.fixture
def pdf_fixture() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/F1"): writer._add_object(
                DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                )
            )
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): font})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 72 Td (PDF fixture text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_text_parser_reads_utf8_fixture() -> None:
    document = TextParser().parse((FIXTURES / "example.txt").read_bytes())

    assert document.text.strip() == "UTF-8 text fixture for parser and chunker tests."
    assert document.sections[0].heading is None


def test_markdown_parser_preserves_heading_sections() -> None:
    document = MarkdownParser().parse((FIXTURES / "example.md").read_bytes())

    assert [section.heading for section in document.sections] == [
        None,
        "First section",
        "Second section",
    ]
    assert "## First section" in document.text
    assert document.sections[1].text == "First section content."


def test_pdf_parser_extracts_fixture_page_text(pdf_fixture: bytes) -> None:
    document = PdfParser().parse(pdf_fixture)

    assert document.text == "PDF fixture text"


def test_chunker_applies_size_and_overlap() -> None:
    document = TextParser().parse(b"abcdefghij")
    chunks = TextChunker(ChunkingConfig(size=6, overlap=2)).chunk(document)

    assert [chunk.text for chunk in chunks] == ["abcdef", "efghij"]


def test_heading_aware_chunking_keeps_sections_separate() -> None:
    document = MarkdownParser().parse((FIXTURES / "example.md").read_bytes())
    chunks = TextChunker(ChunkingConfig(size=100, overlap=0, heading_aware=True)).chunk(
        document
    )

    assert [(chunk.section_heading, chunk.text) for chunk in chunks] == [
        (None, "An introduction before headings."),
        ("First section", "First section content."),
        ("Second section", "Second section content."),
    ]


def test_chunking_config_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap must be smaller than size"):
        ChunkingConfig(size=10, overlap=10)
