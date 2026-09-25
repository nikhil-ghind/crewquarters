"""Upload validation and text extraction (PLAN.md section 9.1, steps 2, 4, 5).

Supported: ``.txt``, ``.md``, text-based ``.pdf``, ``.docx``, ``.csv``. Everything is pure
Python or ships ``arm64`` wheels. Each extracted segment carries a source locator (page,
section, paragraph, row, or line) so citations can point back to it.

The service never calls :func:`extract` in its own process: see
:mod:`crewquarters_knowledge.isolation`, which bounds its time and memory.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SUPPORTED = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILENAME = 200
MAX_PDF_PAGES = 2000
MAX_CSV_ROWS = 100_000
MAX_DOCX_ENTRIES = 5000
MAX_DOCX_UNCOMPRESSED = 100 * 1024 * 1024
MAX_DOCX_DOCUMENT_XML = 4 * 1024 * 1024
MAX_DOCX_BLOCKS = 50_000  # paragraphs plus table rows
MIN_PDF_TEXT_CHARS = 20

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


class ExtractionError(Exception):
    """A permanent, user-visible problem with a document. Retrying does not help."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Segment:
    text: str
    locator: dict[str, Any] = field(default_factory=dict)


def safe_filename(name: str) -> str:
    """The base name only: no directories, control characters, or traversal."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = _CONTROL.sub("", unicodedata.normalize("NFC", base)).strip()
    if not base or base in {".", ".."} or len(base) > MAX_FILENAME:
        raise ExtractionError("INVALID_FILENAME", "The file name is not allowed.")
    return base


def detect_mime(filename: str, head: bytes) -> str:
    """The MIME type from the extension, confirmed by the file's content."""
    mime = SUPPORTED.get(PurePosixPath(filename).suffix.lower())
    if mime is None:
        raise ExtractionError("UNSUPPORTED_TYPE", "Upload a .txt, .md, .pdf, .docx, or .csv file.")
    if mime == "application/pdf":
        ok = head.startswith(b"%PDF-")
    elif mime == SUPPORTED[".docx"]:
        ok = head.startswith(b"PK\x03\x04")
    else:
        ok = b"\x00" not in head
    if not ok:
        raise ExtractionError("CONTENT_MISMATCH", "The file content does not match its type.")
    return mime


