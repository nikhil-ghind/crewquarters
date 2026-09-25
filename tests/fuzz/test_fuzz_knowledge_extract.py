"""Knowledge ingestion on hostile uploads: file names, content sniffing, and extraction.

The service extracts in a bounded child process (``crewquarters_knowledge.isolation``), so
the end-to-end property is on :func:`prepare`: any bytes, under any supported type, end as
chunks or as an ``ExtractionError`` with a user-visible code, never as an unexpected crash
(which the worker would retry forever) and never past the time limit. The in-process
properties are stricter for the pure-Python text formats: they may only raise
``ExtractionError``.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crewquarters_knowledge.extract import (
    SUPPORTED,
    ExtractionError,
    detect_mime,
    extract,
    safe_filename,
)
from crewquarters_knowledge.isolation import Limits, prepare

pytestmark = pytest.mark.no_db

DOCX = SUPPORTED[".docx"]
TEXT_TYPES = ["text/plain", "text/markdown", "text/csv"]
CODES = {
    "UNSUPPORTED_ENCODING",
    "EXTRACTION_FAILED",
    "TOO_LARGE",
    "CONTENT_MISMATCH",
    "ENCRYPTED_PDF",
    "SCANNED_PDF_UNSUPPORTED",
    "DOCUMENT_TOO_COMPLEX",
    "EXTRACTION_TIMEOUT",
}


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a text layer (enough for pypdf's extract_text)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def minimal_docx(document_xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="application/vnd.openxml'
            'formats-package.relationships+xml"/><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.'
            'document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
XML_PIECES = st.sampled_from(
    [
        "<w:p>",
        "</w:p>",
        "<w:r><w:t>hello</w:t></w:r>",
        "<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>",
        '<w:tc><w:tcPr><w:gridSpan w:val="-3"/></w:tcPr></w:tc>',
        '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>',
        "<w:tr>",
        "&amp;",
        "&bogus;",
        "<![CDATA[x]]>",
        "<!DOCTYPE x [<!ENTITY a 'aaaa'>]>",
        "\x00",
        "<w:t>",
    ]
)
DOCX_XML = st.lists(st.one_of(XML_PIECES, st.text(max_size=10)), max_size=15).map(
    lambda parts: f"<w:document {W}><w:body>" + "".join(parts) + "</w:body></w:document>"
)


@st.composite
def mutated(draw: st.DrawFn, seed: bytes) -> bytes:
    data = bytearray(seed)
    for _ in range(draw(st.integers(1, 8))):
        if not data:
            break
        position = draw(st.integers(0, len(data) - 1))
        action = draw(st.sampled_from(["flip", "insert", "delete", "truncate"]))
        if action == "flip":
            data[position] = draw(st.integers(0, 255))
        elif action == "insert":
            data[position:position] = draw(st.binary(min_size=1, max_size=16))
        elif action == "delete":
            del data[position : position + draw(st.integers(1, 16))]
        else:
            del data[position:]
    return bytes(data)


PDF_SEED = minimal_pdf("Support hours are nine to five on weekdays.")
DOCX_SEED = minimal_docx(
    f"<w:document {W}><w:body><w:p><w:r><w:t>Hello world</w:t></w:r></w:p></w:body>"
    "</w:document>".encode()
)
UPLOADS = st.one_of(
    st.tuples(st.sampled_from(TEXT_TYPES), st.binary(max_size=2000)),
    st.tuples(st.sampled_from(TEXT_TYPES), st.text(max_size=2000).map(str.encode)),
    st.tuples(st.just("application/pdf"), mutated(PDF_SEED)),
    st.tuples(st.just("application/pdf"), st.binary(max_size=500).map(lambda b: b"%PDF-" + b)),
    st.tuples(st.just(DOCX), mutated(DOCX_SEED)),
    st.tuples(st.just(DOCX), DOCX_XML.map(lambda x: minimal_docx(x.encode("utf-8", "replace")))),
)


def test_seeds_extract() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pdf, docx = Path(tmp) / "a.pdf", Path(tmp) / "a.docx"
        pdf.write_bytes(PDF_SEED)
        docx.write_bytes(DOCX_SEED)
        assert "nine to five" in extract(pdf, "application/pdf")[0].text
        assert extract(docx, DOCX)[0].text == "Hello world"


@given(st.text(max_size=300))
def test_safe_filename_is_a_plain_base_name(name: str) -> None:
    try:
        safe = safe_filename(name)
    except ExtractionError as exc:
        assert exc.code == "INVALID_FILENAME"
        return
    assert "/" not in safe and "\\" not in safe and safe not in {".", ".."}
    # Tabs and line breaks are kept (they are harmless in JSON logs and the UI escapes them).
    assert not any((ord(c) < 32 and c not in "\t\n") or ord(c) == 127 for c in safe)


@given(st.text(max_size=40), st.binary(max_size=64))
def test_detect_mime_accepts_or_rejects(name: str, head: bytes) -> None:
    try:
        mime = detect_mime(name, head)
    except ExtractionError as exc:
        assert exc.code in {"UNSUPPORTED_TYPE", "CONTENT_MISMATCH"}
        return
    assert mime in SUPPORTED.values()


@given(UPLOADS)
def test_in_process_extraction_is_bounded(upload: tuple[str, bytes]) -> None:
    import tempfile

    mime, data = upload
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload"
        path.write_bytes(data)
        try:
            segments = extract(path, mime)
        except ExtractionError as exc:
            assert exc.code in CODES
            return
        except Exception:
            # Only the binary formats may fail with a parser's own exception; the
            # isolation process maps those to EXTRACTION_FAILED (checked below).
            assert mime in ("application/pdf", DOCX)
            return
    for segment in segments:
        assert segment.text and "\x00" not in segment.text


@settings(max_examples=12)
@given(upload=UPLOADS)
def test_isolated_preparation_ends_in_chunks_or_a_user_visible_error(
    upload: tuple[str, bytes], tmp_path_factory: pytest.TempPathFactory
) -> None:
    mime, data = upload
    path = tmp_path_factory.mktemp("upload") / "doc"
    path.write_bytes(data)
    limits = Limits(timeout_seconds=20, memory_bytes=1024**3)
    try:
        prepared = asyncio.run(prepare(path, mime, 800, 120, limits))
    except ExtractionError as exc:
        assert exc.code in CODES, exc.code
        return
    assert prepared.segments >= 0 and all(p.text for p in prepared.pieces)
