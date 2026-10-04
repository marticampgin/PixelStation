import hashlib
import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from pixel_station import ocr
from pixel_station.database import Database
from pixel_station.files import DocumentParser, NativeFileParser, ingest


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (240, 120), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_native_pdf_does_not_load_ocr_or_docling(tmp_path, monkeypatch):
    path = tmp_path / "native.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(36, 700, "Native text, page one")
    canvas.showPage()
    canvas.drawString(36, 700, "Native text, page two")
    canvas.save()

    def unexpected(*args, **kwargs):
        raise AssertionError("Text PDF must not initialize OCR")

    monkeypatch.setattr(ocr, "ocr_setup_error", unexpected)
    monkeypatch.setattr(ocr, "parse_ocr", unexpected)
    sections, parser, warning = DocumentParser().parse(path, ".pdf")
    assert parser == "native-text-pdf" and warning is None
    assert [section["page"] for section in sections] == [1, 2]
    assert all(section["text"].startswith("Native text") for section in sections)


def test_mixed_pdf_recognizes_only_missing_pages_and_keeps_provenance(tmp_path, monkeypatch):
    native = [
        {"text": "Preserve native contract", "page": 1, "location": "page 1", "heading": ""},
        {"text": "", "page": 2, "location": "page 2", "heading": ""},
    ]
    monkeypatch.setattr(NativeFileParser, "parse", lambda *args: native)
    monkeypatch.setattr(ocr, "ocr_setup_error", lambda **kwargs: None)
    observed = []

    def recognize(path, pages):
        observed.append(pages)
        return [{"text": "Rental date: 2026-11-16", "page": 2, "location": "page 2", "heading": ""}]

    monkeypatch.setattr(ocr, "parse_ocr", recognize)
    sections, parser, warning = DocumentParser().parse(tmp_path / "mixed.pdf", ".pdf")
    assert observed == [[1]]
    assert parser == "native-and-rapidocr-cpu" and warning is None
    assert sections[0]["text"] == "Preserve native contract"
    assert sections[1]["page"] == 2 and sections[1]["location"] == "page 2"


def test_ocr_missing_is_explicit_and_preserves_any_native_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_setup_error", lambda **kwargs: "Local OCR models are missing")
    sections, parser, warning = DocumentParser().parse(tmp_path / "photo.png", ".png")
    assert sections == [] and parser == "image" and "missing" in warning
    monkeypatch.setattr(
        NativeFileParser,
        "parse",
        lambda *args: [{"text": "native", "page": 1}, {"text": "", "page": 2}],
    )
    sections, parser, warning = DocumentParser().parse(tmp_path / "mixed.pdf", ".pdf")
    assert parser == "native" and "not OCR processed" in warning
    assert sections[0]["text"] == "native"


def test_image_ingest_indexes_ocr_and_records_actual_worker_failure(tmp_path, monkeypatch):
    database = Database(tmp_path)
    database.migrate()
    monkeypatch.setattr(ocr, "ocr_setup_error", lambda **kwargs: None)
    monkeypatch.setattr(
        ocr,
        "parse_ocr",
        lambda *args: [
            {
                "text": "Signature: 2026-10-04",
                "page": 1,
                "location": "image, frame 1",
                "heading": "",
            }
        ],
    )
    try:
        with database.session() as session:
            row = ingest(session, tmp_path, "rental.png", image_bytes())
            assert row.parse_status == "ready" and row.parser == "rapidocr-cpu"
            assert row.chunks[0].page == 1 and row.chunks[0].location == "image, frame 1"
            assert "2026-10-04" in row.chunks[0].text

            def failed(*args):
                raise ValueError("Local OCR exceeded its 90-second deadline")

            monkeypatch.setattr(ocr, "parse_ocr", failed)
            alternate = io.BytesIO()
            Image.new("RGB", (241, 121), "white").save(alternate, format="PNG")
            failed_row = ingest(session, tmp_path, "unreadable.png", alternate.getvalue())
            assert failed_row.parse_status == "error"
            assert "deadline" in failed_row.parse_error and not failed_row.chunks
    finally:
        database.engine.dispose()


