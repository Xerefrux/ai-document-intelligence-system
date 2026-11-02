"""Command line: docintel serve | worker | process | seed | reindex | doctor."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .config import Settings, get_settings
from .logging_setup import setup_logging


def _settings(args: argparse.Namespace) -> Settings:
    s = get_settings()
    over = {}
    if getattr(args, "data_dir", None):
        over["data_dir"] = Path(args.data_dir)
    if getattr(args, "host", None):
        over["host"] = args.host
    if getattr(args, "port", None) is not None:
        over["port"] = args.port
    if getattr(args, "workers", None) is not None:
        over["workers"] = args.workers
    if over:
        s = s.model_copy(update=over)
    return s


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    s = _settings(args)
    setup_logging(s.log_level, s.log_json, s.data_dir / "logs" / "docintel.log")
    app = create_app(s)
    print(f"DocIntel {__version__}: http://{s.host}:{s.port}  (API docs at /api/docs)", flush=True)
    if s.workers == 0:
        print("No background workers in this process. Start one with: docintel worker", flush=True)
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning", access_log=False, timeout_graceful_shutdown=30)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .db.engine import init_db, make_engine, make_session_factory
    from .jobs.queue import recover_dead_local
    from .jobs.worker import Worker

    s = _settings(args)
    setup_logging(s.log_level, s.log_json, s.data_dir / "logs" / "worker.log")
    s.ensure_dirs()
    engine = make_engine(s.db_url)
    init_db(engine)
    factory = make_session_factory(engine)
    recover_dead_local(factory, s)
    from .api.embedded import _SharedPool
    from .extraction.llm import LLMExtractor

    pool = _SharedPool(s)
    llm = LLMExtractor(s) if s.llm_enabled else None
    host, pid = __import__("socket").gethostname(), os.getpid()
    workers = [Worker(s, factory, name=f"{host}-{pid}-w{i + 1}", llm=llm, pool=pool) for i in range(max(1, args.count))]
    threads = [threading.Thread(target=w.run_forever, daemon=True) for w in workers]
    for t in threads:
        t.start()
    print(f"{len(workers)} worker(s) running against {s.db_url if not s.is_sqlite else s.data_dir / 'docintel.db'}. Press Ctrl+C to stop.", flush=True)
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping; running jobs go back to the queue and resume from their last stored page.", flush=True)
        for w in workers:
            w.stop.set()
        for t in threads:
            t.join(30)
    pool.close_for_real(wait=False)
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    """Run the pipeline on files without a database and print what it finds."""
    from .extraction.llm import LLMExtractor
    from .jobs.worker import LazyOcrPool
    from .pipeline.process import process_file
    from .pipeline.types import DocIntelError

    s = _settings(args)
    setup_logging("WARNING")
    llm = LLMExtractor(s) if s.llm_enabled else None
    pool = LazyOcrPool(s)
    out, code = [], 0
    try:
        for f in args.files:
            t0 = time.perf_counter()
            try:
                r = process_file(f, s, llm=llm, pool=pool)
                out.append({
                    "file": f, "status": "ok", "type": r.file_type, "source": r.kind, "pages": len(r.pages), "seconds": round(time.perf_counter() - t0, 3), "warnings": r.warnings,
                    "records": [
                        {**rec.to_json(), "issues": [{"code": i.code, "severity": i.severity, "field": i.field, "message": i.message} for i in iss],
                         "needs_review": any(i.severity in ("error", "warning") for i in iss)}
                        for rec, iss in zip(r.records, r.issues)
                    ],
                })
            except DocIntelError as exc:
                code = 1
                out.append({"file": f, "status": "failed", "code": exc.code, "message": str(exc), "seconds": round(time.perf_counter() - t0, 3)})
    finally:
        pool.close(wait=False)
    print(json.dumps(out if len(out) != 1 else out[0], indent=2, ensure_ascii=False, default=str))
    return code


def cmd_seed(args: argparse.Namespace) -> int:
    """Load every supported file in a folder through the same path as an upload, and process them here."""
    from .api.uploads import ingest_path
    from .db.engine import init_db, make_engine, make_session_factory
    from .jobs.worker import Worker
    from .pipeline.detect import SUPPORTED_EXTENSIONS

    s = _settings(args)
    setup_logging("WARNING")
    s.ensure_dirs()
    engine = make_engine(s.db_url)
    init_db(engine)
    factory = make_session_factory(engine)
    files = sorted(p for p in Path(args.folder).rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        print(f"No supported files in {args.folder}")
        return 1
    batch = {"queued": 0, "duplicate": 0, "rejected": 0}
    for p in files:
        r = ingest_path(s, factory, p, force=args.force)
        batch[r["status"]] += 1
        if r["status"] == "rejected":
            print(f"  rejected {p.name}: {r['message']}")
    print(f"Queued {batch['queued']}, skipped {batch['duplicate']} duplicates, rejected {batch['rejected']}. Processing...", flush=True)
    t0 = time.perf_counter()
    w = Worker(s, factory, name="seed-worker")
    n = w.drain()
    w.pool.close(wait=False)
    w.forget()
    print(f"Processed {n} document(s) in {time.perf_counter() - t0:.1f} s. Start the app with: docintel serve")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    """Rebuild the search vectors (after changing DOCINTEL_EMBEDDING_PROVIDER, or if a document was indexed while the embedding service was down)."""
    from sqlalchemy import select

    from .db.engine import init_db, make_engine, make_session_factory, session_scope
    from .db.models import Document
    from .search.index import index_document

    s = _settings(args)
    setup_logging("WARNING")
    engine = make_engine(s.db_url)
    init_db(engine)
    factory = make_session_factory(engine)
    with session_scope(factory) as sess:
        ids = [i for (i,) in sess.execute(select(Document.id).where(Document.status == "completed")).all()]
    done = 0
    for i in ids:
        with session_scope(factory) as sess:
            done += 1 if index_document(sess, i, s) else 0
    print(f"Reindexed {done} of {len(ids)} document(s) with the '{s.embedding_provider}' embedder.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    s = _settings(args)
    ok = True

    def line(name: str, good: bool, detail: str, required: bool = True) -> None:
        nonlocal ok
        mark = "ok  " if good else ("FAIL" if required else "warn")
        if required and not good:
            ok = False
        print(f"[{mark}] {name}: {detail}")

    line("python", sys.version_info >= (3, 11), sys.version.split()[0])
    tess = shutil.which(s.tesseract_cmd)
    line("tesseract", bool(tess), tess or "not found; OCR falls back to RapidOCR (slower, less accurate on small text)", required=False)
    try:
        import rapidocr_onnxruntime  # noqa: F401

        line("rapidocr", True, "installed")
    except Exception as exc:  # noqa: BLE001
        line("rapidocr", False, f"not usable ({exc})", required=not tess)
    try:
        s.ensure_dirs()
        probe = s.data_dir / ".write-test"
        probe.write_text("x")
        probe.unlink()
        line("data folder", True, str(s.data_dir.resolve()))
    except OSError as exc:
        line("data folder", False, str(exc))
    try:
        from sqlalchemy import text

        from .db.engine import init_db, make_engine

        eng = make_engine(s.db_url)
        init_db(eng)
        with eng.connect() as c:
            fts = c.execute(text("SELECT sqlite_compileoption_used('ENABLE_FTS5')")).scalar() if s.is_sqlite else None
        line("database", True, f"{'sqlite' if s.is_sqlite else s.db_url.split(':', 1)[0]}" + (", FTS5 available" if fts else ""))
        if s.is_sqlite and not fts:
            line("fts5", False, "this SQLite build has no FTS5, keyword search will not work")
    except Exception as exc:  # noqa: BLE001
        line("database", False, str(exc))
    provider = s.resolved_llm_provider
    line("ai extraction", True, f"{provider} (mode {s.extraction_mode})" if s.llm_enabled else "off; the offline rules extractor is used. Set GEMINI_API_KEY to enable the AI path.", required=False)
    line("embeddings", True, f"{s.embedding_provider}" + (" (character n-gram hashing: fuzzy keyword match, not true semantic search)" if s.embedding_provider == "hash" else ""), required=False)
    line("api key", True, "required for /api" if s.api_key else "not set; anyone who can reach the port can use the API", required=False)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="docintel", description="Document intelligence for invoices, receipts and spreadsheets.")
    p.add_argument("--version", action="version", version=f"docintel {__version__}")
    p.add_argument("--data-dir", help="where the database, uploads and logs live (default ./data)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="start the API and web UI")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--workers", type=int, help="background workers inside this process (0 to run them separately)")
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("worker", help="run background workers as their own process (set DOCINTEL_WORKERS=0 on the API)")
    sp.add_argument("--count", type=int, default=1)
    sp.set_defaults(fn=cmd_worker)

    sp = sub.add_parser("process", help="read files and print the extracted data as JSON (no database)")
    sp.add_argument("files", nargs="+")
    sp.set_defaults(fn=cmd_process)

    sp = sub.add_parser("seed", help="load a folder of files and process them")
    sp.add_argument("folder")
    sp.add_argument("--force", action="store_true", help="store files even if an identical one exists")
    sp.set_defaults(fn=cmd_seed)

    sp = sub.add_parser("reindex", help="rebuild search vectors")
    sp.set_defaults(fn=cmd_reindex)

    sp = sub.add_parser("doctor", help="check the installation")
    sp.set_defaults(fn=cmd_doctor)

    args = p.parse_args(argv)
    if args.data_dir:
        os.environ["DOCINTEL_DATA_DIR"] = args.data_dir
        get_settings.cache_clear()
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
