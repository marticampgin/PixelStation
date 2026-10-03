import asyncio
import csv
import hashlib
import io
import json
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .database import Attachment, DocumentChunk, get_session, record_dict
from .memory import fts_query
from .vectors import SqliteVectorStore

router = APIRouter(prefix="/api/files", tags=["files"])
MAX_BYTES = 30 * 1024 * 1024
MAX_PARSED_CHARS = 3_000_000
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".css",
    ".sql",
    ".rs",
    ".go",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".sh",
    ".ps1",
    ".xml",
    ".log",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
OFFICE = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}


def sanitize_filename(filename: str) -> str:
    basename = re.split(r"[/\\]", filename)[-1]
    clean = re.sub(r"[^\w.() -]", "_", basename).strip(" .")[:150]
    if not clean or clean.upper().split(".")[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("Choose a valid filename")
    return clean


def validate_content(content: bytes, extension: str) -> None:
    if not content or len(content) > MAX_BYTES:
        raise ValueError("File must contain data and be smaller than 30 MiB")
    if extension in TEXT_EXTENSIONS:
        content.decode("utf-8-sig")
        if b"\x00" in content:
            raise ValueError("Binary data is not a text document")
    elif extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("Content is not a PDF")
    elif extension in OFFICE:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if OFFICE[extension] not in archive.namelist():
                raise ValueError("Content does not match the Office format")
            if sum(info.file_size for info in archive.infolist()) > MAX_BYTES * 5:
                raise ValueError("Expanded document exceeds the 150 MiB limit")
    elif extension in IMAGE_EXTENSIONS:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image exceeds 40 megapixels")
            image.verify()
    else:
        raise ValueError(
            "Unsupported file format. Use TXT, Markdown, code, CSV, XLSX, DOCX, PDF, PPTX, or an image"
        )


class NativeFileParser:
    def parse(self, path: Path, extension: str) -> list[dict]:
        if extension in TEXT_EXTENSIONS:
            content = path.read_text(encoding="utf-8-sig")
            if extension == ".csv":
                csv_rows = list(csv.reader(io.StringIO(content)))
                return [
                    {
                        "text": "\n".join(" | ".join(row) for row in csv_rows[index : index + 60]),
                        "location": f"rows {index + 1}–{min(index + 60, len(csv_rows))}",
                        "heading": "CSV",
                    }
                    for index in range(0, len(csv_rows), 60)
                ]
            return [{"text": content, "location": "document", "heading": ""}]
        if extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF: provide an unlocked copy")
            return [
                {
                    "text": page.extract_text() or "",
                    "page": index + 1,
                    "location": f"page {index + 1}",
                    "heading": "",
                }
                for index, page in enumerate(reader.pages)
            ]
        if extension == ".docx":
            from docx import Document

            document = Document(str(path))
            sections = []
            for paragraph in document.paragraphs:
                if paragraph.text:
                    sections.append(
                        {
                            "text": paragraph.text,
                            "heading": paragraph.text
                            if paragraph.style and paragraph.style.name.startswith("Heading")
                            else "",
                            "location": "document",
                        }
                    )
            for number, table in enumerate(document.tables):
                sections.append(
                    {
                        "text": "\n".join(
                            " | ".join(cell.text for cell in row.cells) for row in table.rows
                        ),
                        "heading": f"Table {number + 1}",
                        "location": f"table {number + 1}",
                    }
                )
            return sections
        if extension == ".xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(path, read_only=True, data_only=True)
            result = []
            try:
                count = 0
                for sheet in workbook:
                    rows = []
                    for index, row in enumerate(sheet.iter_rows(values_only=True)):
                        count += len(row)
                        if count > 500_000:
                            raise ValueError("Workbook exceeds 500,000 cells")
                        rows.append(
                            " | ".join(str(value) if value is not None else "" for value in row)
                        )
                        if len(rows) == 50:
                            result.append(
                                {
                                    "text": "\n".join(rows),
                                    "heading": sheet.title,
                                    "location": f"{sheet.title}, rows {index - 48}–{index + 1}",
                                }
                            )
                            rows = []
                    if rows:
                        result.append(
                            {
                                "text": "\n".join(rows),
                                "heading": sheet.title,
                                "location": sheet.title,
                            }
                        )
                return result
            finally:
                workbook.close()
        if extension == ".pptx":
            from pptx import Presentation

            presentation = Presentation(str(path))
            return [
                {
                    "text": "\n".join(shape.text for shape in slide.shapes if shape.has_text_frame),
                    "page": index + 1,
                    "location": f"slide {index + 1}",
                    "heading": slide.shapes.title.text if slide.shapes.title else "",
                }
                for index, slide in enumerate(presentation.slides)
            ]
        return []


class DocumentParser:
    """Docling normalizes office/PDF when installed; a visible native fallback stays usable."""

    def parse(self, path: Path, extension: str) -> tuple[list[dict], str, str | None]:
        native = NativeFileParser()
        if extension in IMAGE_EXTENSIONS:
            return (
                [],
                "image",
                "Text OCR is not enabled. Attach to a configured vision model for visual analysis.",
            )
        if extension in OFFICE or extension == ".pdf":
            try:
                from docling.document_converter import DocumentConverter
            except ImportError:
                parts = native.parse(path, extension)
                warning = "Native parser used. Install backend[documents] for Docling normalization and local OCR."
                if extension == ".pdf" and not any(part["text"].strip() for part in parts):
                    warning = "Scanned PDF has no extracted text. Install backend[documents] and enable local OCR."
                return parts, "native", warning
            # Avoid needless OCR for text PDFs and preserve explicit page provenance.
            if extension == ".pdf":
                parts = native.parse(path, extension)
                if any(part["text"].strip() for part in parts):
                    return parts, "native-text-pdf", None
            try:
                converted = DocumentConverter().convert(path)
                return (
                    [
                        {
                            "text": converted.document.export_to_markdown(),
                            "location": "document",
                            "heading": "",
                        }
                    ],
                    "docling",
                    None,
                )
            except Exception as exc:
                parts = native.parse(path, extension)
                return parts, "native", f"Docling failed; native fallback used: {str(exc)[:200]}"
        return native.parse(path, extension), "native", None


def chunk_sections(sections: list[dict], target_chars: int = 2400) -> list[dict]:
    chunks: list[dict] = []
    consumed = 0
    for section in sections:
        text_content = section["text"]
        consumed += len(text_content)
        if consumed > MAX_PARSED_CHARS:
            raise ValueError("Extracted document exceeds 3 million characters")
        paragraphs = re.split(r"\n\s*\n", text_content)
        buffer = ""
        heading = section.get("heading", "")
        for paragraph in paragraphs:
            if paragraph.startswith("#"):
                heading = paragraph.splitlines()[0].lstrip("# ")[:200]
            # Split an exceptional giant paragraph at line/sentence/word boundaries.
            segments = [paragraph]
            if len(paragraph) > target_chars * 2:
                words = paragraph.split()
                segments, group = [], ""
                for word in words:
                    if len(group) + len(word) > target_chars and group:
                        segments.append(group)
                        group = ""
                    group += word + " "
                if group:
                    segments.append(group.rstrip())
            for segment in segments:
                if len(buffer) + len(segment) > target_chars and buffer:
                    chunks.append(
                        {
                            **section,
                            "text": buffer.strip(),
                            "heading": heading,
                            "number": len(chunks),
                        }
                    )
                    buffer = ""
                buffer += segment + "\n\n"
        if buffer.strip():
            chunks.append(
                {**section, "text": buffer.strip(), "heading": heading, "number": len(chunks)}
            )
    return chunks


def ingest(
    session: Session, data_dir: Path, filename: str, content: bytes, source: str = "uploaded"
) -> Attachment:
    filename = sanitize_filename(filename)
    extension = Path(filename).suffix.lower()
    validate_content(content, extension)
    digest = hashlib.sha256(content).hexdigest()
    matching = select(Attachment).where(
        Attachment.sha256 == digest, Attachment.extension == extension
    )
    if source == "generated":
        # Artifact names belong to records; identical immutable bytes may be shared by many records.
        matching = matching.where(Attachment.source == source, Attachment.filename == filename)
    old = session.scalar(matching.order_by(Attachment.created_at))
    if old:
        return old
    folder = data_dir / "files" / digest
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"original{extension}"
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError("Stored content-addressed file contains different data")
    else:
        path.write_bytes(content)
    row = Attachment(
        filename=filename,
        sha256=digest,
        path=str(path),
        size=len(content),
        extension=extension,
        media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        source=source,
    )
    session.add(row)
    session.flush()
    chunks = []
    try:
        sections, row.parser, row.parse_error = DocumentParser().parse(path, extension)
        chunks = chunk_sections(sections)
        row.parse_status = (
            "ready" if chunks else "visual" if extension in IMAGE_EXTENSIONS else "no_text"
        )
        for chunk in chunks:
            session.add(DocumentChunk(attachment_id=row.id, **chunk))
    except Exception as exc:
        row.parse_status, row.parse_error = "error", str(exc)[:1000]
    write_file_metadata(row, chunks)
    session.commit()
    return row


def write_file_metadata(row: Attachment, chunks: list[dict]) -> None:
    """Keep per-record names and parse metadata separate from shared immutable bytes."""
    folder = Path(row.path).parent
    record_folder = folder / "records" / row.id
    record_folder.mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": row.id,
        "filename": row.filename,
        "sha256": row.sha256,
        "size": row.size,
        "source": row.source,
        "parser": row.parser,
        "parse_status": row.parse_status,
    }
    for name, value in (("parsed.json", chunks), ("metadata.json", metadata)):
        serialized = json.dumps(value, ensure_ascii=False)
        (record_folder / name).write_text(serialized, encoding="utf-8")
        # Preserve existing legacy readers without replacing another artifact's sidecars.
        legacy = folder / name
        if not legacy.exists():
            legacy.write_text(serialized, encoding="utf-8")


