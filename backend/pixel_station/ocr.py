"""Offline CPU OCR worker. Private input never leaves this process or downloads models."""

import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

OCR_TIMEOUT = 90
MAX_OCR_PAGES = 20
MAX_OCR_PIXELS = 12_000_000
MAX_TOTAL_OCR_PIXELS = 100_000_000
MAX_OCR_CHARS = 3_000_000
_OCR_LOCK = threading.Lock()

# Published RapidOCR 3.9.2 manifest checksums; multilingual recognition includes accents.
OCR_MODELS = {
    "Det": (
        "ch_PP-OCRv4_det_mobile.onnx",
        "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
        "PP-OCRv4/det",
    ),
    "Cls": (
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
        "PP-OCRv4/cls",
    ),
    "Rec": (
        "PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
        "PP-OCRv6/rec",
    ),
}


def ocr_model_directory() -> Path:
    from .config import data_directory

    return Path(os.environ.get("PIXEL_STATION_OCR_MODELS", data_directory() / "models/rapidocr"))


def ocr_setup_error(model_dir: Path | None = None, *, pdf: bool = False) -> str | None:
    needed = ["rapidocr", "onnxruntime"] + (["pypdfium2"] if pdf else [])
    if any(importlib.util.find_spec(name) is None for name in needed):
        return (
            "Local OCR dependencies are missing. Install backend[documents] and local OCR models."
        )
    folder = model_dir or ocr_model_directory()
    for name, checksum, _ in OCR_MODELS.values():
        path = folder / name
        if not path.is_file():
            return "Local OCR models are missing. Run scripts/setup_ocr.py to download verified models."
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            return "Local OCR model checksum failed. Run scripts/setup_ocr.py to restore verified models."
    return None


def parse_ocr(path: Path, pages: list[int] | None = None) -> list[dict]:
    """A deadline includes queue time; killed workers release their native allocations."""
    if pages is not None and (not pages or len(pages) > MAX_OCR_PAGES):
        raise ValueError(f"Local OCR supports at most {MAX_OCR_PAGES} scanned pages per file")
    deadline = time.monotonic() + OCR_TIMEOUT
    if not _OCR_LOCK.acquire(timeout=OCR_TIMEOUT):
        raise ValueError("Local OCR timed out waiting for another document")
    try:
        arguments = [
            sys.executable,
            "-m",
            "pixel_station.ocr",
            str(path),
            str(ocr_model_directory()),
        ]
        if pages is not None:
            arguments.append(",".join(str(page) for page in pages))
        environment = {
            **os.environ,
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "HF_HUB_OFFLINE": "1",
        }
        try:
            result = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=max(0.001, deadline - time.monotonic()),
                env=environment,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Local OCR exceeded its {OCR_TIMEOUT}-second deadline") from exc
        if result.returncode:
            raise ValueError("Local OCR worker failed; no OCR text was indexed")
        parsed = json.loads(result.stdout)
        if "error" in parsed:
            raise ValueError(parsed["error"])
        sections = parsed.get("sections")
        if not isinstance(sections, list) or len(sections) > MAX_OCR_PAGES:
            raise ValueError("Local OCR returned invalid page metadata")
        if sum(len(section["text"]) for section in sections) > MAX_OCR_CHARS:
            raise ValueError("Local OCR text exceeds 3 million characters")
        return sections
    finally:
        _OCR_LOCK.release()


def _engine(model_dir: Path):
    import cv2
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    cv2.setNumThreads(2)
    return RapidOCR(
        params={
            **{
                f"{stage}.model_path": str(model_dir / info[0])
                for stage, info in OCR_MODELS.items()
            },
            "Global.log_level": "error",
            "Global.max_side_len": 3000,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV4,
            "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.rec_batch_num": 2,
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
        }
    )


def _recognize(engine, image, page: int, location: str) -> dict:
    result = engine(image)
    # RapidOCR filters recognition below its explicit 0.5 default confidence.
    content = "\n".join(result.txts or ())
    if len(content) > MAX_OCR_CHARS:
        raise ValueError("Local OCR text exceeds 3 million characters")
    return {"text": content, "page": page, "location": location, "heading": ""}


def _worker(path: Path, model_dir: Path, pages: list[int] | None) -> list[dict]:
    from PIL import Image, ImageOps

    problem = ocr_setup_error(model_dir, pdf=path.suffix.lower() == ".pdf")
    if problem:
        raise ValueError(problem)
    engine = _engine(model_dir)
    if path.suffix.lower() != ".pdf":
        with Image.open(path) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Image exceeds 40 megapixels")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((3000, 3000))
            return [_recognize(engine, image, 1, "image, frame 1")]

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    sections = []
    pixels = 0
    try:
        selected = pages if pages is not None else list(range(len(document)))
        if not selected or len(selected) > MAX_OCR_PAGES:
            raise ValueError(f"Local OCR supports at most {MAX_OCR_PAGES} scanned pages per file")
        if len(set(selected)) != len(selected) or any(
            p < 0 or p >= len(document) for p in selected
        ):
            raise ValueError("Local OCR page selection is invalid")
        for index in selected:
            page = document[index]
            bitmap = None
            try:
                width, height = page.get_size()
                page_pixels = math.ceil(width * 2) * math.ceil(height * 2)
                pixels += page_pixels
                if page_pixels > MAX_OCR_PIXELS or pixels > MAX_TOTAL_OCR_PIXELS:
                    raise ValueError("Scanned PDF exceeds local OCR rendering pixel limits")
                bitmap = page.render(scale=2)
                sections.append(
                    _recognize(
                        engine, bitmap.to_pil().convert("RGB"), index + 1, f"page {index + 1}"
                    )
                )
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        if sum(len(section["text"]) for section in sections) > MAX_OCR_CHARS:
            raise ValueError("Local OCR text exceeds 3 million characters")
        return sections
    finally:
        document.close()


if __name__ == "__main__":
    response: dict
    try:
        selected_pages = (
            [int(value) for value in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
        )
        response = {"sections": _worker(Path(sys.argv[1]), Path(sys.argv[2]), selected_pages)}
    except ValueError as exc:
        response = {"error": str(exc)[:500]}
    except Exception as exc:
        response = {"error": f"Local OCR failed ({type(exc).__name__}); no OCR text was indexed"}
    print(json.dumps(response, ensure_ascii=True))
