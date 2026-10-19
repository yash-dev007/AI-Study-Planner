"""Safe extraction of text from uploaded syllabus documents.

The extractor never executes, renders, or otherwise trusts document content.
It only converts bytes to plain text and returns metadata. OCR is used only
when a PDF page contains too little text to be useful, and only when the
optional OCR dependencies and the system Tesseract binary are available.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.errors import DocumentProcessingError

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "application/octet-stream": None,
}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Byte-order marks for text encodings that legitimately contain NUL bytes.
_TEXT_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass
class ExtractionResult:
    text: str
    page_count: int = 0
    ocr_used: bool = False
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def detect_extension(filename: str, content_type: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return suffix
    mapped = CONTENT_TYPE_EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())
    if mapped:
        return mapped
    raise DocumentProcessingError(
        "Unsupported file type. Upload a PDF, DOCX, or TXT syllabus.",
        code="UNSUPPORTED_FILE_TYPE",
        details={"filename": filename, "content_type": content_type},
    )


def validate_upload(filename: str, content_type: str, data: bytes) -> str:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if not data:
        raise DocumentProcessingError("The uploaded file is empty.", code="EMPTY_FILE")
    if len(data) > max_bytes:
        raise DocumentProcessingError(
            f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
            code="FILE_TOO_LARGE",
            details={"size_bytes": len(data), "limit_bytes": max_bytes},
        )

    extension = detect_extension(filename, content_type)
    if extension == ".pdf" and not data.startswith(b"%PDF"):
        raise DocumentProcessingError(
            "The file extension is .pdf but the content is not a valid PDF.",
            code="CORRUPT_DOCUMENT",
        )
    if extension == ".docx" and not data.startswith(b"PK"):
        raise DocumentProcessingError(
            "The file extension is .docx but the content is not a valid DOCX archive.",
            code="CORRUPT_DOCUMENT",
        )
    if extension == ".txt":
        sample = data[:2048]
        # NUL bytes mean binary content, except in text encoded as UTF-16/UTF-32,
        # which starts with a byte-order mark.
        if b"\x00" in sample and not sample.startswith(_TEXT_BOMS):
            raise DocumentProcessingError(
                "The text file appears to be binary and cannot be read as a syllabus.",
                code="CORRUPT_DOCUMENT",
            )
    return extension


def sanitize_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    if len(cleaned) > settings.max_extracted_chars:
        cleaned = cleaned[: settings.max_extracted_chars]
    return cleaned.strip()


def _ocr_available() -> tuple[bool, str]:
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
    except Exception:
        return False, "pytesseract/Pillow are not installed."
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on host binary
        return False, f"Tesseract is not available: {exc}"
    return True, ""


def extract_text(filename: str, content_type: str, data: bytes) -> ExtractionResult:
    extension = validate_upload(filename, content_type, data)

    if extension == ".txt":
        text = _decode_text(data)
        return ExtractionResult(text=_require_text(sanitize_text(text), ".txt"))

    if extension == ".docx":
        text = _extract_docx(data)
        return ExtractionResult(text=_require_text(sanitize_text(text), ".docx"))

    return _extract_pdf(data)


def _require_text(text: str, extension: str) -> str:
    if not text.strip():
        raise DocumentProcessingError(
            f"The {extension} file contains no readable text. Check the file and try again.",
            code="NO_TEXT_EXTRACTED",
        )
    return text


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx(data: bytes) -> str:
    try:
        import docx

        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentProcessingError(
            "The DOCX file could not be read. It may be corrupted or password protected.",
            code="CORRUPT_DOCUMENT",
            details={"reason": str(exc)},
        ) from exc

    lines: list[str] = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(line for line in lines if line is not None)


def _extract_pdf(data: bytes) -> ExtractionResult:
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentProcessingError(
            "The PDF could not be opened. It may be corrupted or password protected.",
            code="CORRUPT_DOCUMENT",
            details={"reason": str(exc)},
        ) from exc

    with document:
        if document.needs_pass:
            raise DocumentProcessingError(
                "The PDF is password protected. Upload an unprotected copy.",
                code="PASSWORD_PROTECTED_DOCUMENT",
            )
        page_count = document.page_count
        if page_count == 0:
            raise DocumentProcessingError("The PDF contains no pages.", code="CORRUPT_DOCUMENT")

        pages: list[str] = []
        sparse_pages: list[int] = []
        for number in range(page_count):
            text = document.load_page(number).get_text("text")
            pages.append(text)
            if len(text.strip()) < settings.ocr_min_chars_per_page:
                sparse_pages.append(number)

        warnings: list[str] = []
        ocr_used = False
        ocr_pages: list[int] = []

        if sparse_pages and settings.ocr_enabled:
            if len(sparse_pages) == page_count:
                # Fully scanned document: OCR is required, fail clearly if absent.
                ocr_pages = list(sparse_pages)
                ocr_results = _ocr_pages_individually(data, sparse_pages)
                for number, text in zip(sparse_pages, ocr_results, strict=True):
                    pages[number] = text
                ocr_used = True
            else:
                try:
                    ocr_results = _ocr_pages_individually(data, sparse_pages)
                    for number, text in zip(sparse_pages, ocr_results, strict=True):
                        pages[number] = text
                    ocr_pages = list(sparse_pages)
                    ocr_used = True
                    warnings.append(
                        f"OCR was used for {len(sparse_pages)} page(s) with little embedded text."
                    )
                except DocumentProcessingError as exc:
                    warnings.append(
                        f"{len(sparse_pages)} page(s) had no embedded text and could not be OCR'd: "
                        f"{exc.message}"
                    )
        elif sparse_pages:
            warnings.append(
                "Some pages contain little embedded text and may be scanned. OCR is disabled."
            )

        text = "\n\n".join(pages)
        if not text.strip():
            detail = (" " + " ".join(warnings[:1])) if warnings else ""
            raise DocumentProcessingError(
                "No readable text could be extracted from the PDF. It may be an image-only "
                f"scan.{detail}".strip(),
                code="NO_TEXT_EXTRACTED",
                details={"page_count": page_count, "warnings": warnings},
            )
        return ExtractionResult(
            text=sanitize_text(text),
            page_count=page_count,
            ocr_used=ocr_used,
            ocr_pages=ocr_pages,
            warnings=warnings,
        )


def _ocr_pages_individually(data: bytes, page_numbers: list[int]) -> list[str]:  # pragma: no cover
    """Wrapper that keeps per-page OCR results aligned with page numbers."""

    available, reason = _ocr_available()
    if not available:
        raise DocumentProcessingError(
            "The PDF appears to be scanned and OCR is not available on this server. "
            "Install Tesseract and the OCR extras, or upload a text-based document.",
            code="OCR_UNAVAILABLE",
            details={"reason": reason},
        )
    import fitz
    import pytesseract
    from PIL import Image

    results: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for number in page_numbers:
            page = document.load_page(number)
            pixmap = page.get_pixmap(dpi=200)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            results.append(pytesseract.image_to_string(image))
    return results
