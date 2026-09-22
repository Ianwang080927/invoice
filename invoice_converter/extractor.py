from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageOps

from .models import InvoiceRecord, OcrLine
from .parser import looks_like_invoice, parse_invoice_text


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg"}


@dataclass(slots=True)
class ExtractedText:
    text: str
    method: str
    confidence: float | None
    ocr_lines: list[OcrLine] = field(default_factory=list)


class InvoiceExtractor:
    def __init__(self, *, ocr_languages: str = "ch_sim+en") -> None:
        self.ocr_languages = ocr_languages
        self._ocr_engine = None
        self._modern_rapidocr = False

    def _get_ocr_engine(self):
        if self._ocr_engine is None:
            try:
                from rapidocr import RapidOCR
                self._modern_rapidocr = True
            except ImportError:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    raise RuntimeError(
                        "缺少 OCR 组件 RapidOCR。请先运行“安装依赖.bat”。"
                    ) from exc
            self._ocr_engine = RapidOCR()
        return self._ocr_engine

    def _ocr_image(self, image: Image.Image) -> ExtractedText:
        image = ImageOps.exif_transpose(image).convert("RGB")
        max_side = max(image.size)
        if max_side < 1800:
            scale = 1800 / max_side
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        engine = self._get_ocr_engine()
        ocr_lines: list[OcrLine] = []
        if self._modern_rapidocr:
            import numpy as np

            result = engine(np.asarray(image))
            txts = getattr(result, "txts", None)
            result_scores = getattr(result, "scores", None)
            result_boxes = getattr(result, "boxes", None)
            raw_lines = list(txts) if txts is not None else []
            raw_scores = list(result_scores) if result_scores is not None else []
            scores = [float(value) for value in raw_scores]
            raw_boxes = list(result_boxes) if result_boxes is not None else []
            for index, value in enumerate(raw_lines):
                text = str(value).strip()
                if not text:
                    continue
                box = raw_boxes[index] if index < len(raw_boxes) else None
                if box is not None:
                    points = [(float(point[0]), float(point[1])) for point in box]
                    confidence = scores[index] if index < len(scores) else None
                    ocr_lines.append(
                        OcrLine(
                            text=text,
                            left=min(point[0] for point in points),
                            top=min(point[1] for point in points),
                            right=max(point[0] for point in points),
                            bottom=max(point[1] for point in points),
                            confidence=confidence,
                        )
                    )
            lines = [str(value).strip() for value in raw_lines if str(value).strip()]
        else:
            result, _ = engine(image)
            if not result:
                return ExtractedText("", "OCR", None)
            lines = [str(item[1]).strip() for item in result if len(item) >= 3 and str(item[1]).strip()]
            scores = [float(item[2]) for item in result if len(item) >= 3]
            for item in result:
                if len(item) < 3 or not str(item[1]).strip():
                    continue
                points = [(float(point[0]), float(point[1])) for point in item[0]]
                ocr_lines.append(
                    OcrLine(
                        text=str(item[1]).strip(),
                        left=min(point[0] for point in points),
                        top=min(point[1] for point in points),
                        right=max(point[0] for point in points),
                        bottom=max(point[1] for point in points),
                        confidence=float(item[2]),
                    )
                )
        if not lines:
            return ExtractedText("", "OCR", None)
        confidence = sum(scores) / len(scores) if scores else None
        return ExtractedText("\n".join(lines), "OCR", confidence, ocr_lines)

    def _extract_pdf_pages(self, path: Path) -> list[ExtractedText]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("缺少 PDF 组件 PyMuPDF。请先运行“安装依赖.bat”。") from exc

        pages: list[ExtractedText] = []
        with fitz.open(path) as document:
            if document.needs_pass:
                raise RuntimeError("PDF 已加密，无法读取")
            for page in document:
                native_text = page.get_text("text", sort=True).strip()
                if len(native_text) >= 80 and looks_like_invoice(native_text):
                    pages.append(ExtractedText(native_text, "PDF文字", None, self._pdf_lines(page)))
                    continue
                matrix = fitz.Matrix(2.5, 2.5)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                pages.append(self._ocr_image(image))
        return pages

    @staticmethod
    def _pdf_lines(page) -> list[OcrLine]:
        lines: list[OcrLine] = []
        page_dict = page.get_text("dict", sort=True)
        for block in page_dict.get("blocks", []):
            for visual_line in block.get("lines", []):
                for span in visual_line.get("spans", []):
                    text = str(span.get("text", "")).strip()
                    bbox = span.get("bbox")
                    if not text or not bbox or len(bbox) != 4:
                        continue
                    left, top, right, bottom = (float(value) for value in bbox)
                    lines.append(OcrLine(text, left, top, right, bottom, None))
        return lines

    def extract_file(self, path: str | os.PathLike[str]) -> list[InvoiceRecord]:
        file_path = Path(path)
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            extracted_pages = self._extract_pdf_pages(file_path)
        elif suffix in {".jpg", ".jpeg"}:
            with Image.open(file_path) as image:
                extracted_pages = [self._ocr_image(image.copy())]
        else:
            raise ValueError(f"不支持的文件类型：{suffix}")

        records = []
        for page_number, extracted in enumerate(extracted_pages, start=1):
            records.append(
                parse_invoice_text(
                    extracted.text,
                    source_file=str(file_path.resolve()),
                    page_number=page_number,
                    recognition_method=extracted.method,
                    ocr_confidence=extracted.confidence,
                    ocr_lines=extracted.ocr_lines,
                )
            )
        return records


def find_invoice_files(folder: str | os.PathLike[str], recursive: bool = True) -> list[Path]:
    root = Path(folder)
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda value: str(value).lower(),
    )


def convert_folder(
    folder: str | os.PathLike[str],
    *,
    recursive: bool = True,
    progress: Callable[[int, int, Path], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[InvoiceRecord], list[str]]:
    files = find_invoice_files(folder, recursive=recursive)
    extractor = InvoiceExtractor()
    records: list[InvoiceRecord] = []
    errors: list[str] = []
    for index, path in enumerate(files, start=1):
        if should_cancel and should_cancel():
            break
        if progress:
            progress(index, len(files), path)
        try:
            records.extend(extractor.extract_file(path))
        except Exception as exc:  # Keep the batch moving and report the failed source.
            errors.append(f"{path.name}: {exc}")
            records.append(
                InvoiceRecord(
                    source_file=str(path.resolve()),
                    recognition_method="失败",
                    status="失败",
                    issues=str(exc),
                )
            )
    _mark_duplicates(records)
    return records, errors


def _mark_duplicates(records: list[InvoiceRecord]) -> None:
    seen: dict[str, int] = {}
    for index, record in enumerate(records):
        if not record.invoice_number:
            continue
        previous = seen.get(record.invoice_number)
        if previous is None:
            seen[record.invoice_number] = index
            continue
        for duplicate_index in (previous, index):
            duplicate = records[duplicate_index]
            duplicate.status = "需复核"
            note = f"重复发票号：{record.invoice_number}"
            if note not in duplicate.issues:
                duplicate.issues = "；".join(filter(None, [duplicate.issues, note]))
