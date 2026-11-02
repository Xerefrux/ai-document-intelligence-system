"""OCR: two engines behind one interface, image clean-up, and a process pool.

Engines
  tesseract  needs the Tesseract binary. Best on tables and blurry scans in our tests.
  rapidocr   pip only, models are bundled with the package. Used when Tesseract is not installed.

`ocr_image` runs one pass on the raw page. If the result looks poor (low confidence or hardly any
text) it tries to rescue the page: rotate by 90/180/270 degrees, then contrast-enhanced and
binarized copies, and keeps whichever pass scored best. Skew is corrected before the first pass.
"""

from __future__ import annotations

import io
import logging
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context

import cv2
import numpy as np
from PIL import Image

from ..config import Settings
from .layout import build_layout
from .pdfium_lock import PDFIUM_LOCK
from .types import OcrError, OcrUnavailableError, PageContent, Segment

log = logging.getLogger(__name__)


@dataclass
class OcrResult:
    segments: list[Segment]
    confidence: float  # 0..1, weighted by characters
    engine: str
    seconds: float

    @property
    def chars(self) -> int:
        return sum(len(s.text.strip()) for s in self.segments)

    @property
    def vertical_share(self) -> float:
        """Share of longer text runs that are taller than wide. A sideways page reads this way."""
        long_runs = [s for s in self.segments if len(s.text.strip()) >= 4]
        if len(long_runs) < 3:
            return 0.0
        return sum(1 for s in long_runs if s.h > 1.2 * s.w) / len(long_runs)

    @property
    def score(self) -> float:
        # Confidence matters most, but a pass that finds hardly any text must not win on confidence alone,
        # and a page whose lines run vertically is probably rotated even when the engine is confident.
        return self.confidence * (1.0 - math.exp(-self.chars / 150.0)) * (1.0 - 0.8 * self.vertical_share)


def _weighted_conf(segments: list[Segment]) -> float:
    total = sum(len(s.text) for s in segments if s.conf is not None)
    if not total:
        return 0.0
    return sum((s.conf or 0.0) * len(s.text) for s in segments if s.conf is not None) / total


