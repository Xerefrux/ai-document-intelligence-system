"""Open every screen of a running DocIntel server in a real browser and report anything that goes wrong.

The unit tests never draw a page, so this is the check that catches a screen that crashes on real data. (An empty
search once returned invoices without a field the page read, and the whole window went blank.)

It needs Playwright and a browser, which are not project dependencies:

    pip install playwright
    playwright install chromium

Load some data, start the server, then run the script against it:

    docintel --data-dir ./demo-data seed samples
    docintel --data-dir ./demo-data serve            # in another terminal
    python scripts/ui_smoke.py http://127.0.0.1:8000

It only reads. It opens every menu page, every document and every record, then types into the search box and
uses its filters. It exits with 1 if a screen drew nothing, showed the "could not be drawn" message, raised a script
error, logged a console error or got a server error. A server that asks for an API key is not supported.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout


def api(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}/api{path}", timeout=30) as res:
        return json.load(res)


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    docs = api(base, "/documents?limit=200")["items"]
    recs = api(base, "/records?limit=200")["items"]
    first = next((r for r in recs if r["supplier_name"] and r["invoice_number"]), None)
    word = first["supplier_name"].split()[0] if first else "invoice"
    number = first["invoice_number"] if first else "1"

    seen: list[str] = []
    inflight = {"n": 0}
    visited = 0
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page: Page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: seen.append(f"script error: {e}"))
        page.on("console", lambda m: seen.append(f"console error: {m.text}") if m.type == "error" else None)
        page.on("response", lambda r: seen.append(f"server error {r.status} for {r.url}") if r.status >= 500 else None)
        page.on("request", lambda r: inflight.__setitem__("n", inflight["n"] + 1))
        page.on("requestfinished", lambda r: inflight.__setitem__("n", inflight["n"] - 1))
        page.on("requestfailed", lambda r: inflight.__setitem__("n", inflight["n"] - 1))

        def settle() -> None:
            page.wait_for_timeout(500)  # the search box waits 300 ms before it asks the server
            for _ in range(60):
                if inflight["n"] <= 0:
                    break
                page.wait_for_timeout(100)
            page.wait_for_timeout(250)

        def load(route: str) -> None:
            """A fresh load of the app at a route, like pasting the address into a new tab."""
            page.goto("about:blank")
            seen.clear()
            page.goto(f"{base}/#{route}")
            settle()

        def check(label: str) -> bool:
            """Report what the screen shows now. True when the whole window was blank, which also reloads the app."""
            nonlocal visited
            visited += 1
            problems = list(seen)
            seen.clear()
            state = page.evaluate(
                """() => {
                    const root = document.getElementById('root');
                    const main = document.getElementById('main');
                    return { blank: !root || root.childElementCount === 0, text: main ? main.innerText.trim().length : 0 };
                }"""
            )
            if state["blank"]:
                problems.append("the window is blank")
            elif not state["text"]:
                problems.append("the page area is empty")
            if page.locator("[role=alert]", has_text=re.compile("could not (be )?draw")).count():
                problems.append("the screen says it could not be drawn")
            failures.extend(f"{label}: {t}" for t in problems)
            print(f"{'ok     ' if not problems else 'PROBLEM'} {label}" + "".join(f"\n          {t[:300]}" for t in problems))
            if state["blank"]:  # load the app again, so one crash does not hide what comes after it
                load("/")
            return bool(state["blank"])

        def field(label: str) -> Locator:
            return page.locator("label", has_text=re.compile(f"^{label}"))

        def open_search() -> None:
            load("/search")
            page.get_by_role("button", name=re.compile("^Filters")).click()
            settle()

        load("/")
        check("/ (first load)")

        for label in ("Upload", "Documents", "To review", "Search", "Compare", "Overview"):
            seen.clear()
            page.locator("nav.spine").get_by_role("link", name=re.compile(f"^{label}")).first.click()
            settle()
            check(f"menu: {label}")

        for d in docs:
            seen.clear()
            page.evaluate("h => { window.location.hash = h; }", f"/documents/{d['id']}")  # the same thing a click on a link does
            settle()
            check(f"/documents/{d['id'][:8]} {d['filename']}")
        for r in recs:
            seen.clear()
            page.evaluate("h => { window.location.hash = h; }", f"/records/{r['id']}")
            settle()
            check(f"/records/{r['id'][:8]} {r['invoice_number'] or 'no number'}")

        for route in ("/search", f"/search?q={word}", f"/search?q={number}", f"/search?q={word}&mode=keyword", f"/search?q={word}&mode=semantic",
                      "/search?mode=semantic", "/search?mode=keyword", f"/search?supplier={word}", "/search?status=needs_review", "/search?type=xlsx",
                      "/search?issues=true", "/search?issues=false", "/search?min=100&max=5000", "/search?from=2025-01-01&to=2026-12-31"):
            load(route)
            check(route)

        def search_box_and_filters() -> None:
            open_search()
            box = page.locator("input#q")
            for text in (word, number, 'a "quoted" phrase', "AND OR NOT NEAR(", "50% off_ and more", "*", "(", "\\", "emoji \U0001f600 text", "x" * 300, ""):
                seen.clear()
                box.fill(text)
                settle()
                if check(f"typed {text[:24]!r}"):
                    open_search()
            for name in ("Exact words", "Similar meaning", "Both"):
                seen.clear()
                page.get_by_role("button", name=name, exact=True).click()
                settle()
                if check(f"mode: {name}"):
                    open_search()
            for label, value in (("Supplier", word), ("Supplier", "%_"), ("Date from", "2025-01-01"), ("Date to", "2026-12-31"), ("Total at least", "1"), ("Total at most", "99999")):
                seen.clear()
                field(label).locator("input").fill(value)
                settle()
                if check(f"filter {label} = {value}"):
                    open_search()
            for label, value in (("State", "needs_review"), ("State", "ok"), ("File type", "pdf"), ("File type", "xlsx"), ("Problems", "true"), ("Problems", "false")):
                seen.clear()
                field(label).locator("select").select_option(value)
                settle()
                if check(f"filter {label} = {value}"):
                    open_search()
            seen.clear()
            page.get_by_role("button", name="Clear filters").click()
            settle()
            check("filters cleared")

        try:
            search_box_and_filters()
        except PlaywrightTimeout as exc:  # the search page itself did not come up, so there is nothing to type into
            failures.append(f"search box and filters: could not be used ({str(exc).splitlines()[0]})")
            print(f"PROBLEM search box and filters could not be used: {str(exc).splitlines()[0]}")
        browser.close()

    print(f"\n{visited} screens and actions checked, {len(failures)} problem(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
