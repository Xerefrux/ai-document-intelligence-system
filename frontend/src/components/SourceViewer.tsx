import { useEffect, useState } from "react";
import { api, fetchBlobUrl } from "../api";
import { fmtPercent } from "../format";
import { useLoad } from "../hooks";
import type { TextWindow } from "../types";
import { Highlight, Icon } from "./ui";

function useBlobImage(path: string | null) {
  const [state, setState] = useState<{ url?: string; error?: string; loading: boolean }>({ loading: !!path });
  useEffect(() => {
    if (!path) { setState({ loading: false }); return; }
    const ctl = new AbortController();
    let made: string | undefined;
    setState({ loading: true });
    fetchBlobUrl(path, ctl.signal)
      .then((url) => { made = url; setState({ url, loading: false }); })
      .catch((e: Error) => { if (e.name !== "AbortError") setState({ error: e.message, loading: false }); });
    return () => { ctl.abort(); if (made) URL.revokeObjectURL(made); };
  }, [path]);
  return state;
}

interface Props {
  docId: string;
  fileType: string;
  pageCount: number | null;
  startPage?: number | null;
  focusPage?: number | null;
  evidence?: string | null;
}

/** The page as it was received, next to the values read from it. Spreadsheets show the sheet text instead of a picture. */
export default function SourceViewer({ docId, fileType, pageCount, startPage, focusPage, evidence }: Props) {
  const paper = fileType === "pdf" || fileType === "image";
  const total = Math.max(1, pageCount ?? 1);
  const [page, setPage] = useState(Math.min(total, Math.max(1, startPage ?? 1)));
  const [zoom, setZoom] = useState(1);
  const [showText, setShowText] = useState(!paper);

  useEffect(() => { if (focusPage && focusPage >= 1 && focusPage <= total) setPage(focusPage); }, [focusPage, total]);
  useEffect(() => { setPage(Math.min(total, Math.max(1, startPage ?? 1))); }, [docId, startPage, total]);

  const img = useBlobImage(paper ? `/documents/${docId}/pages/${page}/image` : null);
  const text = useLoad((s) => api.get<TextWindow>(`/documents/${docId}/text`, { from: page, to: page }, s), [docId, page, showText], undefined);
  const tp = text.data?.pages[0];

  return (
    <section className="source" aria-label="Source document">
      <div className="source-bar">
        <div className="pager-inline">
          <button type="button" className="btn btn-quiet btn-icon" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page"><Icon name="back" size={16} /></button>
          <span className="page-of">{paper ? "Page" : "Part"} {page} of {total}{tp?.label ? `, sheet ${tp.label}` : ""}</span>
          <button type="button" className="btn btn-quiet btn-icon" disabled={page >= total} onClick={() => setPage(page + 1)} aria-label="Next page"><Icon name="next" size={16} /></button>
        </div>
        {paper && (
          <div className="pager-inline">
            <button type="button" className="btn btn-quiet btn-icon" onClick={() => setZoom(Math.max(0.6, +(zoom - 0.2).toFixed(1)))} aria-label="Zoom out"><Icon name="zoomOut" size={16} /></button>
            <span className="page-of">{Math.round(zoom * 100)}%</span>
            <button type="button" className="btn btn-quiet btn-icon" onClick={() => setZoom(Math.min(2.4, +(zoom + 0.2).toFixed(1)))} aria-label="Zoom in"><Icon name="zoomIn" size={16} /></button>
          </div>
        )}
      </div>

      {paper && (
        <div className="desk-surface">
          {img.loading && <p className="quiet pad" role="status">Drawing the page…</p>}
          {img.error && <p className="pad source-error" role="alert">The page picture is not available: {img.error}</p>}
          {img.url && <img className="paper" src={img.url} alt={`Page ${page} of the uploaded document`} style={{ width: `${zoom * 100}%` }} />}
        </div>
      )}

      <div className="source-text">
        {paper && (
          <button type="button" className="linklike" aria-expanded={showText} onClick={() => setShowText(!showText)}>
            {showText ? "Hide the text read from this page" : "Show the text read from this page"}
          </button>
        )}
        {showText && (
          <>
            {tp && <p className="quiet small">{tp.source === "ocr" ? `Read with OCR${tp.ocr_confidence != null ? `, ${fmtPercent(tp.ocr_confidence)} confidence` : ""}.` : tp.source === "failed" ? "This page could not be read." : "Taken from the file's text layer."}</p>}
            {text.error ? <p className="source-error">{text.error.message}</p> : tp ? (
              <pre className="rawtext rawtext-small"><Highlight text={tp.text || "(no text on this page)"} needle={evidence} /></pre>
            ) : <p className="quiet">Loading…</p>}
          </>
        )}
      </div>
    </section>
  );
}
