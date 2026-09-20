#!/usr/bin/env python3
"""Drive a LiveLLM browser with Playwright.

    python3 scripts/llc.py connect shop --tool cdp > connect.json
    python3 assets/cdp_connect.py connect.json https://example.com

Needs playwright (pip install playwright). The address and the header come from
connect; both stop working about 15 minutes after it ran, so ask again rather
than keeping them.
"""

import json
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    info = json.loads(open(sys.argv[1]).read())
    url = sys.argv[2]

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(info["cdp"]["url"], headers=info["cdp"]["headers"])
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            print(json.dumps({"title": page.title(), "url": page.url}, indent=2))
        finally:
            # Close what you opened; leave the browser itself running so the
            # user's sessions stay logged in.
            page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
