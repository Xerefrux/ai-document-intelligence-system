import { useState } from "react";
import { api } from "../api";
import { Amount, Empty, LoadError, PageHead, RecStatusMark, Snippet } from "../components/ui";
import { fmtDate, plural } from "../format";
import { replaceQuery, useDebounced, useLoad, type Route } from "../hooks";
import type { SearchResult } from "../types";

const MODES = [
  { id: "hybrid", label: "Both" },
  { id: "keyword", label: "Exact words" },
  { id: "semantic", label: "Similar meaning" },
];

export default function SearchPage({ route }: { route: Route }) {
  const g = (k: string) => route.query.get(k) ?? "";
  const [q, setQ] = useState(g("q"));
  const [mode, setMode] = useState(g("mode") || "hybrid");
  const [f, setF] = useState({ supplier: g("supplier"), date_from: g("from"), date_to: g("to"), min_total: g("min"), max_total: g("max"), review_status: g("status"), file_type: g("type"), has_issues: g("issues") });
  const [open, setOpen] = useState(Object.values(f).some(Boolean));
  const dq = useDebounced(q, 300);
  const df = useDebounced(f, 300);

  const res = useLoad((signal) => {
    replaceQuery("/search", { q: dq, mode: mode === "hybrid" ? "" : mode, supplier: df.supplier, from: df.date_from, to: df.date_to, min: df.min_total, max: df.max_total, status: df.review_status, type: df.file_type, issues: df.has_issues });
    return api.get<SearchResult>("/search", { q: dq, mode, ...df, limit: 30 }, signal);
  }, [dq, mode, df]);

  const set = (k: keyof typeof f, v: string) => setF((o) => ({ ...o, [k]: v }));
  const r = res.data;
  const filtered = Object.values(f).some(Boolean);

  return (
    <>
      <PageHead title="Search" />
      <form className="searchbar" role="search" onSubmit={(e) => e.preventDefault()}>
        <label className="sr-only" htmlFor="q">Search invoices and extracted text</label>
        <input id="q" type="search" autoFocus placeholder="Supplier, invoice number, amount, or words from the document" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg" role="group" aria-label="How to search">
          {MODES.map((m) => <button key={m.id} type="button" className={mode === m.id ? "seg-on" : ""} aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>{m.label}</button>)}
        </div>
        <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen(!open)}>Filters{filtered ? " (on)" : ""}</button>
      </form>
      {mode !== "keyword" && r?.semantic_kind === "fuzzy" && (
        <p className="quiet small">Similar meaning is a fuzzy match on word fragments here: it finds typos and word endings, not synonyms. Add an embedding key to make it semantic.</p>
      )}
      {open && (
        <div className="filters">
          <label>Supplier<input value={f.supplier} onChange={(e) => set("supplier", e.target.value)} /></label>
          <label>Date from<input type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} /></label>
          <label>Date to<input type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} /></label>
          <label>Total at least<input inputMode="decimal" value={f.min_total} onChange={(e) => set("min_total", e.target.value)} /></label>
          <label>Total at most<input inputMode="decimal" value={f.max_total} onChange={(e) => set("max_total", e.target.value)} /></label>
          <label>State
            <select value={f.review_status} onChange={(e) => set("review_status", e.target.value)}>
              <option value="">Any</option><option value="needs_review">Needs review</option><option value="ok">No issues</option><option value="corrected">Corrected</option><option value="approved">Approved</option>
            </select>
          </label>
          <label>File type
            <select value={f.file_type} onChange={(e) => set("file_type", e.target.value)}>
              <option value="">Any</option><option value="pdf">PDF</option><option value="image">Image</option><option value="xlsx">Excel (xlsx)</option><option value="xls">Excel (xls)</option><option value="csv">CSV</option>
            </select>
          </label>
          <label>Problems
            <select value={f.has_issues} onChange={(e) => set("has_issues", e.target.value)}>
              <option value="">Either</option><option value="true">Has problems</option><option value="false">Has none</option>
            </select>
          </label>
          <button type="button" className="btn btn-quiet" onClick={() => setF({ supplier: "", date_from: "", date_to: "", min_total: "", max_total: "", review_status: "", file_type: "", has_issues: "" })}>Clear filters</button>
        </div>
      )}

      {res.error && !r ? <LoadError error={res.error} retry={res.reload} /> : !r ? <p className="quiet" role="status">Searching…</p> : (
        <>
          <p className="meta-line" role="status">
            <span>{plural(r.total_records, "invoice")}{dq ? ` for "${dq}"` : ""}</span>
            {r.pages.length > 0 && <span>{plural(r.pages.length, "page")} of document text</span>}
            <span className="quiet">{r.took_ms} ms</span>
          </p>
          {r.note && <p className="notice notice-warn">{r.note}</p>}
          {r.records.length === 0 && r.pages.length === 0 ? (
            <Empty title="No matches">{dq ? "Try fewer words, or a part of the name. Filters can also hide results." : "Nothing is stored yet, or the filters exclude everything."}</Empty>
          ) : (
            <>
              {r.records.length > 0 && (
                <ul className="results">
                  {r.records.map((x) => (
                    <li key={x.id}>
                      <a className="result" href={`#/records/${x.id}`}>
                        <span className="result-top">
                          <span className="result-main">{x.invoice_number ?? "No number"} <span className="quiet">{x.supplier_name}</span></span>
                          <span className="result-side"><Amount value={x.total} currency={x.currency} /><span className="quiet">{fmtDate(x.invoice_date)}</span><RecStatusMark status={x.review_status} /></span>
                        </span>
                        {x.snippet && <span className="result-snip"><Snippet text={x.snippet} /></span>}
                        <span className="result-foot quiet">{x.filename}{x.matched_by.length ? `, matched by ${x.matched_by.map((m) => (m === "keyword" ? "words" : "meaning")).join(" and ")}` : ""}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
              {r.pages.length > 0 && (
                <>
                  <h2 className="sub-head">In the text of documents</h2>
                  <ul className="results">
                    {r.pages.map((p) => (
                      <li key={p.id}>
                        <a className="result" href={`#/documents/${p.document_id}`}>
                          <span className="result-top"><span className="result-main">{p.filename}</span><span className="quiet">{p.label ? `sheet ${p.label}` : `page ${p.page_no}`}</span></span>
                          <span className="result-snip"><Snippet text={p.snippet} /></span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
