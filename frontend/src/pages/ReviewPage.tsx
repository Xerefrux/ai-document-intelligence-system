import { useState } from "react";
import { api } from "../api";
import { Amount, DocStatusMark, Empty, LoadError, PageHead, SeverityMark } from "../components/ui";
import { ISSUE_TITLES, fmtAgo, fmtDate, plural } from "../format";
import { navigate, remember, useLoad } from "../hooks";
import type { DocList, IssueList, RecordRow } from "../types";

interface Queue { items: RecordRow[]; total: number }

export default function ReviewPage() {
  const [code, setCode] = useState("");
  const issues = useLoad((s) => api.get<IssueList>("/issues", { limit: 1 }, s), [], () => 10000);
  const recs = useLoad((s) => api.get<Queue>("/records", { review_status: "needs_review", issue_code: code || undefined, limit: 100 }, s), [code], () => 10000);
  const docs = useLoad(async (s) => {
    const [failed, flagged] = await Promise.all([
      api.get<DocList>("/documents", { status: "failed", limit: 30 }, s),
      api.get<DocList>("/documents", { status: "completed", review_status: "needs_review", limit: 100 }, s),
    ]);
    return { failed: failed.items, empty: flagged.items.filter((d) => d.record_count === 0) };
  }, [], () => 10000);

  const start = (id: string) => {
    remember("docintel.queue", (recs.data?.items ?? []).map((r) => r.id));
    remember("docintel.queueBack", "/review");
    navigate(`/records/${id}`);
  };
  const codes = Object.entries(issues.data?.by_code ?? {}).filter(([c]) => c !== "NO_INVOICE_DETECTED");
  const items = recs.data?.items ?? [];
  const attention = [...(docs.data?.failed ?? []), ...(docs.data?.empty ?? [])];

  return (
    <>
      <PageHead title="To review">
        {items.length > 0 && <button type="button" className="btn btn-primary" onClick={() => start(items[0].id)}>Start with the first one</button>}
      </PageHead>

      {recs.error && !recs.data ? <LoadError error={recs.error} retry={recs.reload} /> : (
        <section aria-label="Records to check">
          <div className="chips" role="group" aria-label="Filter by problem">
            <button type="button" className={code === "" ? "chip chip-on" : "chip"} aria-pressed={code === ""} onClick={() => setCode("")}>All problems{recs.data && !code ? <span className="chip-n">{recs.data.total}</span> : null}</button>
            {codes.map(([c, n]) => (
              <button key={c} type="button" className={code === c ? "chip chip-on" : "chip"} aria-pressed={code === c} onClick={() => setCode(c)}>{ISSUE_TITLES[c] ?? c}<span className="chip-n">{n}</span></button>
            ))}
          </div>
          {!recs.data ? <p className="quiet" role="status">Loading…</p> : items.length === 0 ? (
            <Empty title={code ? "Nothing left with this problem" : "Nothing needs review"}>{code ? "Pick another problem above." : "Every record has passed its checks or been approved."}</Empty>
          ) : (
            <table className="ledger">
              <thead><tr><th scope="col">Invoice number</th><th scope="col">Supplier</th><th scope="col">Date</th><th scope="col" className="num">Total</th><th scope="col">What is wrong</th></tr></thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id}>
                    <td><a href={`#/records/${r.id}`} className="name-cell" onClick={() => { remember("docintel.queue", items.map((x) => x.id)); remember("docintel.queueBack", "/review"); }}>{r.invoice_number ?? <span className="quiet">no number</span>}</a><div className="sub-cell">{r.filename}</div></td>
                    <td>{r.supplier_name ?? <span className="quiet">not found</span>}</td>
                    <td>{fmtDate(r.invoice_date)}</td>
                    <td className="num"><Amount value={r.total} currency={r.currency} /></td>
                    <td>
                      <ul className="why">
                        {(r.issues ?? []).map((i, n) => <li key={n}><SeverityMark severity={i.severity} /><span>{i.message}</span></li>)}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {recs.data && recs.data.total > items.length && <p className="quiet">Showing the first {items.length} of {recs.data.total.toLocaleString()}. Approve or fix these and the next ones appear.</p>}
        </section>
      )}

      {attention.length > 0 && (
        <section className="block" aria-label="Documents that need attention">
          <div className="block-head"><h2>{plural(attention.length, "document")} without usable records</h2></div>
          <table className="ledger">
            <thead><tr><th scope="col">Document</th><th scope="col">Problem</th><th scope="col">When</th></tr></thead>
            <tbody>
              {attention.map((d) => (
                <tr key={d.id}>
                  <td><a href={`#/documents/${d.id}`} className="name-cell">{d.filename}</a></td>
                  <td><DocStatusMark doc={d} /> <span className="quiet">{d.status === "failed" ? (d.error_message ?? d.error_code) : "No invoice was found in it."}</span></td>
                  <td className="quiet">{fmtAgo(d.completed_at ?? d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
