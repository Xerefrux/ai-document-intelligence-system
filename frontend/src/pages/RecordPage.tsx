import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { Amount, Confidence, Icon, LoadError, Notice, PageHead, ReviewStamp, SeverityMark, sourceName, useToast } from "../components/ui";
import SourceViewer from "../components/SourceViewer";
import { FIELD_LABELS, ISSUE_TITLES, fmtAmount, fmtDateTime, plural } from "../format";
import { navigate, remember, useLoad } from "../hooks";
import type { FieldValue, Issue, LineItem, RecordDetail } from "../types";

type Draft = Record<string, string>;
interface ItemDraft { description: string; quantity: string; unit_price: string; amount: string }

const GROUPS: { title: string; fields: string[] }[] = [
  { title: "Invoice", fields: ["invoice_number", "invoice_date", "due_date"] },
  { title: "Parties", fields: ["supplier_name", "customer_name"] },
  { title: "Amounts", fields: ["subtotal", "discount", "shipping", "tax", "total", "currency"] },
];
const DATE = new Set(["invoice_date", "due_date"]);
const MONEY = new Set(["subtotal", "tax", "total", "discount", "shipping"]);
const OPTIONAL_MONEY = ["discount", "shipping"];

const show = (v: FieldValue | undefined): string => (v?.value === null || v?.value === undefined ? "" : String(v.value));
const num = (s: string | number | null | undefined): number | null => {
  if (s === null || s === undefined || s === "") return null;
  const n = typeof s === "number" ? s : Number(String(s).replace(/[, ]/g, ""));
  return Number.isFinite(n) ? n : null;
};

function draftFrom(rec: RecordDetail): Draft {
  const d: Draft = {};
  for (const g of GROUPS) for (const f of g.fields) d[f] = show(rec.fields[f]);
  return d;
}
const itemsFrom = (items: LineItem[]): ItemDraft[] => items.map((i) => ({
  description: i.description ?? "", quantity: i.quantity == null ? "" : String(i.quantity), unit_price: i.unit_price == null ? "" : String(i.unit_price), amount: i.amount == null ? "" : String(i.amount),
}));

export default function RecordPage({ id }: { id: string }) {
  const loaded = useLoad((s) => api.get<RecordDetail>(`/records/${id}`, undefined, s), [id]);
  const [fresh, setFresh] = useState<RecordDetail>();
  useEffect(() => setFresh(undefined), [id]);
  const rec = fresh && fresh.id === id ? fresh : loaded.data;

  if (loaded.error && !rec) {
    const gone = loaded.error instanceof ApiError && loaded.error.status === 404;
    return gone ? <p>This record does not exist. It may have been deleted. <a href="#/review">Back to the review list</a></p> : <LoadError error={loaded.error} retry={loaded.reload} />;
  }
  if (!rec) return <p className="quiet" role="status">Loading…</p>;
  return <Editor key={`${rec.id}:${rec.version}`} rec={rec} onSaved={setFresh} reload={loaded.reload} />;
}