class OcrEngine:
    name = "base"

    def recognize(self, rgb: np.ndarray, psm: int = 6) -> OcrResult:  # pragma: no cover - interface
        raise NotImplementedError


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, cmd: str, langs: str, timeout: float):
        self.cmd, self.langs, self.timeout = cmd, langs, timeout

    def recognize(self, rgb: np.ndarray, psm: int = 6) -> OcrResult:
        t0 = time.time()
        buf = io.BytesIO()
        Image.fromarray(rgb).convert("L").save(buf, format="PNG", compress_level=1)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        try:
            proc = subprocess.run(
                [self.cmd, "stdin", "stdout", "--psm", str(psm), "-l", self.langs, "tsv"],
                input=buf.getvalue(), capture_output=True, timeout=self.timeout, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError(f"Tesseract timed out after {self.timeout:.0f}s", code="OCR_TIMEOUT") from exc
        except FileNotFoundError as exc:
            raise OcrUnavailableError(f"Tesseract was not found at '{self.cmd}'.") from exc
        if proc.returncode != 0:
            raise OcrError("Tesseract failed: " + proc.stderr.decode("utf-8", "replace").strip()[-300:])
        segs = self._parse(proc.stdout.decode("utf-8", "replace"))
        return OcrResult(segs, _weighted_conf(segs), self.name, time.time() - t0)

    @staticmethod
    def _parse(tsv: str) -> list[Segment]:
        words = []
        for ln in tsv.splitlines()[1:]:
            p = ln.split("\t")
            if len(p) < 12 or p[0] != "5":
                continue
            text = p[11].strip()
            if not text:
                continue
            try:
                conf = float(p[10])
                left, top, width, height = int(p[6]), int(p[7]), int(p[8]), int(p[9])
            except ValueError:
                continue
            if conf < 0:
                continue
            words.append(((p[2], p[3], p[4]), left, top, width, height, conf / 100.0, text))
        segs: list[Segment] = []
        cur: list | None = None  # [key, x0, y0, x1, y1, [(text, conf)]]
        for key, left, top, width, height, conf, text in words:
            right, bottom = left + width, top + height
            if cur is not None and cur[0] == key and (left - cur[3]) <= 0.9 * max(height, cur[4] - cur[2]):
                cur[3] = max(cur[3], right)
                cur[2] = min(cur[2], top)
                cur[4] = max(cur[4], bottom)
                cur[5].append((text, conf))
                continue
            if cur is not None:
                segs.append(_seg_from(cur))
            cur = [key, left, top, right, bottom, [(text, conf)]]
        if cur is not None:
            segs.append(_seg_from(cur))
        return segs


def _seg_from(cur: list) -> Segment:
    parts = cur[5]
    text = " ".join(t for t, _ in parts)
    weight = sum(len(t) for t, _ in parts) or 1
    conf = sum(len(t) * c for t, c in parts) / weight
    return Segment(text, cur[1], cur[2], cur[3], cur[4], conf)


class RapidOcrEngine(OcrEngine):
    name = "rapidocr"

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # pragma: no cover - depends on the install
            raise OcrUnavailableError(f"RapidOCR is not installed: {exc}") from exc
        self._ocr = RapidOCR()
        self._lock = threading.Lock()

    def recognize(self, rgb: np.ndarray, psm: int = 6) -> OcrResult:
        t0 = time.time()
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        # RapidOCR remembers these three settings on the instance once any are passed, and its defaults
        # drop single digits such as table quantities. Pass them on every call so each page is treated alike.
        with self._lock:
            result, _ = self._ocr(bgr, box_thresh=0.3, unclip_ratio=2.0, text_score=0.3)
        segs: list[Segment] = []
        for box, text, score in result or []:
            text = str(text).strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            segs.append(Segment(text, min(xs), min(ys), max(xs), max(ys), float(score)))
        return OcrResult(segs, _weighted_conf(segs), self.name, time.time() - t0)


_ENGINES: dict[str, OcrEngine] = {}


def resolve_engine_name(settings: Settings) -> str:
    if settings.ocr_engine == "tesseract":
        if not settings.tesseract_available:
            raise OcrUnavailableError(f"OCR engine 'tesseract' was requested but '{settings.tesseract_cmd}' is not installed.")
        return "tesseract"
    if settings.ocr_engine == "rapidocr":
        return "rapidocr"
    return "tesseract" if settings.tesseract_available else "rapidocr"


def get_engine(settings: Settings) -> OcrEngine:
    name = resolve_engine_name(settings)
    if name not in _ENGINES:
        if name == "tesseract":
            _ENGINES[name] = TesseractEngine(settings.tesseract_cmd, settings.tesseract_langs, settings.ocr_page_timeout_seconds)
        else:
            _ENGINES[name] = RapidOcrEngine()
    return _ENGINES[name]


# --------------------------------------------------------------------------- image clean-up


def estimate_skew(rgb: np.ndarray) -> float:
    """Angle (degrees, OpenCV convention) to rotate the page by so text lines run horizontally."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    scale = 800.0 / max(h, w)
    if scale < 1:
        gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if bw.mean() < 255 * 0.002:  # almost no ink
        return 0.0
    hh, ww = bw.shape
    centre = (ww / 2, hh / 2)

    def sharpness(angle: float) -> float:
        m = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rot = cv2.warpAffine(bw, m, (ww, hh), flags=cv2.INTER_NEAREST)
        prof = rot.sum(axis=1).astype(np.float64)
        return float(np.sum(np.diff(prof) ** 2))

    base = sharpness(0.0)
    coarse = max(np.arange(-10.0, 10.01, 1.0), key=sharpness)
    fine = max(np.arange(coarse - 0.9, coarse + 0.91, 0.15), key=sharpness)
    if sharpness(fine) < base * 1.03:
        return 0.0
    return float(fine)


def rotate_expand(rgb: np.ndarray, angle: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(rgb, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def _fit_size(rgb: np.ndarray, max_side: int, min_side_target: int = 1600) -> np.ndarray:
    h, w = rgb.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        f = max_side / longest
        return cv2.resize(rgb, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    if longest < min_side_target:
        f = min(3.0, min_side_target / longest)
        return cv2.resize(rgb, (int(w * f), int(h * f)), interpolation=cv2.INTER_CUBIC)
    return rgb


def _enhance(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.medianBlur(gray, 3)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)


def _binarize(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.medianBlur(gray, 3)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15)
    return cv2.cvtColor(bw, cv2.COLOR_GRAY2RGB)


def ocr_image(rgb: np.ndarray, engine: OcrEngine, settings: Settings) -> tuple[list[Segment], float, dict, tuple[int, int]]:
    """Returns segments, confidence, notes about what was done, and the (width, height) of the image that was read."""
    meta: dict = {"engine": engine.name, "passes": 0}
    started = time.monotonic()

    def over_budget() -> bool:
        if time.monotonic() - started > settings.ocr_retry_budget_seconds:
            meta["budget_exceeded"] = True
            return True
        return False

    rgb = _fit_size(rgb, settings.ocr_max_side)
    angle = estimate_skew(rgb)
    if abs(angle) >= 0.5:
        rgb = rotate_expand(rgb, angle)
        meta["deskew_deg"] = round(angle, 2)

    def run(img: np.ndarray, psm: int = 6) -> OcrResult:
        meta["passes"] += 1
        return engine.recognize(img, psm=psm)

    best_res = run(rgb)
    best_img = rgb
    best_label = "raw"

    def consider(res: OcrResult, img: np.ndarray, label: str) -> None:
        nonlocal best_res, best_img, best_label
        if res.score > best_res.score:
            best_res, best_img, best_label = res, img, label

    sideways = best_res.vertical_share > 0.4
    poor = best_res.confidence < settings.ocr_retry_below or best_res.chars < 40 or sideways
    if poor:
        # Wrong orientation gives garbage or nothing. Try the other three on a smaller copy first.
        if best_res.confidence < 0.55 or best_res.chars < 40 or sideways:
            small = _fit_size(best_img, 1400, min_side_target=0)
            base = run(small) if small is not best_img else best_res
            best_k, best_score = 0, base.score
            for k in (1, 2, 3):
                if over_budget():
                    break
                r = run(np.ascontiguousarray(np.rot90(small, k)))
                if r.score > best_score:
                    best_k, best_score = k, r.score
            if best_k and best_score > base.score * 1.25 + 0.05:
                rotated = np.ascontiguousarray(np.rot90(best_img, best_k))
                res = run(rotated)
                meta["rotation_ccw_deg"] = 90 * best_k
                best_res, best_img, best_label = res, rotated, "rotated"
        for label, fn in (("enhanced", _enhance), ("binarized", _binarize)):
            if best_res.confidence >= settings.ocr_retry_below and best_res.chars >= 40 and best_res.vertical_share <= 0.4:
                break
            if over_budget():
                break
            img = fn(best_img)
            consider(run(img), img, label)
        if best_res.chars < 40 and not over_budget():
            consider(run(best_img, psm=11), best_img, "sparse")
    meta["variant"] = best_label
    h, w = best_img.shape[:2]
    return best_res.segments, best_res.confidence, meta, (w, h)


# --------------------------------------------------------------------------- tasks and pool


@dataclass(frozen=True)
class OcrTask:
    kind: str  # pdf | image
    path: str
    index: int  # 0 based page or frame


_PDF_CACHE: dict[str, object] = {}

# Windows will not delete a file while any handle to it is open. A PDF kept open here, in the API process or in an
# OCR process, would stop that document from being deleted. On Windows the file is therefore read into memory and
# closed at once. Everywhere else it is opened in place, which costs no memory.
_OPEN_FROM_MEMORY = sys.platform == "win32"


def _open_pdf(path: str):
    import pypdfium2 as pdfium

    if _OPEN_FROM_MEMORY:
        with open(path, "rb") as fh:
            return pdfium.PdfDocument(fh.read())
    return pdfium.PdfDocument(path)


def release_cached_pdfs() -> None:
    """Close the PDF this process keeps open between the pages of one document."""
    with PDFIUM_LOCK:
        for old in list(_PDF_CACHE.values()):
            try:
                old.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        _PDF_CACHE.clear()


def _cached_pdf(path: str):
    """The caller holds PDFIUM_LOCK."""
    doc = _PDF_CACHE.get(path)
    if doc is None:
        release_cached_pdfs()
        doc = _open_pdf(path)
        _PDF_CACHE[path] = doc
    return doc


def render_pdf_page(path: str, index: int, dpi: int, max_side: int) -> np.ndarray:
    with PDFIUM_LOCK:  # PDFium must never be used by two threads at once (see pdfium_lock.py)
        doc = _cached_pdf(path)
        page = doc[index]
        try:
            w, h = page.get_size()
            scale = min(dpi / 72.0, max_side / max(w, h, 1.0))
            pil = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            page.close()
    return np.asarray(pil)


def run_ocr_task(task: OcrTask, settings: Settings) -> PageContent:
    """Read one page by OCR. Runs inside a pool process (or inline)."""
    from .image import load_image_frame

    if task.kind == "pdf":
        rgb = render_pdf_page(task.path, task.index, settings.ocr_dpi, settings.ocr_max_side)
    else:
        rgb = load_image_frame(task.path, task.index, settings)
    engine = get_engine(settings)
    segments, conf, meta, (w, h) = ocr_image(rgb, engine, settings)
    warnings: list[str] = []
    if not segments:
        warnings.append("OCR found no text on this page.")
    elif conf < settings.ocr_low_quality_below:
        warnings.append(f"Low OCR confidence ({conf:.2f}).")
    return PageContent(
        page_no=task.index + 1,
        source="ocr",
        text=build_layout(segments),
        width=float(w),
        height=float(h),
        ocr_confidence=round(conf, 4) if segments else 0.0,
        warnings=warnings,
        meta=meta,
    )


def _pool_init(settings: Settings) -> None:  # pragma: no cover - runs in the child process
    try:
        get_engine(settings)
    except Exception:
        pass


class OcrPool:
    """Runs OCR tasks on several processes. With one process the work is done inline."""

    def __init__(self, settings: Settings, processes: int | None = None):
        self.settings = settings
        self.n = processes if processes is not None else settings.ocr_process_count
        self._ex: ProcessPoolExecutor | None = None
        self._start()

    def _start(self) -> None:
        if self.n > 1:
            self._ex = ProcessPoolExecutor(
                max_workers=self.n, mp_context=get_context("spawn"), initializer=_pool_init, initargs=(self.settings,)
            )

    def submit(self, task: OcrTask) -> Future:
        if self._ex is None:
            fut: Future = Future()
            try:
                fut.set_result(run_ocr_task(task, self.settings))
            except BaseException as exc:  # noqa: BLE001 - delivered through the future
                fut.set_exception(exc)
            return fut
        return self._ex.submit(run_ocr_task, task, self.settings)

    def restart(self) -> None:
        """Replace the pool after a crash or a stuck page."""
        self.close(wait=False)
        self._start()

    def close(self, wait: bool = True) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=wait, cancel_futures=True)
            self._ex = None

    def __enter__(self) -> "OcrPool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
