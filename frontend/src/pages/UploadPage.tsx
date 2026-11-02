import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { api } from "../api";
import { Empty, Icon, LoadError, Notice, PageHead, ProgressBar, DocStatusMark, IssueCountsMark, useToast } from "../components/ui";
import { fmtAgo, fmtBytes, plural, STAGE_LABELS } from "../format";
import { useLoad } from "../hooks";
import type { Doc, DocList } from "../types";
import { ACCEPTED, filesFromDrop, useUploader } from "../uploader";

const UPLOAD_LABEL: Record<string, string> = { waiting: "Waiting to upload", sending: "Uploading", queued: "Sent, processing next", duplicate: "Already stored", rejected: "Not accepted", error: "Upload failed" };

export default function UploadPage() {
  const up = useUploader();
  const toast = useToast();
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);

  const take = (files: File[]) => {
    const r = up.addFiles(files);
    if (r.skipped) toast(`${plural(r.skipped, "file")} skipped: only PDF, images, Excel and CSV are accepted.`, "info");
    if (r.repeated) toast(`${plural(r.repeated, "file")} already in the list.`, "info");
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    void filesFromDrop(e.dataTransfer).then(take);
  };

  const sending = up.items.filter((i) => i.status === "waiting" || i.status === "sending").length;
  const counts = {
    queued: up.items.filter((i) => i.status === "queued").length,
    duplicate: up.items.filter((i) => i.status === "duplicate").length,
    bad: up.items.filter((i) => i.status === "rejected" || i.status === "error").length,
  };
  const shown = [...up.items].sort((a, b) => rank(a.status) - rank(b.status)).slice(0, 150);

  return (
    <>
      <PageHead title="Upload documents" />
      <div
        className={`dropzone${over ? " dropzone-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <Icon name="upload" size={30} />
        <div className="dz-text">
          <p className="dz-title">Drop invoices, receipts and spreadsheets here</p>
          <p className="dz-sub">Whole folders work too. PDF, JPG, PNG, TIFF, XLSX, XLS and CSV, up to 100 MB each.</p>
        </div>
        <div className="dz-buttons">
          <button type="button" className="btn btn-primary" onClick={() => fileInput.current?.click()}>Choose files</button>
          <button type="button" className="btn btn-quiet" onClick={() => dirInput.current?.click()}>Choose a folder</button>
        </div>
        <input ref={fileInput} type="file" multiple accept={ACCEPTED.join(",")} hidden aria-label="Choose files to upload"
          onChange={(e) => { take(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <input ref={dirInput} type="file" multiple hidden aria-label="Choose a folder to upload"
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={(e) => { take(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </div>

      {up.items.length > 0 && (
        <section className="block" aria-label="Uploads">
          <div className="block-head">
            <h2>{sending > 0 ? `Uploading ${plural(sending, "file")}` : "Upload finished"}</h2>
            <button type="button" className="btn btn-quiet" onClick={up.clearFinished} disabled={sending === up.items.length}>Clear finished</button>
          </div>
          <ProgressBar value={up.totalBytes ? up.sentBytes / up.totalBytes : 1} label="Upload progress" />
          <p className="meta-line">
            <span>{fmtBytes(up.sentBytes)} of {fmtBytes(up.totalBytes)} sent</span>
            <span>{plural(counts.queued, "file")} queued for processing</span>
            {counts.duplicate > 0 && <span>{plural(counts.duplicate, "file")} already stored</span>}
            {counts.bad > 0 && <span className="count-bad">{plural(counts.bad, "file")} not accepted</span>}
          </p>
          <ul className="upload-list">
            {shown.map((i) => (
              <li key={i.key} className={`up-row up-${i.status}`}>
                <span className="up-name" title={i.file.name}>{i.file.name}</span>
                <span className="up-size">{fmtBytes(i.file.size)}</span>
                <span className="up-state">
                  {UPLOAD_LABEL[i.status]}
                  {i.message && <span className="up-msg"> {i.message}</span>}
                </span>
                <span className="up-actions">
                  {i.documentId && <a href={`#/documents/${i.documentId}`}>Open</a>}
                  {i.status === "duplicate" && <button type="button" className="linklike" onClick={() => up.processAnyway(i.key)}>Process anyway</button>}
                  {i.status === "error" && <button type="button" className="linklike" onClick={() => up.retry(i.key)}>Try again</button>}
                </span>
              </li>
            ))}
          </ul>
          {up.items.length > shown.length && <p className="quiet">and {plural(up.items.length - shown.length, "more file")}</p>}
        </section>
      )}

      <Pipeline />
    </>
  );
}

function rank(s: string): number {
  return { error: 0, rejected: 1, duplicate: 2, sending: 3, waiting: 4, queued: 5 }[s] ?? 6;
}

