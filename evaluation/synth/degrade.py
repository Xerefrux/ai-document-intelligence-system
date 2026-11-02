"""Turn clean rendered pages into something that looks scanned or photographed."""

from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter

LEVELS = {
    #          dpi         rotation  noise     blur       jpeg      shade
    "light":  ((160, 200), 1.5,      (3, 8),   (0, 0.3),  (70, 90), 0.05),
    "medium": ((120, 150), 3.5,      (10, 18), (0.5, 0.9), (45, 65), 0.2),
    "heavy":  ((96, 110),  5.0,      (20, 28), (0.9, 1.3), (30, 45), 0.3),
}


def rasterize(pdf_path: str | Path, dpi: float) -> list[Image.Image]:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return [doc[i].render(scale=dpi / 72.0).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def degrade(img: Image.Image, rng: random.Random, level: str) -> Image.Image:
    (dpi_lo, dpi_hi), max_rot, (n_lo, n_hi), (b_lo, b_hi), (q_lo, q_hi), shade = LEVELS[level]
    np_rng = np.random.default_rng(rng.randrange(1 << 30))
    angle = rng.uniform(-max_rot, max_rot)
    out = img.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    blur = rng.uniform(b_lo, b_hi)
    if blur > 0.05:
        out = out.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(out).astype(np.float32)
    if shade:
        h, w = arr.shape[:2]
        grad = np.linspace(1.0 - shade, 1.0, w, dtype=np.float32)[None, :, None]
        if rng.random() < 0.5:
            grad = grad[:, ::-1, :]
        arr = arr * grad
    arr += np_rng.normal(0, rng.uniform(n_lo, n_hi), arr.shape)
    if level == "heavy":
        mask = np_rng.random(arr.shape[:2])
        arr[mask < 0.002] = 0
        arr[mask > 0.998] = 255
        arr = 255 - (255 - arr) * 0.85  # faded ink
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=rng.randint(q_lo, q_hi))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def scan_dpi(rng: random.Random, level: str) -> float:
    lo, hi = LEVELS[level][0]
    return rng.uniform(lo, hi)


def save_image_pdf(images: list[Image.Image], path: str | Path, dpi: float) -> None:
    first, rest = images[0], images[1:]
    first.save(str(path), "PDF", resolution=dpi, save_all=True, append_images=rest)


def save_tiff(images: list[Image.Image], path: str | Path, dpi: float) -> None:
    gray = [im.convert("L") for im in images]
    gray[0].save(str(path), "TIFF", save_all=True, append_images=gray[1:], compression="tiff_lzw", dpi=(dpi, dpi))
