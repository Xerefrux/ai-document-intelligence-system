import { useState } from "react";
import { ApiError, api, downloadFile } from "../api";
import { Empty, Highlight, Icon, IssueCountsMark, LoadError, Notice, PageHead, ProgressBar, RecStatusMark, SeverityMark, Tabs, Amount, DocStatusMark, useToast } from "../components/ui";
import { FAILURE_HELP, ISSUE_TITLES, SOURCE_LABELS, STAGE_LABELS, fmtBytes, fmtDate, fmtDateTime, fmtDuration, fmtPercent, plural } from "../format";
import { navigate, remember, useLoad } from "../hooks";
import type { DocDetail, TextWindow } from "../types";

export default function DocumentPage({ id }: { id: string }) {
  const toast = useToast();
  const [tab, setTab] = useState("records");
  const [busy, setBusy] = useState(false);
  const doc = useLoad((s) => api.get<DocDetail>(`/documents/${id}`, undefined, s), [id], (d) => (d && (d.status === "processing" || d.status === "queued") ? 1200 : null));

  if (doc.error && !doc.data) {
    const gone = doc.error instanceof ApiError && doc.error.status === 404;
    return gone ? <Empty title="This document does not exist" action={<a className="btn btn-quiet" href="#/documents">Back to documents</a>}>It may have been deleted.</Empty> : <LoadError error={doc.error} retry={doc.reload} />;
  }
  const d = doc.data;
  if (!d) return <p className="quiet" role="status">Loading…</p>;

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast(ok, "good");
      doc.reload();
    } catch (e) {
      const err = e as ApiError;
      toast(err.message, "bad");
    } finally {
      setBusy(false);
    }
  };

  const reprocess = async (full: boolean) => {
    let confirm = false;
    if (d.corrections > 0) {
      if (!window.confirm(`This document has ${plural(d.corrections, "manual correction")}. Processing it again replaces its records and discards them. Continue?`)) return;
      confirm = true;
    }
    await run(() => api.post(`/documents/${id}/reprocess`, { full, confirm }), full ? "Reading the whole file again." : "Queued to process again.");
  };
  const remove = async () => {
    if (!window.confirm(`Delete ${d.filename}? Its records, text and stored file are removed. This cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.del(`/documents/${id}`);
      toast("Document deleted.", "good");
      navigate("/documents");
    } catch (e) {
      toast((e as Error).message, "bad");
      setBusy(false);
    }
  };

  const active = d.status === "queued" || d.status === "processing";
  const reading = d.stage === "reading" && d.page_count;
  const help = d.error_code ? FAILURE_HELP[d.error_code] : undefined;

  return (
    <>
      <PageHead title={d.filename} back={{ to: "/documents", label: "Documents" }}>
        <button type="button" className="btn btn-quiet" onClick={() => void downloadFile(`/documents/${id}/file`, d.filename)}><Icon name="download" size={15} />Download original</button>
        {active ? (
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void run(() => api.post(`/documents/${id}/cancel`), "Cancelled.")}><Icon name="stop" size={15} />Cancel</button>
        ) : (
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void reprocess(false)}><Icon name="refresh" size={15} />Process again</button>
        )}
        <button type="button" className="btn btn-danger" disabled={busy || d.status === "processing"} onClick={() => void remove()}><Icon name="trash" size={15} />Delete</button>
      </PageHead>

      <dl className="facts">
        <div><dt>State</dt><dd><DocStatusMark doc={d} /></dd></div>
        <div><dt>File</dt><dd>{d.file_type.toUpperCase()}, {fmtBytes(d.size_bytes)}</dd></div>
        <div><dt>Pages</dt><dd>{d.page_count ?? "not read yet"}</dd></div>
        <div><dt>How it was read</dt><dd>{d.source_kind ? SOURCE_LABELS[d.source_kind] ?? d.source_kind : "not yet"}{d.ocr_used && d.ocr_confidence != null ? `, ${fmtPercent(d.ocr_confidence)} OCR confidence` : ""}</dd></div>
        <div><dt>Added</dt><dd>{fmtDateTime(d.created_at)}</dd></div>
        <div><dt>Took</dt><dd>{d.processing_ms ? fmtDuration(d.processing_ms) : "not finished"}</dd></div>
      </dl>

      {active && (
        <div className="block">
          <p className="meta-line"><span>{STAGE_LABELS[d.stage ?? ""] ?? d.stage}</span>{reading ? <span>page {Math.min(d.pages_done + 1, d.page_count ?? 0)} of {d.page_count}</span> : null}{d.job && d.job.attempts > 1 ? <span>attempt {d.job.attempts} of {d.job.max_attempts}</span> : null}</p>
          <ProgressBar value={reading ? d.progress : null} label="Processing progress" />
        </div>
      )}

      {d.status === "failed" && (
        <Notice kind="bad" title={`Could not read this file (${d.error_code})`}
          action={<div className="notice-actions"><button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void reprocess(false)}>Retry</button><button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void reprocess(true)}>Retry from the first page</button></div>}>
          {" "}{d.error_message} {help}
        </Notice>
      )}
      {d.duplicate_of && <Notice kind="warn" title="This looks like a copy.">One of its invoices matches a document that was stored earlier. See the issues below.</Notice>}

      {d.status !== "queued" && d.status !== "processing" && (
        <>
          <Tabs label="Document sections" active={tab} onChange={setTab} tabs={[
            { id: "records", label: "Records", count: d.records_total },
            { id: "issues", label: "Issues", count: d.issues.filter((i) => i.severity !== "info").length },
            { id: "text", label: "Extracted text" },
            { id: "history", label: "History", count: d.events.length },
          ]} />
          <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
            {tab === "records" && <Records d={d} reload={doc.reload} />}
            {tab === "issues" && <Issues d={d} />}
            {tab === "text" && <TextTab d={d} />}
            {tab === "history" && <History d={d} />}
          </div>
        </>
      )}
    </>
  );
}

function Records({ d, reload }: { d: DocDetail; reload: () => void }) {
  const toast = useToast();
  const open = (id: string) => { remember("docintel.queue", d.records.map((r) => r.id)); navigate(`/records/${id}`); };
  if (d.records.length === 0) {
    return (
      <Empty title="No invoice was found in this document"
        action={d.status === "completed" ? <button type="button" className="btn btn-primary" onClick={async () => {
          try { const r = await api.post<{ id: string }>(`/documents/${d.id}/records`); navigate(`/records/${r.id}`); } catch (e) { toast((e as Error).message, "bad"); reload(); }
        }}><Icon name="plus" size={15} />Add a record by hand</button> : undefined}>
        It may be a letter, a blank page or an unreadable scan. Open the extracted text to see what was read. If it is an invoice, add the record by hand and fill it in next to the page.
      </Empty>
    );
  }
  return (
    <>
      <table className="ledger">
        <thead><tr><th scope="col">Invoice number</th><th scope="col">Supplier</th><th scope="col">Date</th><th scope="col" className="num">Total</th><th scope="col">Where</th><th scope="col">State</th><th scope="col">Issues</th></tr></thead>
        <tbody>
          {d.records.map((r) => {
            const n = d.issues.filter((i) => i.record_id === r.id);
            return (
              <tr key={r.id}>
                <td><a href={`#/records/${r.id}`} className="name-cell" onClick={() => remember("docintel.queue", d.records.map((x) => x.id))}>{r.invoice_number ?? <span className="quiet">no number</span>}</a></td>
                <td>{r.supplier_name ?? <span className="quiet">not found</span>}</td>
                <td>{fmtDate(r.invoice_date)}</td>
                <td className="num"><Amount value={r.total} currency={r.currency} /></td>
                <td className="quiet">{r.sheet_name ? `${r.sheet_name}${r.row_ref ? `, ${r.row_ref}` : ""}` : r.page_start ? (r.page_end && r.page_end !== r.page_start ? `pages ${r.page_start} to ${r.page_end}` : `page ${r.page_start}`) : ""}</td>
                <td><RecStatusMark status={r.review_status} /></td>
                <td><IssueCountsMark counts={{ error: n.filter((i) => i.severity === "error").length, warning: n.filter((i) => i.severity === "warning").length }} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {d.records_total > d.records.length && (
        <p className="quiet">Showing the first {d.records.length} of {d.records_total.toLocaleString()} records. <a href={`#/search?doc=${d.id}`} onClick={(e) => { e.preventDefault(); open(d.records[0].id); }}>Open the first one</a> or search within this document.</p>
      )}
    </>
  );
}

function Issues({ d }: { d: DocDetail }) {
  if (d.issues.length === 0) return <Empty title="No issues">Every check passed for this document.</Empty>;
  const name = (rid: string | null) => d.records.find((r) => r.id === rid)?.invoice_number ?? null;
  return (
    <ul className="issue-list">
      {d.issues.map((i) => (
        <li key={i.id} className={`issue issue-${i.severity}`}>
          <SeverityMark severity={i.severity} />
          <div>
            <p className="issue-title">{ISSUE_TITLES[i.code] ?? i.code}{i.record_id && <> in <a href={`#/records/${i.record_id}`}>{name(i.record_id) ?? "a record"}</a></>}</p>
            <p className="issue-msg">{i.message}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function TextTab({ d }: { d: DocDetail }) {
  const [page, setPage] = useState(1);
  const total = d.page_count ?? d.pages.length;
  const win = useLoad((s) => api.get<TextWindow>(`/documents/${d.id}/text`, { from: page, to: page }, s), [d.id, page]);
  const p = win.data?.pages[0];
  const meta = d.pages.find((x) => x.page_no === page);
  return (
    <div className="text-tab">
      <div className="toolbar">
        <div className="pager-inline">
          <button type="button" className="btn btn-quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <label>Page <input type="number" min={1} max={Math.max(1, total)} value={page} onChange={(e) => setPage(Math.min(Math.max(1, +e.target.value || 1), Math.max(1, total)))} className="page-input" /> of {total || 1}</label>
          <button type="button" className="btn btn-quiet" disabled={page >= total} onClick={() => setPage(page + 1)}>Next</button>
        </div>
        {meta && <span className="quiet">{meta.source === "ocr" ? `read with OCR${meta.ocr_confidence != null ? `, ${fmtPercent(meta.ocr_confidence)} confidence` : ""}` : meta.source === "failed" ? "could not be read" : meta.label ? `sheet ${meta.label}` : "text layer"}</span>}
      </div>
      {win.error ? <LoadError error={win.error} retry={win.reload} /> : !p ? <p className="quiet">Loading…</p> : (
        <>
          {p.warnings.length > 0 && <Notice kind="warn">{p.warnings.join(" ")}</Notice>}
          {p.text.trim() ? <pre className="rawtext"><Highlight text={p.text} needle={null} /></pre> : <Empty title="No text on this page">The page is blank, or nothing could be read from it.</Empty>}
        </>
      )}
    </div>
  );
}

function History({ d }: { d: DocDetail }) {
  return (
    <ol className="timeline">
      {d.events.map((e) => (
        <li key={e.id} className={`tl tl-${e.level}`}>
          <time dateTime={e.created_at}>{fmtDateTime(e.created_at)}</time>
          <span>{e.message}</span>
          {e.duration_ms != null && <span className="quiet"> ({fmtDuration(e.duration_ms)})</span>}
        </li>
      ))}
    </ol>
  );
}
