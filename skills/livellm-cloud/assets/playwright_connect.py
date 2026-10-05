#!/usr/bin/env python3
"""Drive a LiveLLM Camoufox browser with Playwright.

    python3 scripts/llc.py connect shop --tool cdp > connect.json
    python3 assets/playwright_connect.py connect.json https://example.com

A Camoufox browser (Firefox-based) takes exactly the Playwright version its
connect answer names (playwright.version, e.g. 1.62): any other is refused.
This script checks that first and prints the pip line to run. The address and
the header come from connect; both stop working about 15 minutes after it ran,
so ask again rather than keeping them.

Work in the browser's own context (browser.contexts[0]): it holds the
cookies and sign-ins. Open pages there, never with browser.new_page(); a
context of your own needs no_viewport=True. page.evaluate runs apart from the
page's scripts; start the script with "mw:" to run it in the page itself.
"""

import json
import sys


def installed_playwright():
    """The Playwright version installed here, or None."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("playwright")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass
    try:
        import playwright
        return getattr(playwright, "__version__", None)
    except ImportError:
        return None


def same_minor(have, want):
    """1.62.1 runs against 1.62; 1.63.0 does not."""
    return have.split(".")[:2] == want.split(".")[:2]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    info = json.loads(open(sys.argv[1]).read())
    url = sys.argv[2]
    pw = info.get("playwright")
    if not pw:
        if info.get("cdp"):
            print("This browser runs Chrome: use assets/cdp_connect.py", file=sys.stderr)
        else:
            print("This answer has no Playwright address: run llc.py connect ID --tool cdp for a browser", file=sys.stderr)
        return 2

    want = str(pw.get("version") or "")
    have = installed_playwright()
    if want and (not have or not same_minor(have, want)):
        print(f"This browser takes Playwright {want}; " + (f"{have} is installed." if have else "none is installed."),
              file=sys.stderr)
        print(f'Run: pip install "playwright=={want}.*"', file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.firefox.connect(pw["url"], headers=pw.get("headers") or {})
        # The browser's own context keeps its sign-ins; a context of your own
        # needs no_viewport=True, or its pages open at the wrong size.
        context = browser.contexts[0] if browser.contexts else browser.new_context(no_viewport=True)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            print(json.dumps({"title": page.title(), "url": page.url}, indent=2))
        finally:
            # Close what you opened; leave the context and the browser running
            # so the user's sessions stay logged in.
            page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