function Editor({ rec, onSaved, reload }: { rec: RecordDetail; onSaved: (r: RecordDetail) => void; reload: () => void }) {
  const toast = useToast();
  const initial = useMemo(() => draftFrom(rec), [rec]);
  const initialItems = useMemo(() => itemsFrom(rec.line_items), [rec]);
  const [values, setValues] = useState<Draft>(initial);
  const [items, setItems] = useState<ItemDraft[]>(initialItems);
  const [note, setNote] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [extra, setExtra] = useState(OPTIONAL_MONEY.some((f) => initial[f] !== ""));
  const refs = useRef<Record<string, HTMLInputElement | null>>({});

  const changedFields = Object.keys(values).filter((k) => values[k].trim() !== initial[k].trim());
  const itemsChanged = JSON.stringify(items) !== JSON.stringify(initialItems);
  const dirty = changedFields.length > 0 || itemsChanged;

  const queue = remember<string[]>("docintel.queue") ?? [];
  const at = queue.indexOf(rec.id);
  const next = at >= 0 && at < queue.length - 1 ? queue[at + 1] : null;
  const back = remember<string>("docintel.queueBack") ?? (rec.document ? `/documents/${rec.document.id}` : "/review");

  const issuesFor = (f: string): Issue[] => rec.issues.filter((i) => i.field === f && i.severity !== "info");
  const open = rec.issues.filter((i) => i.severity !== "info");
  const notes = rec.issues.filter((i) => i.severity === "info");

  const save = async () => {
    if (!dirty || busy) return false;
    setBusy(true);
    setErrors({});
    const fields: Record<string, string | null> = {};
    for (const k of changedFields) fields[k] = values[k].trim() === "" ? null : values[k].trim();
    try {
      const r = await api.patch<{ changed: string[]; record: RecordDetail }>(`/records/${rec.id}`, {
        fields: changedFields.length ? fields : undefined,
        line_items: itemsChanged ? items.map((i) => ({ description: i.description || null, quantity: i.quantity || null, unit_price: i.unit_price || null, amount: i.amount || null })) : undefined,
        note: note.trim() || undefined,
        expected_version: rec.version,
      });
      onSaved(r.record);
      const still = r.record.issues.filter((i) => i.severity !== "info").length;
      toast(still ? `Saved. ${plural(still, "check")} still fail${still === 1 ? "s" : ""}.` : "Saved. All checks pass.", still ? "info" : "good");
      return true;
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "VERSION_CONFLICT") setConflict(true);
      else if (err.code === "INVALID_VALUE" && typeof err.details?.field === "string") {
        const f = err.details.field as string;
        setErrors({ [f]: err.message });
        refs.current[f]?.focus();
      } else toast(err.message, "bad");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (dirty && !(await save())) return;
    setBusy(true);
    try {
      await api.post(`/records/${rec.id}/approve`, note.trim() ? { note: note.trim() } : {});
      toast("Approved.", "good");
      if (next) navigate(`/records/${next}`);
      else navigate(back);
    } catch (e) {
      toast((e as Error).message, "bad");
      setBusy(false);
    }
  };
  const recheck = async () => {
    setBusy(true);
    try {
      onSaved(await api.post<RecordDetail>(`/records/${rec.id}/revalidate`));
      toast("Checked again.", "good");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm("Delete this record? The document stays; only this record is removed.")) return;
    setBusy(true);
    try {
      await api.del(`/records/${rec.id}`);
      toast("Record deleted.", "good");
      navigate(rec.document ? `/documents/${rec.document.id}` : "/review");
    } catch (e) {
      toast((e as Error).message, "bad");
      setBusy(false);
    }
  };

  // Ctrl or Cmd + S saves
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); void saveRef.current(); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const on = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", on);
    return () => window.removeEventListener("beforeunload", on);
  }, [dirty]);

  const set = (k: string, v: string) => { setValues((o) => ({ ...o, [k]: v })); if (errors[k]) setErrors(({ [k]: _x, ...r }) => r); };
  const setItem = (i: number, k: keyof ItemDraft, v: string) => setItems((l) => l.map((x, n) => (n === i ? { ...x, [k]: v } : x)));

  // live arithmetic, using the values as they are typed
  const lineSum = items.reduce((s, i) => s + (num(i.amount) ?? 0), 0);
  const hasLines = items.some((i) => num(i.amount) !== null);
  const subtotal = num(values.subtotal);
  const total = num(values.total);
  const base = subtotal ?? (hasLines ? lineSum : null);
  const calc = base === null ? null : base + (num(values.tax) ?? 0) - Math.abs(num(values.discount) ?? 0) + (num(values.shipping) ?? 0);
  const tol = 0.05;
  const totalOk = calc !== null && total !== null ? Math.abs(calc - total) <= tol : null;
  const linesOk = hasLines && subtotal !== null ? Math.abs(lineSum - subtotal) <= Math.max(tol, Math.abs(subtotal) * 0.0005) : null;

  const focusField = (f: string | null) => { if (f) { refs.current[f]?.focus(); refs.current[f]?.scrollIntoView({ block: "center", behavior: "smooth" }); } };
  const ev = focus ? rec.fields[focus] : undefined;

  return (
    <>
      <PageHead title={rec.invoice_number ? `Invoice ${rec.invoice_number}` : "Record without an invoice number"} back={{ to: back, label: back.startsWith("/review") ? "To review" : "Back" }}>
        <ReviewStamp status={rec.review_status} />
      </PageHead>
      <p className="byline">
        {rec.document && <a href={`#/documents/${rec.document.id}`}>{rec.document.filename}</a>}
        {rec.sheet_name ? <span>sheet {rec.sheet_name}{rec.row_ref ? `, ${rec.row_ref}` : ""}</span> : rec.page_start ? <span>page {rec.page_start}{rec.page_end && rec.page_end !== rec.page_start ? ` to ${rec.page_end}` : ""}</span> : null}
        <span>read by {rec.method === "manual" ? "a person" : rec.method === "llm" ? "the AI reader" : rec.method === "hybrid" ? "the rules and the AI reader" : "the rules reader"}</span>
        {next && <span>{queue.length - at - 1} more after this one</span>}
      </p>

      {conflict && (
        <Notice kind="warn" title="This record changed while you were editing." action={<button type="button" className="btn btn-quiet" onClick={() => { setConflict(false); reload(); onSaved(undefined as unknown as RecordDetail); }}>Load the latest</button>}>
          {" "}Someone else saved a change, or it was checked again. Load the latest version and apply your edit again.
        </Notice>
      )}

      <div className="review-split">
        {rec.document ? (
          <SourceViewer docId={rec.document.id} fileType={rec.document.file_type} pageCount={rec.document.page_count} startPage={rec.page_start ?? 1} focusPage={ev?.page ?? null} evidence={ev?.evidence ?? null} />
        ) : <div />}

        <div className="form-col">
          {open.length > 0 && (
            <section className="notes" aria-label="Problems found">
              <h2>{plural(open.length, "problem")} to look at</h2>
              <ul className="issue-list">
                {open.map((i) => (
                  <li key={i.id} className={`issue issue-${i.severity}`}>
                    <SeverityMark severity={i.severity} />
                    <div>
                      <p className="issue-title">{ISSUE_TITLES[i.code] ?? i.code}</p>
                      <p className="issue-msg">{i.message}{i.field && <> <button type="button" className="linklike" onClick={() => focusField(i.field)}>Go to {FIELD_LABELS[i.field]?.toLowerCase() ?? i.field}</button></>}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {GROUPS.map((g) => (
            <fieldset key={g.title} className="group">
              <legend>{g.title}</legend>
              {g.fields.filter((f) => !OPTIONAL_MONEY.includes(f) || extra).map((f) => {
                const fv = rec.fields[f];
                const changed = values[f].trim() !== initial[f].trim();
                const probs = issuesFor(f);
                const bad = probs.some((p) => p.severity === "error") || !!errors[f];
                return (
                  <div key={f} className={`frow${changed ? " frow-changed" : ""}${bad ? " frow-bad" : probs.length ? " frow-warn" : ""}`}>
                    <label htmlFor={`f-${f}`}>{FIELD_LABELS[f]}</label>
                    <div className="fcell">
                      <input
                        id={`f-${f}`} ref={(el) => { refs.current[f] = el; }} value={values[f]} spellCheck={false} autoComplete="off"
                        type={DATE.has(f) ? "date" : "text"} inputMode={MONEY.has(f) ? "decimal" : undefined} maxLength={f === "currency" ? 3 : undefined}
                        className={MONEY.has(f) ? "in-money" : undefined}
                        aria-invalid={bad || undefined} aria-describedby={`h-${f}`}
                        onFocus={() => setFocus(f)} onChange={(e) => set(f, f === "currency" ? e.target.value.toUpperCase() : e.target.value)}
                      />
                      <div className="fmeta">
                        <Confidence value={changed ? 1 : fv?.confidence} source={changed ? "manual" : fv?.source} />
                        <span className="src">{changed ? "edited" : fv?.value == null ? "not found" : fv.source === "manual" ? "typed by a person" : sourceName(fv.source)}</span>
                      </div>
                    </div>
                    <div className="fhelp" id={`h-${f}`}>
                      {errors[f] && <p className="ferr">{errors[f]}</p>}
                      {!errors[f] && probs.map((p) => <p key={p.id} className={p.severity === "error" ? "ferr" : "fwarn"}>{p.message}</p>)}
                      {!changed && fv?.evidence && <p className="evidence-line"><q className="evidence">{fv.evidence}</q>{fv.page ? <span className="quiet"> page {fv.page}</span> : null}</p>}
                      {!changed && fv?.value == null && !probs.length && <p className="quiet">Nothing was found for this field. It was left empty rather than guessed.</p>}
                      {changed && <p className="quiet">Was {initial[f] || "empty"}</p>}
                    </div>
                  </div>
                );
              })}
              {g.title === "Amounts" && (
                <div className="amounts-foot">
                  {!extra && <button type="button" className="linklike" onClick={() => setExtra(true)}>Add discount or shipping</button>}
                  {calc !== null && total !== null && (
                    <p className={totalOk ? "calc calc-ok" : "calc calc-bad"} role="status">
                      <Icon name={totalOk ? "check" : "alert"} size={15} />
                      {totalOk ? `Subtotal, tax and the rest add up to ${fmtAmount(calc)}, which matches the total.` : `Subtotal, tax and the rest add up to ${fmtAmount(calc)}, but the total says ${fmtAmount(total)}.`}
                    </p>
                  )}
                </div>
              )}
            </fieldset>
          ))}

          <fieldset className="group">
            <legend>Line items</legend>
            {items.length === 0 ? <p className="quiet">No line items were found.</p> : (
              <table className="ledger items">
                <thead><tr><th scope="col">Description</th><th scope="col" className="num">Qty</th><th scope="col" className="num">Unit price</th><th scope="col" className="num">Amount</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={i}>
                      <td><input aria-label={`Line ${i + 1} description`} value={it.description} onChange={(e) => setItem(i, "description", e.target.value)} /></td>
                      <td className="num"><input aria-label={`Line ${i + 1} quantity`} className="in-money" inputMode="decimal" value={it.quantity} onChange={(e) => setItem(i, "quantity", e.target.value)} /></td>
                      <td className="num"><input aria-label={`Line ${i + 1} unit price`} className="in-money" inputMode="decimal" value={it.unit_price} onChange={(e) => setItem(i, "unit_price", e.target.value)} /></td>
                      <td className="num"><input aria-label={`Line ${i + 1} amount`} className="in-money" inputMode="decimal" value={it.amount} onChange={(e) => setItem(i, "amount", e.target.value)} /></td>
                      <td><button type="button" className="btn btn-quiet btn-icon" aria-label={`Remove line ${i + 1}`} onClick={() => setItems((l) => l.filter((_, n) => n !== i))}><Icon name="x" size={14} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="amounts-foot">
              <button type="button" className="linklike" onClick={() => setItems((l) => [...l, { description: "", quantity: "", unit_price: "", amount: "" }])}>Add a line</button>
              {linesOk !== null && (
                <p className={linesOk ? "calc calc-ok" : "calc calc-bad"} role="status">
                  <Icon name={linesOk ? "check" : "alert"} size={15} />
                  {linesOk ? `The lines add up to ${fmtAmount(lineSum)}, matching the subtotal.` : `The lines add up to ${fmtAmount(lineSum)}, but the subtotal says ${fmtAmount(subtotal)}.`}
                </p>
              )}
            </div>
          </fieldset>

          {notes.length > 0 && (
            <details className="more">
              <summary>{plural(notes.length, "note")} from the checks</summary>
              <ul className="issue-list">{notes.map((i) => <li key={i.id} className="issue issue-info"><SeverityMark severity="info" /><div><p className="issue-msg">{i.message}</p></div></li>)}</ul>
            </details>
          )}
          {rec.corrections.length > 0 && (
            <details className="more">
              <summary>{plural(rec.corrections.length, "change")} made by people</summary>
              <ol className="timeline">
                {rec.corrections.map((c) => (
                  <li key={c.id} className="tl">
                    <time dateTime={c.created_at}>{fmtDateTime(c.created_at)}</time>
                    <span>{FIELD_LABELS[c.field] ?? c.field}: {c.old_value ?? "empty"} became {c.new_value ?? "empty"}{c.note ? `. ${c.note}` : ""}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
          <p className="quiet small">Amount shown in the list: <Amount value={rec.total} currency={rec.currency} />. Last changed {fmtDateTime(rec.updated_at)}.</p>
        </div>
      </div>

      <div className="actionbar" role="region" aria-label="Actions">
        <label className="note-field">
          <span className="sr-only">Note about this change</span>
          <input placeholder="Why did you change it? (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="actionbar-buttons">
          <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void remove()}><Icon name="trash" size={15} />Delete record</button>
          <button type="button" className="btn btn-quiet" disabled={busy || dirty} onClick={() => void recheck()} title={dirty ? "Save first" : undefined}><Icon name="refresh" size={15} />Check again</button>
          <button type="button" className="btn btn-quiet" disabled={!dirty || busy} onClick={() => { setValues(initial); setItems(initialItems); setErrors({}); }}>Discard changes</button>
          <button type="button" className="btn btn-quiet" disabled={!dirty || busy} onClick={() => void save()}>Save changes</button>
          <button type="button" className="btn btn-primary" disabled={busy || rec.review_status === "approved" && !dirty} onClick={() => void approve()}>
            <Icon name="check" size={15} />{dirty ? "Save and approve" : rec.review_status === "approved" ? "Approved" : next ? "Approve and go to the next" : "Approve"}
          </button>
        </div>
      </div>
    </>
  );
}
