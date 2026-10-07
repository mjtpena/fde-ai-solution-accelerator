from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from accelerator.retrieval_core.chunking import Chunker, ChunkingConfig, TextChunker
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


def test_markdown_parser_preserves_literal_hashes_in_headings() -> None:
    document = MarkdownParser().parse(b"## C#\n\n### C##\n\n## Closing hashes ###\n")

    assert [section.heading for section in document.sections] == [
        "C#",
        "C##",
        "Closing hashes",
    ]


def test_markdown_parser_ignores_headings_inside_fenced_code() -> None:
    markdown = (
        b"````markdown\n"
        b"# inside backtick fence\n"
        b"```\n"
        b"## still inside longer fence\n"
        b"````\n"
        b"~~~markdown\n"
        b"### inside tilde fence\n"
        b"~~~\n"
        b"## Outside heading\n"
        b"Outside content.\n"
    )

    document = MarkdownParser().parse(markdown)

    assert [section.heading for section in document.sections] == [
        None,
        "Outside heading",
    ]
    assert "# inside backtick fence" in document.sections[0].text
    assert "## still inside longer fence" in document.sections[0].text
    assert "### inside tilde fence" in document.sections[0].text


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


def test_chunking_config_rejects_nonpositive_size() -> None:
    with pytest.raises(ValueError, match="size must be greater than zero"):
        ChunkingConfig(size=0, overlap=0)


def test_chunking_config_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError, match="overlap cannot be negative"):
        ChunkingConfig(size=10, overlap=-1)


def test_text_chunker_implements_chunker_protocol() -> None:
    chunker: Chunker = TextChunker(ChunkingConfig(size=10, overlap=0))

    assert chunker.chunk(TextParser().parse(b"protocol"))[0].text == "protocol"