def normalize(text: str) -> str:
    """NFKC, no control characters, collapsed spaces; line breaks and headings survive."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", _CONTROL.sub("", line)).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def extract(path: Path, mime: str) -> list[Segment]:
    if mime == "application/pdf":
        segments = _pdf(path)
    elif mime == SUPPORTED[".docx"]:
        segments = _docx(path)
    elif mime == "text/csv":
        segments = _csv(_text(path))
    elif mime == "text/markdown":
        segments = _markdown(_text(path))
    else:
        segments = _plain(_text(path))
    return [s for s in (Segment(normalize(s.text), s.locator) for s in segments) if s.text]


def _text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("UNSUPPORTED_ENCODING", "Text files must be UTF-8.") from None


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """(first line number, paragraph) pairs, split on blank lines."""
    out: list[tuple[int, str]] = []
    start, lines = 1, list[str]()
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not lines:
                start = number
            lines.append(line)
        elif lines:
            out.append((start, "\n".join(lines)))
            lines = []
    if lines:
        out.append((start, "\n".join(lines)))
    return out


def _plain(text: str) -> list[Segment]:
    return [Segment(p, {"line": line}) for line, p in _paragraphs(text)]


def _markdown(text: str) -> list[Segment]:
    segments: list[Segment] = []
    section: str | None = None
    for line, paragraph in _paragraphs(text):
        body: list[str] = []
        for offset, row in enumerate(paragraph.split("\n")):
            heading = _HEADING.match(row.strip())
            if heading is None:
                body.append(row)
                continue
            if body:
                segments.append(Segment("\n".join(body), _section(section, line)))
                body = []
            section = heading.group(2).strip()
            segments.append(Segment(row.strip(), _section(section, line + offset)))
        if body:
            segments.append(Segment("\n".join(body), _section(section, line)))
    return segments


def _section(section: str | None, line: int) -> dict[str, Any]:
    return {"section": section, "line": line} if section else {"line": line}


def _csv(text: str) -> list[Segment]:
    rows = csv.reader(io.StringIO(text))
    try:
        header = next(rows, None)
        if header is None:
            return []
        segments: list[Segment] = []
        for number, row in enumerate(rows, start=2):
            if number > MAX_CSV_ROWS:
                raise ExtractionError("TOO_LARGE", f"CSV files are limited to {MAX_CSV_ROWS} rows.")
            cells = [
                f"{h.strip() or f'column {i + 1}'}: {v.strip()}"
                for i, (h, v) in enumerate(zip(header, row, strict=False))
                if v.strip()
            ]
            if cells:
                segments.append(Segment("; ".join(cells), {"row": number}))
    except csv.Error:
        raise ExtractionError("EXTRACTION_FAILED", "The CSV file is malformed.") from None
    return segments


def _pdf(path: Path) -> list[Segment]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("ENCRYPTED_PDF", "Password-protected PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ExtractionError("TOO_LARGE", f"PDFs are limited to {MAX_PDF_PAGES} pages.")
        segments = [
            Segment(page.extract_text() or "", {"page": number})
            for number, page in enumerate(reader.pages, start=1)
        ]
    except ExtractionError:
        raise
    except Exception:  # pypdf raises many types for malformed files
        raise ExtractionError("EXTRACTION_FAILED", "The PDF could not be read.") from None
    if sum(len(s.text.strip()) for s in segments) < MIN_PDF_TEXT_CHARS:
        raise ExtractionError(
            "SCANNED_PDF_UNSUPPORTED",
            "This PDF has no text layer (it may be scanned). OCR is not supported.",
        )
    return segments


def _docx(path: Path) -> list[Segment]:
    import docx

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_DOCX_ENTRIES
                or sum(e.file_size for e in entries) > MAX_DOCX_UNCOMPRESSED
            ):
                raise ExtractionError("TOO_LARGE", "The document expands to an unsafe size.")
            if "word/document.xml" not in archive.namelist():
                raise ExtractionError("CONTENT_MISMATCH", "This is not a Word document.")
            # A few kilobytes can inflate to megabytes of markup that python-docx parses
            # slowly; zipfile never inflates past the size the entry declares.
            if archive.getinfo("word/document.xml").file_size > MAX_DOCX_DOCUMENT_XML:
                raise ExtractionError(
                    "DOCUMENT_TOO_COMPLEX",
                    "The document's text is too large or complex. Split it into smaller files.",
                )
        document = docx.Document(str(path))
    except ExtractionError:
        raise
    except Exception:  # zipfile/lxml/python-docx raise many types for malformed files
        raise ExtractionError("EXTRACTION_FAILED", "The document could not be read.") from None
    paragraphs = document.paragraphs
    if len(paragraphs) + sum(len(t.rows) for t in document.tables) > MAX_DOCX_BLOCKS:
        raise ExtractionError(
            "DOCUMENT_TOO_COMPLEX",
            "The document has too many paragraphs. Split it into smaller files.",
        )
    # Resolve style ids once: python-docx's paragraph.style searches the styles each time.
    style_names = {s.style_id: s.name or "" for s in document.styles}
    segments: list[Segment] = []
    section: str | None = None
    for number, paragraph in enumerate(paragraphs, start=1):
        style = style_names.get(paragraph._p.style or "", "")
        if style.startswith(("Heading", "Title")) and paragraph.text.strip():
            section = paragraph.text.strip()
        locator: dict[str, Any] = {"paragraph": number}
        if section:
            locator["section"] = section
        segments.append(Segment(paragraph.text, locator))
    for t, table in enumerate(document.tables, start=1):
        for r, row in enumerate(table.rows, start=1):
            segments.append(
                Segment(" | ".join(c.text.strip() for c in row.cells), {"table": t, "row": r})
            )
    return segments
