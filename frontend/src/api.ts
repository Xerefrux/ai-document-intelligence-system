import type { UploadResponse } from "./types";

const KEY = "docintel.apiKey";

export function getApiKey(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(value: string): void {
  try {
    if (value) sessionStorage.setItem(KEY, value);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage can be unavailable; the key then lasts until the page is reloaded */
  }
}

export class ApiError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export const UNAUTHORIZED_EVENT = "docintel:unauthorized";

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { Accept: "application/json", ...extra };
  const key = getApiKey();
  if (key) h["X-API-Key"] = key;
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  let code = "ERROR";
  let message = `The server answered ${res.status}.`;
  let details: Record<string, unknown> | undefined;
  try {
    const body = await res.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    }
  } catch {
    /* not JSON */
  }
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (res.status === 429) message = message || "Too many requests. Wait a moment.";
  return new ApiError(res.status, code, message, details);
}

export type Params = Record<string, string | number | boolean | null | undefined>;

export function withParams(path: string, params?: Params): string {
  if (!params) return path;
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: headers(body !== undefined ? { "Content-Type": "application/json" } : undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "NETWORK", "Cannot reach the server. Check that it is running.");
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string, params?: Params, signal?: AbortSignal) => request<T>("GET", withParams(path, params), undefined, signal),
  post: <T,>(path: string, body?: unknown, params?: Params) => request<T>("POST", withParams(path, params), body ?? {}),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
};

/** Fetch a protected file (page image, original document) and return an object URL for it. */
export async function fetchBlobUrl(path: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(`/api${path}`, { headers: headers(), signal });
  if (!res.ok) throw await toError(res);
  return URL.createObjectURL(await res.blob());
}

export async function downloadFile(path: string, filename: string): Promise<void> {
  const url = await fetchBlobUrl(path);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Upload files in one request. XMLHttpRequest is used because fetch cannot report upload progress. */
export function uploadFiles(files: File[], opts: { force?: boolean; onProgress?: (loaded: number, total: number) => void; signal?: AbortSignal } = {}): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", withParams("/api/documents", opts.force ? { force: true } : undefined));
    const key = getApiKey();
    if (key) xhr.setRequestHeader("X-API-Key", key);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) return resolve(body as UploadResponse);
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      reject(new ApiError(xhr.status, body?.error?.code ?? "UPLOAD_FAILED", body?.error?.message ?? `The upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new ApiError(0, "NETWORK", "Cannot reach the server. Check that it is running."));
    xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));
    opts.signal?.addEventListener("abort", () => xhr.abort());
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    xhr.send(form);
  });
}
