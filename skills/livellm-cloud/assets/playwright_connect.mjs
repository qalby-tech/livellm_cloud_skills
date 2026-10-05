// Drive a LiveLLM Camoufox browser with Playwright in Node.
//
//   python3 scripts/llc.py connect shop --tool cdp > connect.json
//   node assets/playwright_connect.mjs connect.json https://example.com
//
// A Camoufox browser (Firefox-based) takes exactly the Playwright version its
// connect answer names (playwright.version, e.g. 1.62): any other is refused.
// This script checks that first and prints the npm line to run. The address
// and the header come from connect; both stop working about 15 minutes after
// it ran, so ask again rather than keeping them.
//
// Work in the browser's own context (browser.contexts()[0]): it holds the
// cookies and sign-ins. Open pages there, never with browser.newPage(); a
// context of your own needs { viewport: null }. page.evaluate runs apart from
// the page's scripts; start the script with "mw:" to run it in the page itself.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const [file, url] = process.argv.slice(2);
if (!file || !url) {
  console.error("usage: node playwright_connect.mjs connect.json https://example.com");
  process.exit(2);
}

const info = JSON.parse(readFileSync(file, "utf8"));
const pw = info.playwright;
if (!pw) {
  console.error(info.cdp
    ? "This browser runs Chrome: use assets/cdp_connect.mjs"
    : "This answer has no Playwright address: run llc.py connect ID --tool cdp for a browser");
  process.exit(2);
}

// The Playwright installed here, or null.
function installedPlaywright() {
  try {
    return createRequire(import.meta.url)("playwright/package.json").version;
  } catch {
    return null;
  }
}

const want = String(pw.version ?? "");
const have = installedPlaywright();
const sameMinor = (a, b) => a.split(".").slice(0, 2).join(".") === b.split(".").slice(0, 2).join(".");
if (want && (!have || !sameMinor(have, want))) {
  console.error(`This browser takes Playwright ${want}; ${have ? `${have} is installed.` : "none is installed where this script can see it."}`);
  // Node looks for playwright next to this script, not in the folder you run it from.
  console.error(`Run: npm i playwright@${want}` + (have ? "" : " in this script's folder, or copy the script into your project"));
  process.exit(2);
}

const { firefox } = await import("playwright");
const browser = await firefox.connect(pw.url, { headers: pw.headers ?? {} });
// The browser's own context keeps its sign-ins; a context of your own needs
// { viewport: null }, or its pages open at the wrong size.
const context = browser.contexts()[0] ?? (await browser.newContext({ viewport: null }));
const page = await context.newPage();

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  console.log(JSON.stringify({ title: await page.title(), url: page.url() }, null, 2));
} finally {
  // Close what you opened; leave the context and the browser running so the
  // user's sessions stay logged in.
  await page.close();
}
// The connection keeps Node running: end it without closing the browser.
process.exit(0);
