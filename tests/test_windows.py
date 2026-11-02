"""Things that only go wrong on Windows.

I could not run the project on Windows while building it, so each test here forces the situation Windows creates and
checks that the code copes. They run on every system.
"""

from __future__ import annotations

import importlib
import mimetypes
import subprocess
import sys
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from docintel.api.app import STATIC_DIR, create_app
from docintel.jobs.worker import Worker
from docintel.pipeline import ocr
from tests.conftest import make_settings
from tests.factory import Factory

ROOT = Path(__file__).resolve().parent.parent


def test_the_web_app_gets_the_right_script_type_even_if_the_system_says_otherwise(tmp_path):
    """Windows takes file types from its registry. Where .js is listed as text/plain, the browser refuses to run the
    page's script and shows a blank screen, so the service sets the types it needs itself."""
    assets = STATIC_DIR / "assets"
    script, sheet = next(assets.glob("*.js")), next(assets.glob("*.css"))
    mimetypes.add_type("text/plain", ".js")  # what a bad registry entry does
    mimetypes.add_type("text/plain", ".css")
    try:
        c = TestClient(create_app(make_settings(tmp_path), start_workers=False))
        assert c.get(f"/assets/{script.name}").headers["content-type"].startswith("text/javascript")
        assert c.get(f"/assets/{sheet.name}").headers["content-type"].startswith("text/css")
    finally:
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/css", ".css")


def test_the_command_line_also_runs_as_python_dash_m():
    """`docintel` is a launcher that Windows may not have on the PATH or may block; `python -m docintel` needs neither."""
    done = subprocess.run([sys.executable, "-m", "docintel", "--version"], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("docintel ")


def test_a_pdf_can_be_rendered_from_memory_so_that_windows_can_delete_the_file(tmp_path, monkeypatch):
    """Windows will not delete a file while any handle to it is open. On Windows the cached PDF is read into memory, so
    nothing holds the file. The picture must be the same either way."""
    pdf, _ = Factory(tmp_path / "in").pdf("one.pdf", seed=5, template="T1", tax_rate=10.0)
    ocr.release_cached_pdfs()
    from_disk = ocr.render_pdf_page(str(pdf), 0, 100, 1500)
    monkeypatch.setattr(ocr, "_OPEN_FROM_MEMORY", True)
    ocr.release_cached_pdfs()
    try:
        from_memory = ocr.render_pdf_page(str(pdf), 0, 100, 1500)
    finally:
        ocr.release_cached_pdfs()
    assert from_disk.shape == from_memory.shape and np.array_equal(from_disk, from_memory)


def test_deleting_a_document_still_succeeds_when_the_file_system_refuses_to_remove_the_file(tmp_path, monkeypatch):
    """The record is removed first. If Windows then says the file is in use, the request must not turn into an error."""
    s = make_settings(tmp_path)
    app = create_app(s, start_workers=False)
    c = TestClient(app, raise_server_exceptions=False)
    pdf, _ = Factory(tmp_path / "in").pdf("a.pdf", seed=7, template="T2", tax_rate=10.0)
    r = c.post("/api/documents", files=[("files", (pdf.name, pdf.read_bytes(), "application/octet-stream"))])
    assert r.status_code in (200, 201, 202), r.text
    assert Worker(s, app.state.factory, name="test-worker").drain() == 1
    doc = c.get("/api/documents").json()["items"][0]

    real_unlink = Path.unlink

    def refuse(self, missing_ok=False):
        if self.parent == s.uploads_dir:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    assert c.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert c.get(f"/api/documents/{doc['id']}").status_code == 404


def test_the_speed_test_script_still_imports_without_the_unix_resource_module(monkeypatch):
    """`resource` exists only on Linux and macOS. The speed test must start on Windows, with the memory figure left out."""
    monkeypatch.setitem(sys.modules, "resource", None)  # makes `import resource` fail, as it does on Windows
    sys.modules.pop("evaluation.perf", None)
    try:
        perf = importlib.import_module("evaluation.perf")
        assert perf.resource is None
        assert perf.peak_memory_mb() is None
    finally:
        sys.modules.pop("evaluation.perf", None)
