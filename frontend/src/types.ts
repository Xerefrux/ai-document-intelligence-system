export type DocStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";
export type DocReview = "pending" | "ok" | "needs_review";
export type RecReview = "ok" | "needs_review" | "corrected" | "approved";
export type Severity = "error" | "warning" | "info";

export interface IssueCounts { error: number; warning: number; info: number }

export interface Doc {
  id: string;
  filename: string;
  file_type: "pdf" | "image" | "xlsx" | "xls" | "csv";
  size_bytes: number;
  status: DocStatus;
  stage: string | null;
  page_count: number | null;
  pages_done: number;
  progress: number | null;
  record_count: number;
  review_status: DocReview;
  source_kind: string | null;
  ocr_used: boolean;
  ocr_engine: string | null;
  ocr_confidence: number | null;
  error_code: string | null;
  error_message: string | null;
  duplicate_of: string | null;
  batch_id: string | null;
  processing_ms: number | null;
  created_at: string;
  completed_at: string | null;
  sha256: string;
  issues: IssueCounts;
  job?: { id: string; status: string; attempts: number; max_attempts: number; stage: string | null; run_after: string | null; error_code: string | null };
}

export interface DocList {
  items: Doc[];
  total: number;
  limit: number;
  offset: number;
  status_counts: Record<DocStatus, number>;
  needs_review: number;
}

export interface RecordRow {
  id: string;
  document_id: string;
  filename?: string;
  record_index?: number;
  kind: "invoice" | "ledger_row";
  invoice_number: string | null;
  invoice_date: string | null;
  supplier_name: string | null;
  customer_name: string | null;
  total: number | null;
  currency: string | null;
  review_status: RecReview;
  confidence: number | null;
  page?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  sheet_name?: string | null;
  row_ref?: string | null;
  method?: string;
  issue_count?: number;
  issues?: { code: string; severity: Severity; message: string }[];
}

export interface Issue {
  id: number;
  record_id: string | null;
  document_id: string;
  code: string;
  severity: Severity;
  field: string | null;
  message: string;
  details: Record<string, unknown>;
}

export interface FieldValue {
  value: string | number | null;
  confidence: number;
  evidence: string | null;
  page: number | null;
  source: "rules" | "llm" | "both" | "manual" | string;
  note: string | null;
}

export interface LineItem {
  id?: number;
  position?: number;
  description: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
  confidence?: number | null;
}

export interface Correction { id: number; field: string; old_value: string | null; new_value: string | null; note: string | null; created_at: string }

export interface RecordDetail extends RecordRow {
  fields: Record<string, FieldValue>;
  line_items: LineItem[];
  issues: Issue[];
  corrections: Correction[];
  document: { id: string; filename: string; file_type: Doc["file_type"]; page_count: number | null; source_kind: string | null } | null;
  version: number;
  updated_at: string | null;
}

export interface DocEvent { id: number; stage: string; level: "info" | "warning" | "error"; message: string; duration_ms: number | null; created_at: string; job_id: string | null }
export interface PageInfo { page_no: number; source: string; char_count: number; ocr_confidence: number | null; label: string | null; warnings: string[] }

export interface DocDetail extends Omit<Doc, "issues"> {
  records: RecordRow[];
  records_total: number;
  issues: Issue[];
  events: DocEvent[];
  pages: PageInfo[];
  corrections: number;
}

export interface PageText { page_no: number; source: string; label: string | null; ocr_confidence: number | null; warnings: string[]; text: string }
export interface TextWindow { document_id: string; from: number; to: number; page_count: number | null; pages: PageText[] }

export interface SearchRecord extends RecordRow {
  score: number;
  matched_by: ("keyword" | "semantic")[];
  snippet: string | null;
  similarity?: number;
}
export interface SearchPage { type: "page"; id: string; document_id: string; filename: string; page_no: number; label: string | null; snippet: string; score: number }
export interface SearchResult {
  query: string;
  mode: string;
  records: SearchRecord[];
  pages: SearchPage[];
  total_records: number;
  semantic_available: boolean;
  semantic_kind: "fuzzy" | "semantic" | null;
  note?: string;
  took_ms: number;
}

export interface Stats {
  documents: { total: number; by_status: Record<DocStatus, number>; by_review: Record<string, number>; no_records: number; success_rate: number | null; failure_rate: number | null };
  records: { total: number; by_review: Record<RecReview, number> };
  issues: { by_severity: IssueCounts; top_codes: { code: string; count: number }[] };
  corrections: number;
  processing: { samples: number; median_seconds: number | null; p95_seconds: number | null; documents_last_hour: number; pages_last_hour: number };
  queue: { queued: number; running: number; succeeded: number; failed: number; cancelled: number; oldest_queued_seconds: number };
  workers: { id: string; pid: number; current_job: string | null; jobs_done: number; jobs_failed: number; alive: boolean }[];
}

export interface Health {
  status: "ok" | "degraded" | "down";
  version: string;
  queue: { queued: number; running: number; oldest_queued_seconds: number };
  workers: { alive: number; embedded_threads: number };
  ocr: { tesseract: boolean; engine: string };
  llm: string;
  auth_required: boolean;
  message: string | null;
}

export interface Config {
  version: string;
  database: string;
  auth_required: boolean;
  max_upload_mb: number;
  max_files_per_request: number;
  max_request_mb: number;
  max_pdf_pages: number;
  workers: number;
  ocr_engine: string;
  tesseract_available: boolean;
  extraction_mode: string;
  llm_provider: string;
  llm_enabled: boolean;
  embedding_provider: string;
  amount_tolerance: number;
}

export interface IssueRow { id: number; code: string; severity: Severity; field: string | null; message: string; record_id: string | null; document_id: string; filename: string; invoice_number: string | null; supplier_name: string | null; review_status: RecReview | null }
export interface IssueList { items: IssueRow[]; total: number; by_severity: IssueCounts; by_code: Record<string, number> }

export type PairStatus = "MATCH" | "MISMATCH" | "MISSING_RIGHT" | "MISSING_LEFT";
export interface Side { id: string; document_id: string; filename: string; invoice_number: string | null; supplier_name: string | null; invoice_date: string | null; total: number | null; subtotal: number | null; tax: number | null; currency: string | null }
export interface Reconciliation {
  id: string;
  mode: string;
  created_at: string;
  summary: Record<PairStatus | "total", number>;
  items: { key: string | null; status: PairStatus; left: Side | null; right: Side | null; diffs: { field: string; left: unknown; right: unknown; difference?: number }[] }[];
}

export interface UploadResultItem {
  filename: string;
  status: "queued" | "duplicate" | "rejected";
  document_id?: string;
  code?: string;
  message?: string;
  warnings?: string[];
}
export interface UploadResponse { batch_id: string; queued: number; duplicate: number; rejected: number; items: UploadResultItem[] }