/** What the server is doing right now. Refreshes quickly while there is work and slowly when idle. */
function Pipeline() {
  const toast = useToast();
  const active = useLoad(
    async (signal) => {
      const [proc, queued, recent] = await Promise.all([
        api.get<DocList>("/documents", { status: "processing", limit: 20 }, signal),
        api.get<DocList>("/documents", { status: "queued", sort: "oldest", limit: 8 }, signal),
        api.get<DocList>("/documents", { sort: "newest", limit: 14 }, signal),
      ]);
      return { proc, queued, recent };
    },
    [],
    (d) => (d && d.proc.total + d.queued.total > 0 ? 1500 : 6000),
  );

  if (active.error && !active.data) return <LoadError error={active.error} retry={active.reload} />;
  const d = active.data;
  if (!d) return <p className="quiet" role="status">Loading…</p>;
  const counts = d.recent.status_counts;
  const finished = d.recent.items.filter((x) => x.status === "completed" || x.status === "failed" || x.status === "cancelled").slice(0, 8);
  const busy = d.proc.total + d.queued.total > 0;

  const retryFailed = async () => {
    try {
      const r = await api.post<{ requeued: number; skipped: { code: string }[] }>("/documents/reprocess-failed");
      toast(r.requeued ? `${plural(r.requeued, "document")} queued again.` : "Nothing to retry.", r.requeued ? "good" : "info");
      if (r.skipped.length) toast(`${plural(r.skipped.length, "document")} skipped because the file itself is damaged or locked. Upload a fixed copy.`, "info");
      active.reload();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };

  return (
    <section className="block" aria-label="Processing">
      <div className="block-head">
        <h2>{busy ? "Processing now" : "Nothing is processing"}</h2>
        {counts.failed > 0 && <button type="button" className="btn btn-quiet" onClick={retryFailed}><Icon name="refresh" size={15} />Retry {plural(counts.failed, "failed document")}</button>}
      </div>
      <dl className="tally">
        <div><dt>Waiting</dt><dd>{counts.queued.toLocaleString()}</dd></div>
        <div><dt>Processing</dt><dd>{counts.processing.toLocaleString()}</dd></div>
        <div><dt>Finished</dt><dd>{counts.completed.toLocaleString()}</dd></div>
        <div><dt>Failed</dt><dd className={counts.failed ? "count-bad" : undefined}>{counts.failed.toLocaleString()}</dd></div>
      </dl>

      {d.proc.items.length > 0 && (
        <ul className="active-list">
          {d.proc.items.map((x) => <ActiveRow key={x.id} doc={x} />)}
        </ul>
      )}
      {d.queued.items.length > 0 && (
        <>
          <p className="quiet next-up">Next in line{d.queued.total > d.queued.items.length ? ` (${d.queued.total.toLocaleString()} waiting)` : ""}</p>
          <ul className="plain-list">
            {d.queued.items.map((x) => <li key={x.id}><a href={`#/documents/${x.id}`}>{x.filename}</a><span className="quiet">{x.stage === "waiting to retry" ? " waiting to try again" : ""}</span></li>)}
          </ul>
        </>
      )}

      <h3 className="sub-head">Finished recently</h3>
      {finished.length === 0 ? (
        <Empty title="Nothing finished yet">Upload a document above and it will show up here as soon as it is read.</Empty>
      ) : (
        <table className="ledger">
          <thead><tr><th scope="col">Document</th><th scope="col">Result</th><th scope="col" className="num">Records</th><th scope="col">Issues</th><th scope="col">When</th></tr></thead>
          <tbody>
            {finished.map((x) => (
              <tr key={x.id}>
                <td><a href={`#/documents/${x.id}`} className="name-cell">{x.filename}</a></td>
                <td><DocStatusMark doc={x} />{x.status === "failed" && <span className="quiet"> {x.error_code}</span>}</td>
                <td className="num">{x.record_count}</td>
                <td><IssueCountsMark counts={x.issues} /></td>
                <td className="quiet">{fmtAgo(x.completed_at ?? x.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {counts.failed === 0 && counts.completed === 0 && counts.queued === 0 && counts.processing === 0 && (
        <Notice kind="info">Documents you upload are read in the background. You can leave this page; progress is kept.</Notice>
      )}
    </section>
  );
}

function ActiveRow({ doc }: { doc: Doc }) {
  const stage = STAGE_LABELS[doc.stage ?? ""] ?? doc.stage ?? "Working";
  const reading = doc.stage === "reading" && doc.page_count;
  return (
    <li className="active-row">
      <div className="active-top">
        <a href={`#/documents/${doc.id}`} className="name-cell">{doc.filename}</a>
        <span className="quiet">{stage}{reading ? `, page ${Math.min(doc.pages_done + 1, doc.page_count ?? 0)} of ${doc.page_count}` : ""}</span>
      </div>
      <ProgressBar value={reading ? doc.progress : doc.stage === "reading" ? null : 0.97} label={`Progress of ${doc.filename}`} />
    </li>
  );
}
