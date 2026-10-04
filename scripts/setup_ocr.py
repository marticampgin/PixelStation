"""Download small public CPU OCR weights; never called implicitly on private uploads."""

import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pixel_station.ocr import OCR_MODELS, ocr_model_directory, ocr_setup_error


def main() -> None:
    folder = ocr_model_directory()
    folder.mkdir(parents=True, exist_ok=True)
    for name, checksum, suffix in OCR_MODELS.values():
        path = folder / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            print(f"Verified {name}")
            continue
        temporary = path.with_suffix(".partial")
        try:
            url = f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/{suffix}/{name}"
            count = 0
            digest = hashlib.sha256()
            with urlopen(url, timeout=30) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > 80 * 1024 * 1024:
                        raise ValueError("OCR weight download exceeds its 80 MiB limit")
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != checksum:
                raise ValueError(f"Public OCR model checksum mismatch: {name}")
            temporary.replace(path)
            print(f"Downloaded and verified {name}: {count} bytes")
        finally:
            temporary.unlink(missing_ok=True)
    if problem := ocr_setup_error():
        raise ValueError(problem)
    print("Local CPU OCR dependencies and checksummed models are ready.")


if __name__ == "__main__":
    main()
