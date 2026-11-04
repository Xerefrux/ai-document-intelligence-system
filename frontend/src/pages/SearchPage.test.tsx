import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Route } from "../hooks";
import SearchPage, { matchedBy } from "./SearchPage";

vi.mock("../api", () => ({ api: { get: vi.fn() } }));

const route: Route = { path: ["search"], query: new URLSearchParams() };

const record = (over: Record<string, unknown>) => ({
  type: "record", id: "r1", document_id: "d1", filename: "layout-2-BD202508018.pdf", kind: "invoice", invoice_number: "BD202508018",
  invoice_date: "2025-01-24", supplier_name: "Summit Software GmbH", customer_name: "Acme", total: 290.58, currency: "GBP",
  review_status: "ok", confidence: 0.93, page: 1, issue_count: 0, ...over,
});

const answer = (records: unknown[], over: Record<string, unknown> = {}) => ({
  query: "", mode: "hybrid", records, pages: [], total_records: records.length, semantic_available: true, semantic_kind: "fuzzy", took_ms: 1.2, ...over,
});

beforeEach(() => vi.mocked(api.get).mockReset());

describe("the search page", () => {
  // This is the answer the server used to give for an empty search once any invoice existed: no score, no matched_by, no snippet.
  // Reading matched_by.length on it threw, React dropped the whole tree, and the person saw a blank white page.
  it("lists invoices when the search is empty and the server sends no match details", async () => {
    vi.mocked(api.get).mockResolvedValue(answer([record({}), record({ id: "r2", invoice_number: null, supplier_name: null, total: null, currency: null, invoice_date: null })]));
    render(<SearchPage route={route} />);
    expect(await screen.findByText("BD202508018")).toBeInTheDocument();
    expect(screen.getByText("No number")).toBeInTheDocument();
    expect(screen.getAllByText("layout-2-BD202508018.pdf")).toHaveLength(2);
    expect(screen.queryByText(/matched by/)).toBeNull();
  });

  it("says how an invoice was found when the server does send match details", async () => {
    vi.mocked(api.get).mockResolvedValue(answer([record({ matched_by: ["keyword", "semantic"], score: 0.03, snippet: "Paid to \x02Summit\x03 Software" })], { query: "summit" }));
    render(<SearchPage route={route} />);
    expect(await screen.findByText(/matched by words and meaning/)).toBeInTheDocument();
    expect(document.querySelector("mark")?.textContent).toBe("Summit");
  });

  it("shows the empty state, not an error, when nothing is stored", async () => {
    vi.mocked(api.get).mockResolvedValue(answer([]));
    render(<SearchPage route={route} />);
    expect(await screen.findByText("No matches")).toBeInTheDocument();
  });
});

describe("matchedBy", () => {
  it("names the ways an invoice matched, and says nothing when there were none or the field is missing", () => {
    expect(matchedBy(["keyword"])).toBe(", matched by words");
    expect(matchedBy(["semantic"])).toBe(", matched by meaning");
    expect(matchedBy(["keyword", "semantic"])).toBe(", matched by words and meaning");
    expect(matchedBy([])).toBe("");
    expect(matchedBy(undefined)).toBe("");
  });
});
