import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { uploadFiles } from "./api";
import type { UploadResultItem } from "./types";

export type UploadState = "waiting" | "sending" | "queued" | "duplicate" | "rejected" | "error";

export interface UploadItem {
  key: string;
  file: File;
  status: UploadState;
  message?: string;
  code?: string;
  documentId?: string;
  warnings?: string[];
  force?: boolean;
}

export const ACCEPTED = [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"];
const MAX_FILES = 20;
const MAX_BYTES = 120 * 1024 * 1024;
const PARALLEL = 2;

export const isAccepted = (name: string) => ACCEPTED.some((e) => name.toLowerCase().endsWith(e));
const keyOf = (f: File) => `${f.name}:${f.size}:${f.lastModified}`;

/** Group waiting files into requests of at most MAX_FILES files and about MAX_BYTES bytes. */
export function makeBatch(waiting: UploadItem[]): UploadItem[] {
  const batch: UploadItem[] = [];
  let bytes = 0;
  for (const it of waiting) {
    if (batch.length >= MAX_FILES) break;
    if (batch.length > 0 && bytes + it.file.size > MAX_BYTES) break;
    batch.push(it);
    bytes += it.file.size;
  }
  return batch;
}

interface Uploader {
  items: UploadItem[];
  sentBytes: number;
  totalBytes: number;
  addFiles: (files: File[]) => { added: number; skipped: number; repeated: number };
  retry: (key: string) => void;
  processAnyway: (key: string) => void;
  clearFinished: () => void;
}

const Ctx = createContext<Uploader | null>(null);
export const useUploader = (): Uploader => {
  const v = useContext(Ctx);
  if (!v) throw new Error("UploaderProvider is missing");
  return v;
};

export function UploaderProvider({ children, onDone }: { children: ReactNode; onDone?: () => void }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [inflight, setInflight] = useState<Record<number, number>>({});
  const itemsRef = useRef<UploadItem[]>([]);
  const active = useRef(0);
  const batchNo = useRef(0);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  const commit = useCallback((fn: (l: UploadItem[]) => UploadItem[]) => {
    itemsRef.current = fn(itemsRef.current);
    setItems(itemsRef.current);
  }, []);

  const pump = useCallback(() => {
    while (active.current < PARALLEL) {
      const waiting = itemsRef.current.filter((i) => i.status === "waiting");
      if (waiting.length === 0) break;
      const force = waiting[0].force === true;
      const batch = makeBatch(waiting.filter((i) => (i.force === true) === force));
      const keys = new Set(batch.map((b) => b.key));
      const id = ++batchNo.current;
      active.current++;
      commit((l) => l.map((i) => (keys.has(i.key) ? { ...i, status: "sending" } : i)));
      uploadFiles(batch.map((b) => b.file), { force, onProgress: (loaded) => setInflight((m) => ({ ...m, [id]: loaded })) })
        .then((res) => {
          const byIndex = res.items as UploadResultItem[];
          commit((l) => l.map((i) => {
            const idx = batch.findIndex((b) => b.key === i.key);
            if (idx < 0) return i;
            const r = byIndex[idx];
            if (!r) return { ...i, status: "error", message: "The server sent no answer for this file." };
            return { ...i, status: r.status, message: r.message, code: r.code, documentId: r.document_id, warnings: r.warnings };
          }));
        })
        .catch((e: Error) => {
          commit((l) => l.map((i) => (keys.has(i.key) ? { ...i, status: "error", message: e.message } : i)));
        })
        .finally(() => {
          active.current--;
          setInflight((m) => {
            const { [id]: _gone, ...rest } = m;
            return rest;
          });
          if (itemsRef.current.every((i) => i.status !== "waiting" && i.status !== "sending")) doneRef.current?.();
          pump();
        });
    }
  }, [commit]);

  const addFiles = useCallback((files: File[]) => {
    const known = new Set(itemsRef.current.filter((i) => i.status !== "error" && i.status !== "rejected").map((i) => keyOf(i.file)));
    let skipped = 0;
    let repeated = 0;
    const fresh: UploadItem[] = [];
    for (const f of files) {
      if (!isAccepted(f.name)) { skipped++; continue; }
      const k = keyOf(f);
      if (known.has(k)) { repeated++; continue; }
      known.add(k);
      fresh.push({ key: `${k}:${Math.random().toString(36).slice(2, 8)}`, file: f, status: "waiting" });
    }
    if (fresh.length) {
      commit((l) => [...l, ...fresh]);
      pump();
    }
    return { added: fresh.length, skipped, repeated };
  }, [commit, pump]);

  const retry = useCallback((key: string) => {
    commit((l) => l.map((i) => (i.key === key ? { ...i, status: "waiting", message: undefined, code: undefined } : i)));
    pump();
  }, [commit, pump]);

  const processAnyway = useCallback((key: string) => {
    commit((l) => l.map((i) => (i.key === key ? { ...i, status: "waiting", force: true, message: undefined } : i)));
    pump();
  }, [commit, pump]);

  const clearFinished = useCallback(() => {
    commit((l) => l.filter((i) => i.status === "waiting" || i.status === "sending"));
  }, [commit]);

  const value = useMemo<Uploader>(() => {
    const totalBytes = items.reduce((n, i) => n + i.file.size, 0);
    const doneBytes = items.filter((i) => i.status !== "waiting" && i.status !== "sending").reduce((n, i) => n + i.file.size, 0);
    const sendingBytes = items.filter((i) => i.status === "sending").reduce((n, i) => n + i.file.size, 0);
    const loaded = Object.values(inflight).reduce((a, b) => a + b, 0);
    return { items, totalBytes, sentBytes: doneBytes + Math.min(loaded, sendingBytes), addFiles, retry, processAnyway, clearFinished };
  }, [items, inflight, addFiles, retry, processAnyway, clearFinished]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Collect every file from a drop, including files inside dropped folders. Must be called synchronously from the drop event. */
export function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const entries = Array.from(dt.items ?? [])
    .map((i) => (i.kind === "file" ? (i as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }).webkitGetAsEntry?.() ?? null : null))
    .filter((e): e is FileSystemEntry => e !== null);
  if (entries.length === 0) return Promise.resolve(Array.from(dt.files));
  const out: File[] = [];
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      out.push(await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej)));
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        if (batch.length === 0) break;
        for (const e of batch) await walk(e);
      }
    }
  };
  return (async () => {
    for (const e of entries) await walk(e);
    return out;
  })();
}
