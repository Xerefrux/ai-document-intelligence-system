import { useState } from "react";
import { api } from "../api";
import { Amount, Empty, Icon, LoadError, Notice, PageHead, useToast } from "../components/ui";
import { fmtAmount, fmtDate, fmtAgo, FIELD_LABELS } from "../format";
import { useLoad } from "../hooks";
import type { PairStatus, Reconciliation } from "../types";

const LABEL: Record<PairStatus, string> = {
  MATCH: "Match",
  MISMATCH: "Differ",
  MISSING_RIGHT: "Only in the invoices",
  MISSING_LEFT: "Only in the spreadsheet",
};
const ORDER: PairStatus[] = ["MISMATCH", "MISSING_RIGHT", "MISSING_LEFT", "MATCH"];

export default function ComparePage() {
  const toast = useToast();
  const [result, setResult] = useState<Reconciliation>();
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<PairStatus | "">("");
  const past = useLoad((s) => api.get<{ items: { id: string; created_at: string; summary: Reconciliation["summary"] }[] }>("/reconciliations", { limit: 6 }, s), [result?.id]);

  const run = async () => {
    setBusy(true);
    try {
      const r = await api.post<Reconciliation>("/reconciliations", { mode: "auto" });
      setResult(r);
      setFilter("");
      toast("Comparison finished.", "good");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(false);
    }
  };
  const open = async (id: string) => {
    try { setResult(await api.get<Reconciliation>(`/reconciliations/${id}`)); setFilter(""); } catch (e) { toast((e as Error).message, "bad"); }
  };
  const items = (result?.items ?? []).filter((i) => !filter || i.status === filter).sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));

  return (
    <>
      <PageHead title="Compare with a spreadsheet">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void run()}>{busy ? "Comparing…" : "Compare now"}</button>
      </PageHead>
      <p className="lede">Every invoice read from a PDF or image is matched by invoice number against the rows read from your spreadsheets (ledgers, exports). You see which agree, which differ and by how much, and which exist on only one side. Differences are also added to the record as a warning.</p>

      {past.error && <LoadError error={past.error} retry={past.reload} />}
      {!result && (past.data?.items.length ?? 0) > 0 && (
        <p className="meta-line">Earlier comparisons: {past.data!.items.map((p) => <button key={p.id} type="button" className="linklike" onClick={() => void open(p.id)}>{fmtAgo(p.created_at)} ({p.summary.total} pairs)</button>)}</p>
      )}

      {!result ? <Empty title="No comparison yet">Upload your invoices and a spreadsheet first, then press Compare now.</Empty> : (
        <>
          <div className="chips" role="group" aria-label="Filter results">
            <button type="button" className={filter === "" ? "chip chip-on" : "chip"} aria-pressed={filter === ""} onClick={() => setFilter("")}>All<span className="chip-n">{result.summary.total}</span></button>
            {ORDER.map((s) => <button key={s} type="button" className={filter === s ? "chip chip-on" : "chip"} aria-pressed={filter === s} onClick={() => setFilter(s)}>{LABEL[s]}<span className="chip-n">{result.summary[s] ?? 0}</span></button>)}
          </div>
          {result.summary.total === 0 ? <Notice kind="info">There was nothing to compare. A comparison needs at least one invoice and one spreadsheet row.</Notice> : (
            <table className="ledger">
              <thead><tr><th scope="col">Invoice number</th><th scope="col">Result</th><th scope="col">Invoice document</th><th scope="col">Spreadsheet row</th><th scope="col">Differences</th></tr></thead>
              <tbody>
                {items.map((i, n) => (
                  <tr key={`${i.key}-${n}`}>
                    <td className="name-cell">{i.key ?? "no number"}</td>
                    <td><span className={`mark ${i.status === "MATCH" ? "mark-good" : i.status === "MISMATCH" ? "mark-bad" : "mark-muted"}`}>{LABEL[i.status]}</span></td>
                    <td>{i.left ? <><a href={`#/records/${i.left.id}`}>{i.left.supplier_name ?? i.left.filename}</a><div className="sub-cell"><Amount value={i.left.total} currency={i.left.currency} />, {fmtDate(i.left.invoice_date)}</div></> : <span className="quiet">none</span>}</td>
                    <td>{i.right ? <><a href={`#/records/${i.right.id}`}>{i.right.supplier_name ?? i.right.filename}</a><div className="sub-cell"><Amount value={i.right.total} currency={i.right.currency} />, {fmtDate(i.right.invoice_date)}</div></> : <span className="quiet">none</span>}</td>
                    <td>
                      {i.diffs.length === 0 ? <span className="quiet"></span> : (
                        <ul className="why">
                          {i.diffs.map((d, k) => (
                            <li key={k}><Icon name="alert" size={14} /><span>{FIELD_LABELS[d.field] ?? d.field}: {typeof d.left === "number" ? fmtAmount(d.left) : String(d.left)} against {typeof d.right === "number" ? fmtAmount(d.right) : String(d.right)}{d.difference != null ? ` (off by ${fmtAmount(Math.abs(d.difference))})` : ""}</span></li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
