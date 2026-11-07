"""Plain text from documents given to the Story Analyzer as extra context.

Supported: Word (.docx), PDF (.pdf) and text files (.txt, .md, .csv, .log,
.json, .xml, .yaml/.yml). Anything else (images, old .doc, spreadsheets, ...)
is skipped with a reason; its file name can still be passed to the LLM.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import PurePath

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # per file
MAX_TEXT_CHARS = 200_000  # per file, before the prompt budget
WORD_TYPES = {".docx"}
PDF_TYPES = {".pdf"}
TEXT_TYPES = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".xml", ".yaml", ".yml"}
SUPPORTED_TYPES = WORD_TYPES | PDF_TYPES | TEXT_TYPES
UPLOAD_ACCEPT = ".docx,.pdf,.txt,.md,.csv,.log,.json,.xml,.yaml,.yml"


@dataclass
class DocumentText:
    name: str
    text: str = ""
    skipped: str | None = None  # reason the text could not be read

    @property
    def used(self) -> bool:
        return self.skipped is None and bool(self.text.strip())


def extension(name: str) -> str:
    return PurePath(name or "").suffix.lower()


def is_supported(name: str) -> bool:
    return extension(name) in SUPPORTED_TYPES


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("the PDF is password-protected") from exc
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
        if sum(len(part) for part in parts) > MAX_TEXT_CHARS:
            break
    return "\n".join(parts)


def _docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(part for part in parts if part.strip())


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def extract_text(name: str, data: bytes) -> DocumentText:
    """Text of document ``name``; never raises (problems are reported in ``skipped``)."""
    ext = extension(name)
    if len(data) > MAX_DOCUMENT_BYTES:
        return DocumentText(name, skipped=f"larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    if ext == ".doc":
        return DocumentText(name, skipped="old Word format (.doc): save it as .docx")
    if ext not in SUPPORTED_TYPES:
        return DocumentText(name, skipped=f"{ext or 'this file type'} is not read (Word .docx, PDF and text files are)")
    try:
        if ext in PDF_TYPES:
            text = _pdf(data)
        elif ext in WORD_TYPES:
            text = _docx(data)
        else:
            text = _text(data)
    except Exception as exc:  # noqa: BLE001 - a broken file must not break the analysis
        logger.info("Could not read %s: %s", name, exc)
        return DocumentText(name, skipped=f"could not be read ({exc})")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return DocumentText(name, skipped="no text found (a scanned PDF or an empty file?)")
    return DocumentText(name, text[:MAX_TEXT_CHARS])
