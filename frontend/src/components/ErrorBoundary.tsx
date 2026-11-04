import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Notice } from "./ui";

interface Props {
  children: ReactNode;
  /** "screen" sits inside the page frame, so the menu stays usable. "app" is the last resort around everything. */
  level?: "screen" | "app";
}

interface State {
  error: Error | null;
}

/**
 * If one screen fails while drawing, React drops the whole tree and the person is left with a blank page.
 * This catches the failure, says what happened and leaves everything outside it working.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("DocIntel: a screen failed to draw.", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.level === "app") {
      return (
        <div className="empty" role="alert">
          <p className="empty-title">DocIntel could not draw this page</p>
          <p className="empty-body">Something unexpected went wrong in the web page. Reloading usually fixes it. Detail: {error.message}</p>
          <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>Reload</button>
        </div>
      );
    }

    return (
      <Notice
        kind="bad"
        title="This screen could not be drawn."
        action={<button type="button" className="btn btn-quiet" onClick={() => this.setState({ error: null })}>Try again</button>}
      >
        Something unexpected happened here, but the rest of DocIntel still works. Pick another page from the menu or try again. Detail: {error.message}
      </Notice>
    );
  }
}