def test_blank_ocr_image_has_no_invented_text(tmp_path, monkeypatch):
    path = tmp_path / "blank.png"
    path.write_bytes(image_bytes())
    monkeypatch.setattr(ocr, "ocr_setup_error", lambda **kwargs: None)
    monkeypatch.setattr(
        ocr,
        "parse_ocr",
        lambda *args: [{"text": "", "page": 1, "location": "image, frame 1", "heading": ""}],
    )
    parts, parser, warning = DocumentParser().parse(path, ".png")
    assert parts[0]["text"] == "" and parser == "rapidocr-cpu"
    assert "No readable text" in warning


def test_model_setup_checks_dependencies_and_checksums(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: None)
    assert "dependencies" in ocr.ocr_setup_error(tmp_path)
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        ocr, "OCR_MODELS", {"Rec": ("rec.onnx", hashlib.sha256(b"verified").hexdigest(), "")}
    )
    assert "missing" in ocr.ocr_setup_error(tmp_path)
    (tmp_path / "rec.onnx").write_bytes(b"verified")
    assert ocr.ocr_setup_error(tmp_path) is None
    (tmp_path / "rec.onnx").write_bytes(b"corrupted")
    assert "checksum" in ocr.ocr_setup_error(tmp_path)


def test_ocr_deadline_kills_worker_and_releases_queue(tmp_path, monkeypatch):
    calls = []

    def process(arguments, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"sections": [{"text": "read", "page": 1}]})
        )

    monkeypatch.setattr(ocr.subprocess, "run", process)
    with pytest.raises(ValueError, match="deadline"):
        ocr.parse_ocr(tmp_path / "image.png")
    assert ocr.parse_ocr(tmp_path / "image.png")[0]["text"] == "read"
    assert all(0 < call["timeout"] <= ocr.OCR_TIMEOUT for call in calls)
    assert calls[0]["env"]["HF_HUB_OFFLINE"] == "1"
    assert calls[0]["env"]["OMP_NUM_THREADS"] == "2"


@pytest.mark.parametrize(
    "result, message",
    [
        (SimpleNamespace(returncode=1, stdout="private native exception"), "worker failed"),
        (
            SimpleNamespace(returncode=0, stdout=json.dumps({"error": "Pixel limit exceeded"})),
            "Pixel limit",
        ),
        (
            SimpleNamespace(returncode=0, stdout=json.dumps({"sections": "not metadata"})),
            "invalid page metadata",
        ),
    ],
)
def test_worker_failure_is_not_silently_visual(tmp_path, monkeypatch, result, message):
    monkeypatch.setattr(ocr.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(ValueError, match=message):
        ocr.parse_ocr(tmp_path / "image.png")


@pytest.mark.parametrize("pages", [[], list(range(21))])
def test_pdf_page_budget_rejects_before_spawning_worker(tmp_path, monkeypatch, pages):
    monkeypatch.setattr(
        ocr.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must reject before worker")
    )
    with pytest.raises(ValueError, match="20 scanned pages"):
        ocr.parse_ocr(tmp_path / "scan.pdf", pages)


def test_worker_rejects_oversized_pdf_before_rendering_and_closes_handles(tmp_path, monkeypatch):
    closed = []
    page = SimpleNamespace(
        get_size=lambda: (100_000, 100_000),
        render=lambda **kwargs: pytest.fail("Rendering exceeds bound"),
        close=lambda: closed.append("page"),
    )

    class Document:
        def __len__(self):
            return 1

        def __getitem__(self, key):
            return page

        def close(self):
            closed.append("document")

    monkeypatch.setitem(
        sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=lambda path: Document())
    )
    monkeypatch.setattr(ocr, "ocr_setup_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(ocr, "_engine", lambda *args: object())
    with pytest.raises(ValueError, match="pixel limits"):
        ocr._worker(tmp_path / "huge.pdf", tmp_path, [0])
    assert closed == ["page", "document"]


def test_worker_does_not_initialize_engine_without_verified_models(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ocr, "ocr_setup_error", lambda *args, **kwargs: "Local OCR models are missing"
    )
    monkeypatch.setattr(ocr, "_engine", lambda *args: pytest.fail("Must not download/init models"))
    with pytest.raises(ValueError, match="models are missing"):
        ocr._worker(tmp_path / "image.png", tmp_path, None)
