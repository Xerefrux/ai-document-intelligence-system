import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Amount, Confidence, Highlight, ProgressBar, Snippet } from "./ui";

describe("small components", () => {
  it("renders search hits as <mark> elements and never as HTML", () => {
    const { container } = render(<Snippet text={"total \x02<b>1,234</b>\x03 due"} />);
    const marks = container.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe("<b>1,234</b>");
    expect(container.querySelector("b")).toBeNull();
  });

  it("highlights a phrase ignoring case and extra spaces, and survives characters that are special in a pattern", () => {
    const { container } = render(<Highlight text="Paid to Alpha   Office Solutions (UK)" needle="alpha office" />);
    expect(container.querySelector("mark")?.textContent).toBe("Alpha   Office");
    const odd = render(<Highlight text="a (b" needle="(b" />);
    expect(odd.container.querySelector("mark")?.textContent).toBe("(b");
  });

  it("shows confidence in words for screen readers", () => {
    render(<Confidence value={0.92} />);
    expect(screen.getByText("Confidence high")).toBeInTheDocument();
    render(<Confidence value={0} />);
    expect(screen.getByText("Confidence no value")).toBeInTheDocument();
  });

  it("exposes progress to assistive technology and clamps it", () => {
    render(<ProgressBar value={1.7} label="Reading pages" />);
    const bar = screen.getByRole("progressbar", { name: "Reading pages" });
    expect(bar.getAttribute("aria-valuenow")).toBe("100");
  });

  it("shows an amount with its currency, or says there is none", () => {
    const { container } = render(<Amount value={1234.5} currency="BDT" />);
    expect(container.textContent).toBe("1,234.50 BDT");
    const none = render(<Amount value={null} />);
    expect(none.container.textContent).toBe("none");
  });
});
