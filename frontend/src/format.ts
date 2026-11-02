/** Timestamps from the API are UTC and end in Z; dates (invoice dates) are plain YYYY-MM-DD. */

export function fmtAmount(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(d).replace(/ /g, "\u00a0");
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(d);
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s - m * 60)} s`;
}

export function fmtSeconds(s: number | null | undefined): string {
  return s === null || s === undefined ? "none yet" : fmtDuration(Math.round(s * 1000));
}

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return fmtDateTime(iso);
}

export function fmtPercent(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "" : `${(v * 100).toFixed(digits)}%`;
}

export const FIELD_LABELS: Record<string, string> = {
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  supplier_name: "Supplier",
  customer_name: "Customer",
  subtotal: "Subtotal",
  tax: "Tax",
  discount: "Discount",
  shipping: "Shipping",
  total: "Total",
  currency: "Currency",
  line_items: "Line items",
  review_status: "Review status",
};

export const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting its turn",
  starting: "Starting",
  reading: "Reading pages",
  extracting: "Finding invoice fields",
  validating: "Checking the numbers",
  saving: "Saving",
  indexing: "Adding to search",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  "waiting to retry": "Waiting to try again",
};

export const SOURCE_LABELS: Record<string, string> = {
  text_pdf: "PDF with a text layer",
  scanned_pdf: "Scanned PDF, read with OCR",
  mixed_pdf: "PDF with text and scanned pages",
  image: "Image, read with OCR",
  spreadsheet: "Spreadsheet",
  csv: "CSV file",
};

export const ISSUE_TITLES: Record<string, string> = {
  MISSING_FIELD: "A field is missing",
  INVALID_NUMBER: "A number is not valid",
  INVALID_DATE: "A date is not valid",
  DATE_AMBIGUOUS: "A date can be read two ways",
  DUE_BEFORE_INVOICE_DATE: "Due date is before the invoice date",
  LINE_ITEM_MISMATCH: "A line does not multiply out",
  SUBTOTAL_MISMATCH: "Lines do not add up to the subtotal",
  TOTAL_MISMATCH: "Subtotal, tax and total do not add up",
  DUPLICATE_INVOICE: "Duplicate invoice",
  POSSIBLE_DUPLICATE: "Possible duplicate",
  LOW_CONFIDENCE: "Low confidence",
  UNGROUNDED_VALUE: "AI value not found in the document",
  OCR_LOW_QUALITY: "Poor scan quality",
  CROSS_DOC_MISMATCH: "Differs from another document",
  EXTRACTOR_DISAGREEMENT: "Two readers disagree",
  NO_INVOICE_DETECTED: "No invoice found",
};

/** What a person can do about a failed document, by error code. */
export const FAILURE_HELP: Record<string, string> = {
  CORRUPT_FILE: "The file is damaged or cut short. Export or download it again from where it came from and upload the new copy.",
  PASSWORD_PROTECTED: "The file is password protected. Remove the password, then upload it again.",
  IMAGE_TOO_LARGE: "The image has more pixels than the server allows. Scan at a lower resolution, or raise the image size limit in the settings and retry.",
  OCR_UNAVAILABLE: "No OCR engine could start. Install Tesseract (or the RapidOCR package) and retry.",
  OCR_FAILED: "The OCR engine failed on a page. Retrying often works.",
  WORKER_LOST: "The server stopped while this was running. Retrying resumes from the last page it saved.",
  TIMEOUT: "Processing took longer than the time limit. Retry, or raise the limit in the settings for very large files.",
  LIMIT_EXCEEDED: "The file is larger or longer than the configured limits.",
  PROCESSING_ERROR: "Something unexpected went wrong. Retry once; if it fails again the details are in the history tab.",
};

export function plural(n: number, one: string, many?: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many ?? one + "s"}`;
}
