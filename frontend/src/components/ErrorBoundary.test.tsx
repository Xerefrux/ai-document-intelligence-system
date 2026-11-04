import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

let broken = true;
function Flaky() {
  if (broken) throw new Error("kaboom");
  return <p>All fine</p>;
}

beforeEach(() => {
  broken = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined); // React and the boundary both log the failure on purpose
});
afterEach(() => vi.restoreAllMocks());

describe("error boundary", () => {
  it("shows what went wrong and leaves everything around the failing screen on the page", () => {
    render(
      <div>
        <nav>Menu</nav>
        <ErrorBoundary>
          <Flaky />
        </ErrorBoundary>
      </div>,
    );
    expect(screen.getByText("Menu")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This screen could not be drawn.");
    expect(alert).toHaveTextContent("kaboom");
  });

  it("draws the screen again when asked to try again", () => {
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("All fine")).toBeInTheDocument();
  });

  it("starts clean when it is keyed to another page", () => {
    const { rerender } = render(
      <ErrorBoundary key="search">
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    broken = false;
    rerender(
      <ErrorBoundary key="documents">
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("All fine")).toBeInTheDocument();
  });

  it("offers a reload when it is the last resort around the whole app", () => {
    render(
      <ErrorBoundary level="app">
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
