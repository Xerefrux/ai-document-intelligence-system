import { api } from "../api";
import { Empty, LoadError, PageHead } from "../components/ui";
import { ISSUE_TITLES, fmtPercent, fmtSeconds, plural } from "../format";
import { useLoad } from "../hooks";
import type { Config, Stats } from "../types";

export default function OverviewPage() {
  const stats = useLoad((s) => api.get<Stats>("/stats", undefined, s), [], () => 4000);
  const cfg = useLoad((s) => api.get<Config>("/config", undefined, s), []);
  if (stats.error && !stats.data) return <LoadError error={stats.error} retry={stats.reload} />;
  const s = stats.data;
  if (!s) return <p className="quiet" role="status">Loading…</p>;
  const c = cfg.data;
  const max = Math.max(1, ...s.issues.top_codes.map((x) => x.count));
  const finished = s.documents.by_status.completed + s.documents.by_status.failed;

  return (
    <>
      <PageHead title="Overview" />
      {s.documents.total === 0 ? <Empty title="Nothing has been processed yet">Upload some documents and the figures here start to fill in.</Empty> : (
        <>
          <dl className="figures">
            <div><dt>Documents read without failing</dt><dd>{fmtPercent(s.documents.success_rate, 1) || "none yet"}</dd><dd className="fig-sub">{plural(s.documents.by_status.completed, "document")} of {finished.toLocaleString()} finished</dd></div>
            <div><dt>Typical time per document</dt><dd>{fmtSeconds(s.processing.median_seconds)}</dd><dd className="fig-sub">half are faster; slowest 1 in 20 take {fmtSeconds(s.processing.p95_seconds)} or more</dd></div>
            <div><dt>Pages read in the last hour</dt><dd>{s.processing.pages_last_hour.toLocaleString()}</dd><dd className="fig-sub">{plural(s.processing.documents_last_hour, "document")}</dd></div>
            <div><dt>Records waiting for a person</dt><dd>{s.records.by_review.needs_review.toLocaleString()}</dd><dd className="fig-sub">of {s.records.total.toLocaleString()} records, {s.records.by_review.approved.toLocaleString()} approved</dd></div>
          </dl>

          <div className="two-col">
            <section aria-label="Problems found">
              <h2 className="sub-head">Problems the checks found</h2>
              {s.issues.top_codes.length === 0 ? <p className="quiet">None so far.</p> : (
                <ul className="bars">
                  {s.issues.top_codes.map((x) => (
                    <li key={x.code}>
                      <span className="bars-name">{ISSUE_TITLES[x.code] ?? x.code}</span>
                      <span className="bars-track"><span className="bars-fill" style={{ width: `${(x.count / max) * 100}%` }} /></span>
                      <span className="bars-n">{x.count.toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section aria-label="Work queue">
              <h2 className="sub-head">Work queue</h2>
              <dl className="kv">
                <div><dt>Waiting</dt><dd>{s.queue.queued}</dd></div>
                <div><dt>Running</dt><dd>{s.queue.running}</dd></div>
                <div><dt>Failed</dt><dd>{s.documents.by_status.failed}</dd></div>
                <div><dt>Corrections made by people</dt><dd>{s.corrections}</dd></div>
                <div><dt>Background workers</dt><dd>{s.workers.filter((w) => w.alive).length} running</dd></div>
              </dl>
            </section>
          </div>
        </>
      )}

      {c && (
        <section className="block" aria-label="How this server is set up">
          <h2 className="sub-head">How this server is set up</h2>
          <dl className="kv">
            <div><dt>Reading scanned pages</dt><dd>{c.tesseract_available ? "Tesseract OCR" : c.ocr_engine === "rapidocr" ? "RapidOCR" : "RapidOCR (Tesseract not found)"}</dd></div>
            <div><dt>AI reader</dt><dd>{c.llm_enabled ? `On (${c.llm_provider}), mode ${c.extraction_mode}` : "Off. The offline rules reader is used."}</dd></div>
            <div><dt>Search by meaning</dt><dd>{c.embedding_provider === "hash" ? "Word fragments, fuzzy only" : c.embedding_provider === "none" ? "Off" : `Embeddings from ${c.embedding_provider}`}</dd></div>
            <div><dt>Database</dt><dd>{c.database}</dd></div>
            <div><dt>Upload limit</dt><dd>{c.max_upload_mb} MB per file, {c.max_files_per_request} files per request</dd></div>
            <div><dt>Amounts may differ by</dt><dd>{c.amount_tolerance.toFixed(2)} before a total is flagged</dd></div>
            <div><dt>Access</dt><dd>{c.auth_required ? "API key required" : "Open to anyone who can reach this address"}</dd></div>
          </dl>
        </section>
      )}
    </>
  );
}
