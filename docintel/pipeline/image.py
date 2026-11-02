"""Image files: JPG, PNG and TIFF (a multi-page TIFF is several pages)."""

from __future__ import annotations

import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import Settings
from .types import CorruptFileError, LimitExceededError, PageContent


def _open(path: str, settings: Settings) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return Image.open(path)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise LimitExceededError(
            f"The image is larger than the {settings.max_image_pixels:,} pixel limit.", code="IMAGE_TOO_LARGE"
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptFileError(f"The image could not be opened ({exc}). It may be damaged.") from exc


def load_image_frame(path: str, index: int, settings: Settings) -> np.ndarray:
    """Return frame `index` as an RGB array. Raises CorruptFileError for damaged or cut-off files."""
    im = _open(path, settings)
    try:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(index)
        im.load()  # a truncated file fails here, not later in OCR
        im = ImageOps.exif_transpose(im) or im
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba)
        if im.mode.startswith("I;16") or im.mode in ("I", "F"):
            arr = np.asarray(im, dtype=np.float64)
            top = arr.max() or 1.0
            im = Image.fromarray((arr / top * 255).astype(np.uint8))
        return np.asarray(im.convert("RGB"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise LimitExceededError("The image is larger than the pixel limit.", code="IMAGE_TOO_LARGE") from exc
    except EOFError as exc:
        raise CorruptFileError(f"Frame {index + 1} does not exist in this image.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptFileError(f"The image data is damaged or cut off ({exc}).") from exc
    finally:
        try:
            im.close()
        except Exception:  # pragma: no cover
            pass


class ImageReader:
    kind = "image"

    def __init__(self, path: str, settings: Settings):
        self.path = path
        self.settings = settings
        im = _open(path, settings)
        try:
            frames = getattr(im, "n_frames", 1)
        finally:
            im.close()
        self.warnings: list[str] = []
        if frames > settings.max_tiff_frames:
            self.warnings.append(f"The file has {frames} frames; only the first {settings.max_tiff_frames} are read.")
            frames = settings.max_tiff_frames
        self.page_count = frames
        # Decode the first frame now so a damaged file fails fast, before it is queued for OCR work.
        load_image_frame(path, 0, settings)

    def close(self) -> None:  # pragma: no cover - nothing held open
        pass

    def try_text_page(self, index: int) -> PageContent | None:
        return None  # images always need OCR
