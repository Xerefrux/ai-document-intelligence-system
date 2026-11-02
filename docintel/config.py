"""Settings for the whole application.

Everything is read from environment variables (prefix DOCINTEL_) or a .env file, once, and
validated at startup. Other modules receive a Settings object; none of them read os.environ.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCINTEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ---- storage ---------------------------------------------------------
    data_dir: Path = Path("data")
    database_url: str = ""

    # ---- server ----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=0, le=65535)
    cors_origins: str = ""
    api_key: str = ""
    rate_limit_per_minute: int = Field(600, ge=0)
    trust_proxy_headers: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    # ---- uploads and safety limits ---------------------------------------
    max_upload_mb: int = Field(100, ge=1)
    max_files_per_request: int = Field(100, ge=1)
    max_request_mb: int = Field(512, ge=1)
    max_pdf_pages: int = Field(3000, ge=1)
    max_image_pixels: int = Field(100_000_000, ge=1_000_000)
    max_tiff_frames: int = Field(200, ge=1)
    max_sheet_rows: int = Field(200_000, ge=100)
    max_sheet_cols: int = Field(200, ge=5)
    max_sheets: int = Field(100, ge=1)
    max_zip_uncompressed_mb: int = Field(500, ge=1)
    dedupe_files: bool = True

    # ---- background jobs -------------------------------------------------
    workers: int = Field(1, ge=0, le=32)
    job_max_attempts: int = Field(3, ge=1, le=10)
    job_backoff_seconds: float = Field(5.0, ge=0)
    job_stale_seconds: int = Field(120, ge=10)
    job_poll_seconds: float = Field(0.5, ge=0.05)
    job_heartbeat_seconds: float = Field(10.0, ge=1)
    page_commit_batch: int = Field(20, ge=1)
    document_timeout_seconds: int = Field(7200, ge=10)

    # ---- OCR and PDF reading ---------------------------------------------
    ocr_engine: Literal["auto", "tesseract", "rapidocr"] = "auto"
    ocr_dpi: int = Field(200, ge=72, le=600)
    ocr_max_side: int = Field(3000, ge=800)
    ocr_processes: int = Field(0, ge=0, le=32)
    ocr_retry_below: float = Field(0.80, ge=0, le=1)
    ocr_low_quality_below: float = Field(0.70, ge=0, le=1)
    ocr_page_timeout_seconds: int = Field(180, ge=5)
    ocr_retry_budget_seconds: float = Field(15.0, ge=1, description="Stop trying other rotations and image clean-ups on one page after this long. Hopeless pages are then flagged instead of costing minutes.")
    text_layer_min_chars: int = Field(30, ge=1)
    tesseract_cmd: str = "tesseract"
    tesseract_langs: str = "eng"

    # ---- extraction ------------------------------------------------------
    extraction_mode: Literal["rules", "llm", "hybrid", "auto"] = "auto"
    date_order: Literal["auto", "dmy", "mdy"] = "auto"
    amount_tolerance: float = Field(0.05, ge=0)
    review_confidence: float = Field(0.60, ge=0, le=1)
    max_records_per_document: int = Field(5000, ge=1)

    # ---- LLM -------------------------------------------------------------
    llm_provider: Literal["auto", "none", "gemini", "openai"] = "auto"
    gemini_api_key: str = Field("", validation_alias=AliasChoices("DOCINTEL_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))
    gemini_models: str = Field(
        "gemini-flash-latest,gemini-2.5-flash,gemini-flash-lite-latest",
        validation_alias=AliasChoices("DOCINTEL_GEMINI_MODELS", "GEMINI_MODEL"),
    )
    gemini_base_url: str = Field("https://generativelanguage.googleapis.com", validation_alias=AliasChoices("DOCINTEL_GEMINI_BASE_URL", "GEMINI_BASE_URL"))
    openai_api_key: str = Field("", validation_alias=AliasChoices("DOCINTEL_OPENAI_API_KEY", "OPENAI_API_KEY"))
    openai_base_url: str = Field("https://api.openai.com/v1", validation_alias=AliasChoices("DOCINTEL_OPENAI_BASE_URL", "OPENAI_BASE_URL"))
    openai_model: str = Field("", validation_alias=AliasChoices("DOCINTEL_OPENAI_MODEL", "OPENAI_MODEL"))
    llm_timeout_seconds: float = Field(60.0, ge=1)
    llm_max_retries: int = Field(2, ge=0, le=6)
    llm_cooldown_seconds: float = Field(60.0, ge=0)
    llm_failure_threshold: int = Field(3, ge=1)
    llm_max_input_chars: int = Field(30000, ge=2000)
    llm_concurrency: int = Field(4, ge=1, le=16)
    llm_redact_pii: bool = True
    llm_strict_grounding: bool = True

    # ---- search ----------------------------------------------------------
    embedding_provider: Literal["hash", "gemini", "openai", "none"] = "hash"
    embedding_model: str = ""
    embedding_dim: int = Field(512, ge=64, le=4096)

    @model_validator(mode="after")
    def _check_provider_settings(self) -> "Settings":
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("DOCINTEL_LLM_PROVIDER=gemini needs GEMINI_API_KEY")
        if self.llm_provider == "openai" and not (self.openai_api_key and self.openai_model):
            raise ValueError("DOCINTEL_LLM_PROVIDER=openai needs OPENAI_API_KEY and OPENAI_MODEL")
        if self.embedding_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("DOCINTEL_EMBEDDING_PROVIDER=gemini needs GEMINI_API_KEY")
        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError("DOCINTEL_EMBEDDING_PROVIDER=openai needs OPENAI_API_KEY")
        return self

    # ---- derived values --------------------------------------------------
    @property
    def db_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite:///{(self.data_dir / 'docintel.db').resolve().as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        return self.max_request_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def resolved_llm_provider(self) -> str:
        """The provider that will actually be used: gemini, openai or none."""
        if self.llm_provider == "none":
            return "none"
        if self.llm_provider == "gemini" or (self.llm_provider == "auto" and self.gemini_api_key):
            return "gemini" if self.gemini_api_key else "none"
        if self.llm_provider == "openai" or (self.llm_provider == "auto" and self.openai_api_key and self.openai_model):
            return "openai" if (self.openai_api_key and self.openai_model) else "none"
        return "none"

    @property
    def llm_enabled(self) -> bool:
        return self.resolved_llm_provider != "none" and self.extraction_mode != "rules"

    @property
    def ocr_process_count(self) -> int:
        if self.ocr_processes:
            return self.ocr_processes
        return max(1, min(os.cpu_count() or 1, 4))

    @property
    def tesseract_available(self) -> bool:
        return shutil.which(self.tesseract_cmd) is not None

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.previews_dir):
            d.mkdir(parents=True, exist_ok=True)

    def public_view(self) -> dict:
        """Settings that are safe to show in the UI. Secrets become booleans."""
        return {
            "version": __import__("docintel").__version__,
            "database": "sqlite" if self.is_sqlite else self.db_url.split(":", 1)[0],
            "auth_required": bool(self.api_key),
            "max_upload_mb": self.max_upload_mb,
            "max_files_per_request": self.max_files_per_request,
            "max_request_mb": self.max_request_mb,
            "max_pdf_pages": self.max_pdf_pages,
            "workers": self.workers,
            "ocr_engine": self.ocr_engine,
            "tesseract_available": self.tesseract_available,
            "extraction_mode": self.extraction_mode,
            "llm_provider": self.resolved_llm_provider,
            "llm_enabled": self.llm_enabled,
            "llm_strict_grounding": self.llm_strict_grounding,
            "llm_redact_pii": self.llm_redact_pii,
            "embedding_provider": self.embedding_provider,
            "amount_tolerance": self.amount_tolerance,
            "date_order": self.date_order,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
