"""Shared fixtures. Sample files are generated while the tests run, so the repository carries no binary test data."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Never let a key on the developer's machine make a test call a real service.
for _k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "DOCINTEL_API_KEY", "DOCINTEL_GEMINI_API_KEY", "DOCINTEL_OPENAI_API_KEY"):
    os.environ.pop(_k, None)

from docintel.config import Settings  # noqa: E402

HAVE_TESSERACT = bool(shutil.which("tesseract"))
needs_tesseract = pytest.mark.skipif(not HAVE_TESSERACT, reason="Tesseract is not installed")


def make_settings(tmp_path: Path, **over) -> Settings:
    base = dict(
        data_dir=tmp_path / "data", workers=0, job_backoff_seconds=0.05, rate_limit_per_minute=0, ocr_processes=1,
        embedding_provider="hash", llm_provider="none", extraction_mode="rules", _env_file=None,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def make(tmp_path):
    """Helper that builds sample files in a temporary folder."""
    from tests.factory import Factory

    return Factory(tmp_path / "samples")
