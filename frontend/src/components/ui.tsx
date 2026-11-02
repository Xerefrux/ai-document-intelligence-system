import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import type { Doc, RecReview, Severity } from "../types";

/* ------------------------------------------------------------------ icons (24px grid, drawn with strokes) */

const PATHS: Record<string, string> = {
  upload: "M12 16V4m0 0L7 9m5-5l5 5M4 20h16",
  download: "M12 4v12m0 0l-5-5m5 5l5-5M4 20h16",
  check: "M4 12.5l5 5L20 6.5",
  x: "M6 6l12 12M18 6L6 18",
  alert: "M12 3.5l9.5 16.5h-19L12 3.5zM12 10v4.5M12 17.2v.1",
  refresh: "M20 11a8 8 0 10-2.4 5.7M20 4.5V11h-6.5",
  trash: "M4 7h16M9.5 7V4.5h5V7M6.5 7l.9 13h9.2l.9-13",
  search: "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM20 20l-4.8-4.8",
  file: "M6 3h8l5 5v13H6V3zM14 3v5h5",
  plus: "M12 5v14M5 12h14",
  back: "M19 12H5m0 0l6-6m-6 6l6 6",
  next: "M5 12h14m0 0l-6-6m6 6l-6 6",
  info: "M12 11v6M12 7.5v.1M12 3a9 9 0 100 18 9 9 0 000-18z",
  stop: "M7 7h10v10H7z",
  zoomIn: "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM20 20l-4.8-4.8M8 10.5h5M10.5 8v5",
  zoomOut: "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM20 20l-4.8-4.8M8 10.5h5",
  columns: "M4 5h16v14H4zM12 5v14",
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

/* ------------------------------------------------------------------ status marks */

export function DocStatusMark({ doc }: { doc: Pick<Doc, "status" | "review_status"> }) {
  if (doc.status === "failed") return <span className="mark mark-bad">Failed</span>;
  if (doc.status === "cancelled") return <span className="mark mark-muted">Cancelled</span>;
  if (doc.status === "queued") return <span className="mark mark-muted">Waiting</span>;
  if (doc.status === "processing") return <span className="mark mark-live"><span className="pulse" aria-hidden="true" />Processing</span>;
  if (doc.review_status === "needs_review") return <span className="mark mark-bad">Needs review</span>;
  return <span className="mark mark-good">Checked</span>;
}

const REC_LABEL: Record<RecReview, string> = { ok: "No issues", needs_review: "Needs review", corrected: "Corrected", approved: "Approved" };
const REC_CLASS: Record<RecReview, string> = { ok: "mark-good", needs_review: "mark-bad", corrected: "mark-pen", approved: "mark-good" };

export function RecStatusMark({ status }: { status: RecReview }) {
  return <span className={`mark ${REC_CLASS[status]}`}>{REC_LABEL[status]}</span>;
}

/** The inked-stamp version used once, at the top of the review screen. */
export function ReviewStamp({ status }: { status: RecReview }) {
  return <span className={`stamp stamp-${status}`}>{REC_LABEL[status]}</span>;
}

export function SeverityMark({ severity }: { severity: Severity }) {
  const label = severity === "error" ? "Error" : severity === "warning" ? "Warning" : "Note";
  return (
    <span className={`sev sev-${severity}`} title={label}>
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        {severity === "error" ? <><circle cx="8" cy="8" r="7" fill="currentColor" /><path d="M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" /></> : null}
        {severity === "warning" ? <><path d="M8 1.5L15 14H1L8 1.5z" fill="currentColor" /><path d="M8 6v3.6M8 11.6v.1" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" /></> : null}
        {severity === "info" ? <><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M8 7.2V11M8 5v.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></> : null}
      </svg>
      <span className="sr-only">{label}: </span>
    </span>
  );
}

export function IssueCountsMark({ counts }: { counts: { error: number; warning: number } }) {
  if (!counts.error && !counts.warning) return <span className="quiet">None</span>;
  return (
    <span className="issue-counts">
      {counts.error > 0 && <span className="count-bad">{counts.error} {counts.error === 1 ? "error" : "errors"}</span>}
      {counts.warning > 0 && <span className="count-warn">{counts.warning} {counts.warning === 1 ? "warning" : "warnings"}</span>}
    </span>
  );
}

/** Three bars: how sure the reader was. Always paired with text for people who cannot see colour. */
export function Confidence({ value, source }: { value: number | null | undefined; source?: string }) {
  const v = value ?? 0;
  const level = v >= 0.85 ? 3 : v >= 0.6 ? 2 : v > 0 ? 1 : 0;
  const words = level === 3 ? "high" : level === 2 ? "medium" : level === 1 ? "low" : "no value";
  return (
    <span className={`conf conf-${level}`} title={`Confidence ${Math.round(v * 100)}%${source ? `, read by ${sourceName(source)}` : ""}`}>
      <span className="conf-bars" aria-hidden="true"><i /><i /><i /></span>
      <span className="sr-only">Confidence {words}</span>
    </span>
  );
}

export function sourceName(source: string | undefined): string {
  switch (source) {
    case "llm": return "the AI reader";
    case "both": return "the rules and the AI reader";
    case "manual": return "a person";
    default: return "the rules reader";
  }
}

export function ProgressBar({ value, label }: { value: number | null | undefined; label: string }) {
  const pct = value === null || value === undefined ? null : Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className={`bar${pct === null ? " bar-wait" : ""}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
      <div className="bar-fill" style={{ width: pct === null ? "35%" : `${pct}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------ text with highlighted search hits (no innerHTML) */

/** The API marks hits with \x02 ... \x03. Turn them into <mark> elements. */
export function Snippet({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  const out: ReactNode[] = [];
  let inMark = false;
  text.split(/([\x02\x03])/).forEach((part, i) => {
    if (part === "\x02") inMark = true;
    else if (part === "\x03") inMark = false;
    else if (part) out.push(inMark ? <mark key={i}>{part}</mark> : part);
  });
  return <>{out}</>;
}

/** Plain text with every occurrence of `needle` highlighted, ignoring case and runs of spaces. */
export function Highlight({ text, needle }: { text: string; needle: string | null | undefined }) {
  const n = (needle ?? "").trim();
  if (n.length < 2) return <>{text}</>;
  const pattern = n.split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
  let re: RegExp;
  try {
    re = new RegExp(pattern, "gi");
  } catch {
    return <>{text}</>;
  }
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let guard = 0;
  while ((m = re.exec(text)) && guard++ < 200) {
    if (m[0].length === 0) break;
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<mark key={m.index} className="hl">{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

/* ------------------------------------------------------------------ small layout pieces */

export function PageHead({ title, children, back }: { title: ReactNode; children?: ReactNode; back?: { to: string; label: string } }) {
  return (
    <header className="page-head">
      <div>
        {back && <a className="backlink" href={`#${back.to}`}><Icon name="back" size={15} />{back.label}</a>}
        <h1>{title}</h1>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p className="empty-body">{children}</p>}
      {action}
    </div>
  );
}

export function Notice({ kind = "info", title, children, action }: { kind?: "info" | "warn" | "bad" | "good"; title?: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === "bad" ? "alert" : "status"}>
      <div>
        {title && <strong>{title}</strong>}
        {children && <span>{children}</span>}
      </div>
      {action}
    </div>
  );
}

export function LoadError({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <Notice kind="bad" title="Could not load this" action={retry && <button type="button" className="btn btn-quiet" onClick={retry}>Try again</button>}>
      {error.message}
    </Notice>
  );
}

export interface TabDef { id: string; label: string; count?: number }

export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string }) {
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    requestAnimationFrame(() => document.getElementById(`tab-${next.id}`)?.focus());
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t, i) => (
        <button key={t.id} id={`tab-${t.id}`} type="button" role="tab" aria-selected={t.id === active} aria-controls={`panel-${t.id}`} tabIndex={t.id === active ? 0 : -1}
          className={t.id === active ? "tab tab-on" : "tab"} onClick={() => onChange(t.id)} onKeyDown={(e) => onKey(e, i)}>
          {t.label}{t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ toasts */

type ToastKind = "good" | "bad" | "info";
interface Toast { id: number; kind: ToastKind; text: string }
const ToastCtx = createContext<(text: string, kind?: ToastKind) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback((text: string, kind: ToastKind = "info") => {
    const id = nextId.current++;
    setItems((l) => [...l.slice(-3), { id, kind, text }]);
    window.setTimeout(() => setItems((l) => l.filter((t) => t.id !== id)), kind === "bad" ? 9000 : 4500);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.text}
            <button type="button" className="toast-x" aria-label="Dismiss" onClick={() => setItems((l) => l.filter((x) => x.id !== t.id))}><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ------------------------------------------------------------------ money */

export function Amount({ value, currency }: { value: number | null | undefined; currency?: string | null }) {
  if (value === null || value === undefined) return <span className="quiet">none</span>;
  const s = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  return <span className="amount">{s}{currency ? <span className="cur"> {currency}</span> : null}</span>;
}