def retrieve_files(
    session: Session,
    ids: list[str],
    query: str,
    limit: int = 6,
    embedding: list[float] | None = None,
) -> list[dict]:
    if not ids:
        return []
    candidates = list(
        session.scalars(select(DocumentChunk).where(DocumentChunk.attachment_id.in_(ids)))
    )
    rank = {}
    if expression := fts_query(query):
        matches = session.execute(
            text(
                "SELECT id,bm25(document_chunks_fts) AS rank FROM document_chunks_fts WHERE document_chunks_fts MATCH :q ORDER BY rank LIMIT 60"
            ),
            {"q": expression},
        )
        rank = {row.id: index for index, row in enumerate(matches)}
    semantic = (
        dict(SqliteVectorStore(session, DocumentChunk).search(embedding, 100)) if embedding else {}
    )
    candidates.sort(
        key=lambda row: (
            -(1 / (rank[row.id] + 1) if row.id in rank else 0) - max(0, semantic.get(row.id, 0)),
            row.number,
        )
    )
    files = {
        row.id: row for row in session.scalars(select(Attachment).where(Attachment.id.in_(ids)))
    }
    return [
        {
            "id": row.id,
            "file_id": row.attachment_id,
            "filename": files[row.attachment_id].filename,
            "text": row.text,
            "location": row.location,
            "page": row.page,
            "heading": row.heading,
            "number": row.number,
        }
        for row in candidates[:limit]
    ]


