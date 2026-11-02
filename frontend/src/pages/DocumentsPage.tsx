import { useState } from "react";
import { api } from "../api";
import { Empty, IssueCountsMark, LoadError, PageHead, DocStatusMark, ProgressBar } from "../components/ui";
import { fmtAgo, fmtBytes, fmtDuration, plural, STAGE_LABELS } from "../format";
import { replaceQuery, useDebounced, useLoad, type Route } from "../hooks";
import type { DocList, DocStatus } from "../types";

const PAGE = 50;
const FILTERS: { id: "" | DocStatus | "needs_review"; label: string }[] = [
  { id: "", label: "All" },
  { id: "needs_review", label: "Needs review" },
  { id: "processing", label: "Processing" },
  { id: "queued", label: "Waiting" },
  { id: "completed", label: "Finished" },
  { id: "failed", label: "Failed" },
];
const TYPE_NAME: Record<string, string> = { pdf: "PDF", image: "Image", xlsx: "Excel", xls: "Excel", csv: "CSV" };

export default function DocumentsPage({ route }: { route: Route }) {
  const [status, setStatus] = useState(route.query.get("status") ?? "");
  const [q, setQ] = useState(route.query.get("q") ?? "");
  const [type, setType] = useState(route.query.get("type") ?? "");
  const [offset, setOffset] = useState(0);
  const dq = useDebounced(q, 250);

  const list = useLoad(
    (signal) => api.get<DocList>("/documents", {
      status: status && status !== "needs_review" ? status : undefined,
      review_status: status === "needs_review" ? "needs_review" : undefined,
      file_type: type || undefined,
      q: dq || undefined,
      limit: PAGE,
      offset,
    }, signal),
    [status, dq, type, offset],
    (d) => (d && d.items.some((x) => x.status === "processing" || x.status === "queued") ? 2000 : 8000),
  );

  const change = (fn: () => void) => { fn(); setOffset(0); };
  const sync = (s: string, t: string, text: string) => replaceQuery("/documents", { status: s, type: t, q: text });
  const d = list.data;
  const counts = d?.status_counts;

  return (
    <>
      <PageHead title="Documents" />
      <div className="toolbar">
        <div className="chips" role="group" aria-label="Filter by state">
          {FILTERS.map((f) => {
            const n = !counts ? undefined : f.id === "" ? Object.values(counts).reduce((a, b) => a + b, 0) : f.id === "needs_review" ? d?.needs_review : counts[f.id as DocStatus];
            return (
              <button key={f.id} type="button" className={status === f.id ? "chip chip-on" : "chip"} aria-pressed={status === f.id}
                onClick={() => change(() => { setStatus(f.id); sync(f.id, type, q); })}>
                {f.label}{n !== undefined && <span className="chip-n">{n.toLocaleString()}</span>}
              </button>
            );
          })}
        </div>
        <div className="toolbar-right">
          <label className="field-inline">
            <span className="sr-only">File type</span>
            <select value={type} onChange={(e) => change(() => { setType(e.target.value); sync(status, e.target.value, q); })}>
              <option value="">All types</option>
              <option value="pdf">PDF</option>
              <option value="image">Images</option>
              <option value="xlsx">Excel (xlsx)</option>
              <option value="xls">Excel (xls)</option>
              <option value="csv">CSV</option>
            </select>
          </label>
          <label className="field-inline">
            <span className="sr-only">Search file names</span>
            <input type="search" placeholder="Find by file name" value={q} onChange={(e) => change(() => { setQ(e.target.value); sync(status, type, e.target.value); })} />
          </label>
        </div>
      </div>

      {list.error && !d ? <LoadError error={list.error} retry={list.reload} /> : !d ? <p className="quiet" role="status">Loading…</p> : d.items.length === 0 ? (
        <Empty title={status || dq || type ? "No documents match these filters" : "No documents yet"}>
          {status || dq || type ? "Clear a filter to see more." : <>Go to <a href="#/">Upload</a> to add some.</>}
        </Empty>
      ) : (
        <>
          <table className="ledger">
            <thead>
              <tr>
                <th scope="col">Document</th><th scope="col">State</th><th scope="col" className="num">Pages</th><th scope="col" className="num">Records</th>
                <th scope="col">Issues</th><th scope="col">Added</th><th scope="col" className="num">Took</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((x) => (
                <tr key={x.id}>
                  <td>
                    <a href={`#/documents/${x.id}`} className="name-cell">{x.filename}</a>
                    <div className="sub-cell">{TYPE_NAME[x.file_type]}, {fmtBytes(x.size_bytes)}</div>
                  </td>
                  <td>
                    <DocStatusMark doc={x} />
                    {x.status === "processing" && (
                      <div className="mini-progress">
                        <ProgressBar value={x.progress} label={`Progress of ${x.filename}`} />
                        <span className="quiet">{STAGE_LABELS[x.stage ?? ""] ?? x.stage}</span>
                      </div>
                    )}
                    {x.status === "failed" && <div className="sub-cell">{x.error_code}</div>}
                  </td>
                  <td className="num">{x.page_count ?? ""}</td>
                  <td className="num">{x.status === "completed" ? x.record_count : ""}</td>
                  <td><IssueCountsMark counts={x.issues} /></td>
                  <td className="quiet">{fmtAgo(x.created_at)}</td>
                  <td className="num quiet">{fmtDuration(x.processing_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <span className="quiet">{offset + 1} to {Math.min(offset + PAGE, d.total)} of {plural(d.total, "document")}</span>
            <button type="button" className="btn btn-quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <button type="button" className="btn btn-quiet" disabled={offset + PAGE >= d.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        </>
      )}
    </>
  );
}
