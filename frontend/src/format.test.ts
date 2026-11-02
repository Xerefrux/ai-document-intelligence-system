import { describe, expect, it } from "vitest";
import { fmtAgo, fmtAmount, fmtBytes, fmtDate, fmtDuration, fmtPercent, plural } from "./format";

describe("formatting helpers", () => {
  it("formats amounts with two decimals and no value as empty", () => {
    expect(fmtAmount(1234.5)).toBe("1,234.50");
    expect(fmtAmount(0)).toBe("0.00");
    expect(fmtAmount(null)).toBe("");
    expect(fmtAmount(undefined)).toBe("");
    expect(fmtAmount(Number.NaN)).toBe("");
  });

  it("shows an invoice date as the day printed, whatever the viewer's time zone", () => {
    expect(fmtDate("2026-03-04").replace(/ /g, " ")).toBe("4 Mar 2026");
    expect(fmtDate("2026-12-31").replace(/ /g, " ")).toBe("31 Dec 2026");
    expect(fmtDate(null)).toBe("");
    expect(fmtDate("not a date")).toBe("not a date");
  });

  it("formats sizes and durations in plain units", () => {
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fmtDuration(400)).toBe("400 ms");
    expect(fmtDuration(2500)).toBe("2.5 s");
    expect(fmtDuration(125000)).toBe("2 min 5 s");
  });

  it("says how long ago something happened", () => {
    const now = Date.parse("2026-11-01T12:00:00Z");
    expect(fmtAgo("2026-11-01T11:59:58Z", now)).toBe("just now");
    expect(fmtAgo("2026-11-01T11:59:00Z", now)).toBe("1 min ago");
    expect(fmtAgo("2026-11-01T09:00:00Z", now)).toBe("3 h ago");
    expect(fmtAgo(null, now)).toBe("");
  });

  it("formats percentages and plurals", () => {
    expect(fmtPercent(0.256)).toBe("26%");
    expect(fmtPercent(0.256, 1)).toBe("25.6%");
    expect(fmtPercent(null)).toBe("");
    expect(plural(1, "file")).toBe("1 file");
    expect(plural(3, "file")).toBe("3 files");
  });
});
