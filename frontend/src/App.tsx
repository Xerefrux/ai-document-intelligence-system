import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { UNAUTHORIZED_EVENT, api, getApiKey, setApiKey } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Icon, ToastProvider } from "./components/ui";
import { useLoad, useRoute } from "./hooks";
import ComparePage from "./pages/ComparePage";
import DocumentPage from "./pages/DocumentPage";
import DocumentsPage from "./pages/DocumentsPage";
import OverviewPage from "./pages/OverviewPage";
import RecordPage from "./pages/RecordPage";
import ReviewPage from "./pages/ReviewPage";
import SearchPage from "./pages/SearchPage";
import UploadPage from "./pages/UploadPage";
import type { Health, Stats } from "./types";
import { UploaderProvider } from "./uploader";

const NAV = [
  { to: "/", key: "upload", label: "Upload", icon: "upload" },
  { to: "/documents", key: "documents", label: "Documents", icon: "file" },
  { to: "/review", key: "review", label: "To review", icon: "alert" },
  { to: "/search", key: "search", label: "Search", icon: "search" },
  { to: "/compare", key: "compare", label: "Compare", icon: "columns" },
  { to: "/overview", key: "overview", label: "Overview", icon: "info" },
];

export default function App() {
  return (
    <ToastProvider>
      <UploaderProvider>
        <Shell />
      </UploaderProvider>
    </ToastProvider>
  );
}

function Shell() {
  const route = useRoute();
  const [needKey, setNeedKey] = useState(false);
  const health = useLoad((s) => api.get<Health>("/health", undefined, s), [needKey], () => 8000);
  const stats = useLoad((s) => api.get<Stats>("/stats", undefined, s), [needKey], () => 6000);

  useEffect(() => {
    const on = () => setNeedKey(true);
    window.addEventListener(UNAUTHORIZED_EVENT, on);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, on);
  }, []);
  useEffect(() => {
    if (health.data?.auth_required && !getApiKey()) setNeedKey(true);
  }, [health.data?.auth_required]);

  const [section = "", id] = route.path;
  const current = section === "" ? "upload" : section === "records" ? "review" : section;
  const toReview = stats.data ? stats.data.records.by_review.needs_review + stats.data.documents.by_status.failed + stats.data.documents.no_records : 0;

  let page;
  switch (section) {
    case "":
    case "upload": page = <UploadPage />; break;
    case "documents": page = id ? <DocumentPage key={id} id={id} /> : <DocumentsPage route={route} />; break;
    case "records": page = id ? <RecordPage id={id} /> : <ReviewPage />; break;
    case "review": page = <ReviewPage />; break;
    case "search": page = <SearchPage route={route} />; break;
    case "compare": page = <ComparePage />; break;
    case "overview": page = <OverviewPage />; break;
    default: page = <div className="empty"><p className="empty-title">There is no page here</p><a href="#/">Go to Upload</a></div>;
  }

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to the content</a>
      <nav className="spine" aria-label="Main">
        <a className="brand" href="#/" aria-label="DocIntel, home">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand-name">DocIntel</span>
        </a>
        <ul className="nav">
          {NAV.map((n) => (
            <li key={n.key}>
              <a href={`#${n.to}`} className={current === n.key ? "nav-link nav-on" : "nav-link"} aria-current={current === n.key ? "page" : undefined}>
                <Icon name={n.icon} size={17} />
                <span>{n.label}</span>
                {n.key === "review" && toReview > 0 && <span className="nav-count" aria-label={`${toReview} waiting`}>{toReview > 999 ? "999+" : toReview}</span>}
              </a>
            </li>
          ))}
        </ul>
        <div className="spine-foot" role="status">
          {health.data ? (
            <>
              <p className={health.data.status === "ok" ? "dot dot-ok" : "dot dot-bad"}>{health.data.status === "ok" ? "Server running" : health.data.status === "degraded" ? "Jobs are waiting, no worker is running" : "Server unavailable"}</p>
              <p>{health.data.workers.alive} {health.data.workers.alive === 1 ? "worker" : "workers"}, {health.data.queue.queued} waiting</p>
              <p>{health.data.ocr.tesseract ? "OCR: Tesseract" : "OCR: RapidOCR"}. AI reader {health.data.llm === "none" ? "off" : "on"}.</p>
            </>
          ) : health.error ? <p className="dot dot-bad">Cannot reach the server</p> : <p>Connecting…</p>}
        </div>
      </nav>
      <main id="main" className="desk" tabIndex={-1}>
        {/* Keyed by the page, so going to another page starts clean after a failure. */}
        <ErrorBoundary key={route.path.join("/")}>{page}</ErrorBoundary>
      </main>
      {needKey && <KeyPrompt onDone={() => { setNeedKey(false); window.location.reload(); }} />}
    </div>
  );
}

function KeyPrompt({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setApiKey(value.trim());
    onDone();
  };
  return (
    <div className="overlay">
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="key-title" onSubmit={submit}>
        <h2 id="key-title">This server needs an API key</h2>
        <p>Ask whoever runs it for the key. It is kept in this browser tab only and is gone when you close the tab.</p>
        <label>API key<input type="password" autoFocus autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} /></label>
        <button type="submit" className="btn btn-primary" disabled={!value.trim()}>Continue</button>
      </form>
    </div>
  );
}