class LocalFileWriter:
    formats = {"txt", "md", "csv", "xlsx", "docx", "pdf"}

    def write(self, path: Path, content: str, format: str) -> None:
        if format not in self.formats:
            raise ValueError("Unsupported output format")
        if format in {"txt", "md", "csv"}:
            path.write_text(content, encoding="utf-8")
        elif format == "xlsx":
            from openpyxl import Workbook, load_workbook

            workbook = Workbook()
            for row in csv.reader(io.StringIO(content)):
                workbook.active.append(
                    ["'" + cell if cell.startswith(("=", "+", "-", "@")) else cell for cell in row]
                )
            workbook.save(path)
            verified = load_workbook(path, read_only=True)
            if not verified.sheetnames or verified.active.max_row < 1:
                raise ValueError("Workbook validation failed")
            verified.close()
        elif format == "docx":
            from docx import Document

            document = Document()
            for paragraph in content.split("\n"):
                document.add_paragraph(paragraph)
            document.save(str(path))
            if not Document(str(path)).paragraphs:
                raise ValueError("DOCX validation failed")
        elif format == "pdf":
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

            styles = getSampleStyleSheet()
            story = []
            for paragraph in content.split("\n"):
                story.extend(
                    [Paragraph(escape(paragraph) or " ", styles["BodyText"]), Spacer(1, 6)]
                )
            SimpleDocTemplate(str(path)).build(story)
            from pypdf import PdfReader

            if not PdfReader(path).pages:
                raise ValueError("PDF validation failed")
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Writer did not create a nonempty file")
        NativeFileParser().parse(path, "." + format)


class FileCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=150)
    content: str = Field(min_length=1, max_length=1_000_000)
    format: Literal["txt", "md", "csv", "xlsx", "docx", "pdf"] = "md"


@router.get("")
def list_files(q: str = "", session: Session = Depends(get_session)):
    statement = select(Attachment).order_by(Attachment.created_at.desc())
    if q:
        statement = statement.where(Attachment.filename.ilike(f"%{q}%"))
    return [record_dict(row) for row in session.scalars(statement)]


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    content = await file.read(MAX_BYTES + 1)
    try:
        row = await asyncio.to_thread(
            ingest, session, request.app.state.data_dir, file.filename or "upload.txt", content
        )
        return record_dict(row)
    except (ValueError, UnicodeError, zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


def write_generated(session: Session, data_dir: Path, payload: FileCreate) -> Attachment:
    from uuid import uuid4

    name = sanitize_filename(payload.filename)
    name = str(Path(name).with_suffix("." + payload.format))
    folder = data_dir / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f"{uuid4().hex}.{payload.format}"
    try:
        LocalFileWriter().write(temp, payload.content, payload.format)
        return ingest(session, data_dir, name, temp.read_bytes(), "generated")
    finally:
        temp.unlink(missing_ok=True)


@router.post("/create")
def create_file(request: Request, payload: FileCreate, session: Session = Depends(get_session)):
    try:
        return record_dict(write_generated(session, request.app.state.data_dir, payload))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{file_id}")
def inspect_file(file_id: str, session: Session = Depends(get_session)):
    row = session.get(Attachment, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    return {**record_dict(row), "chunks": [record_dict(chunk) for chunk in row.chunks]}


@router.get("/{file_id}/content")
def download_file(file_id: str, request: Request, session: Session = Depends(get_session)):
    row = session.get(Attachment, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    path = Path(row.path).resolve()
    if not path.is_relative_to(request.app.state.data_dir) or not path.is_file():
        raise HTTPException(404, "Stored file unavailable")
    return FileResponse(path, filename=row.filename, media_type=row.media_type)


@router.delete("/{file_id}")
def delete_file(file_id: str, confirmed: bool = False, session: Session = Depends(get_session)):
    if not confirmed:
        raise HTTPException(409, "Confirm file deletion")
    row = session.get(Attachment, file_id)
    if not row:
        raise HTTPException(404, "File not found")
    # Keep hash-addressed original bytes for backup/recovery; remove library/index references.
    session.delete(row)
    session.commit()
    return {"deleted": True}
