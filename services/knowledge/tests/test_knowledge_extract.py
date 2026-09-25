"""Validation and extraction for every supported format, plus malformed-file rejection."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from crewquarters_knowledge import extract as ex
from crewquarters_knowledge.extract import ExtractionError, Segment

pytestmark = pytest.mark.no_db

DOCX = ex.SUPPORTED[".docx"]


def _file(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _code(fn: object, *args: object) -> str:
    with pytest.raises(ExtractionError) as info:
        fn(*args)  # type: ignore[operator]
    return info.value.code


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\x\\notes.md", "notes.md"),
        ("re\x00port\x07.csv", "report.csv"),
        ("  spaced.txt ", "spaced.txt"),
    ],
)
def test_safe_filename(raw: str, safe: str) -> None:
    assert ex.safe_filename(raw) == safe


@pytest.mark.parametrize("raw", ["", "..", "x" * 300 + ".txt"])
def test_unsafe_filenames_rejected(raw: str) -> None:
    assert _code(ex.safe_filename, raw) == "INVALID_FILENAME"


def test_mime_is_confirmed_by_content(
    make_pdf: Callable[[list[str]], bytes], make_docx: Callable[..., bytes]
) -> None:
    assert ex.detect_mime("a.PDF", make_pdf(["hi"])[:16]) == "application/pdf"
    assert ex.detect_mime("a.docx", make_docx("h", ["p"])[:16]) == DOCX
    assert _code(ex.detect_mime, "a.exe", b"MZ") == "UNSUPPORTED_TYPE"
    assert _code(ex.detect_mime, "a.pdf", b"plain text") == "CONTENT_MISMATCH"
    assert _code(ex.detect_mime, "a.docx", b"%PDF-1.4") == "CONTENT_MISMATCH"
    assert _code(ex.detect_mime, "a.txt", b"bin\x00ary") == "CONTENT_MISMATCH"


def test_plain_text_paragraph_lines(tmp_path: Path) -> None:
    path = _file(tmp_path, "a.txt", b"\xef\xbb\xbfFirst line\nstill first\n\n\nSecond")
    assert ex.extract(path, "text/plain") == [
        Segment("First line\nstill first", {"line": 1}),
        Segment("Second", {"line": 5}),
    ]


def test_invalid_utf8(tmp_path: Path) -> None:
    path = _file(tmp_path, "a.txt", b"caf\xe9")
    assert _code(ex.extract, path, "text/plain") == "UNSUPPORTED_ENCODING"


def test_markdown_sections(tmp_path: Path) -> None:
    md = b"Intro text\n\n# Refunds\nRefunds take 5 days.\n\n## Cancellation\nCancel anytime."
    segments = ex.extract(_file(tmp_path, "a.md", md), "text/markdown")
    assert segments == [
        Segment("Intro text", {"line": 1}),
        Segment("# Refunds", {"section": "Refunds", "line": 3}),
        Segment("Refunds take 5 days.", {"section": "Refunds", "line": 3}),
        Segment("## Cancellation", {"section": "Cancellation", "line": 6}),
        Segment("Cancel anytime.", {"section": "Cancellation", "line": 6}),
    ]


def test_csv_rows_keep_headers(tmp_path: Path) -> None:
    content = b"name,plan,notes\nAsha,Pro,renews in May\nRavi,,\nLee,Free,\n"
    segments = ex.extract(_file(tmp_path, "a.csv", content), "text/csv")
    assert segments == [
        Segment("name: Asha; plan: Pro; notes: renews in May", {"row": 2}),
        Segment("name: Ravi", {"row": 3}),
        Segment("name: Lee; plan: Free", {"row": 4}),
    ]


def test_malformed_csv(tmp_path: Path) -> None:
    content = b'a\n"' + b"x" * 200_000 + b'"\n'
    assert _code(ex.extract, _file(tmp_path, "a.csv", content), "text/csv") == "EXTRACTION_FAILED"


def test_pdf_pages(tmp_path: Path, make_pdf: Callable[[list[str]], bytes]) -> None:
    path = _file(tmp_path, "a.pdf", make_pdf(["Page one says hello.", "Page two (terms)."]))
    assert ex.extract(path, "application/pdf") == [
        Segment("Page one says hello.", {"page": 1}),
        Segment("Page two (terms).", {"page": 2}),
    ]


def test_scanned_pdf_is_rejected_clearly(
    tmp_path: Path, make_pdf: Callable[[list[str]], bytes]
) -> None:
    path = _file(tmp_path, "scan.pdf", make_pdf(["", ""]))
    assert _code(ex.extract, path, "application/pdf") == "SCANNED_PDF_UNSUPPORTED"


def test_corrupt_and_encrypted_pdf(tmp_path: Path, make_pdf: Callable[[list[str]], bytes]) -> None:
    from pypdf import PdfReader, PdfWriter

    corrupt = _file(tmp_path, "bad.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50)
    assert _code(ex.extract, corrupt, "application/pdf") in {
        "EXTRACTION_FAILED",
        "SCANNED_PDF_UNSUPPORTED",
    }
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["secret text here"]))))
    writer.encrypt("owner-password")
    out = io.BytesIO()
    writer.write(out)
    locked = _file(tmp_path, "locked.pdf", out.getvalue())
    assert _code(ex.extract, locked, "application/pdf") == "ENCRYPTED_PDF"


def test_docx_sections_and_tables(tmp_path: Path, make_docx: Callable[..., bytes]) -> None:
    content = make_docx(
        "Policy", ["Refunds within 30 days.", ""], [["Plan", "Price"], ["Pro", "9"]]
    )
    segments = ex.extract(_file(tmp_path, "a.docx", content), DOCX)
    assert Segment("Policy", {"paragraph": 1, "section": "Policy"}) in segments
    assert Segment("Refunds within 30 days.", {"paragraph": 2, "section": "Policy"}) in segments
    assert Segment("Pro | 9", {"table": 1, "row": 2}) in segments


def test_docx_expansion_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_docx: Callable[..., bytes]
) -> None:
    monkeypatch.setattr(ex, "MAX_DOCX_UNCOMPRESSED", 1000)
    path = _file(tmp_path, "a.docx", make_docx("Big", ["x" * 5000]))
    assert _code(ex.extract, path, DOCX) == "TOO_LARGE"


def test_docx_that_is_not_word(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a word document")
    path = _file(tmp_path, "a.docx", buffer.getvalue())
    assert _code(ex.extract, path, DOCX) == "CONTENT_MISMATCH"
    broken = _file(tmp_path, "b.docx", b"PK\x03\x04truncated")
    assert _code(ex.extract, broken, DOCX) == "EXTRACTION_FAILED"


def test_normalize() -> None:
    assert ex.normalize("  \ufb01le\x07  name \r\n\r\n\r\n\r\n next ") == "file name\n\nnext"
