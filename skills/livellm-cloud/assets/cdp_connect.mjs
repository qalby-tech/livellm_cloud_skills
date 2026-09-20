// Drive a LiveLLM browser with Playwright in Node.
//
//   python3 scripts/llc.py connect shop --tool cdp > connect.json
//   node assets/cdp_connect.mjs connect.json https://example.com
//
// Needs playwright (npm i playwright). The address and the header come from
// connect; both stop working about 15 minutes after it ran, so ask again
// rather than keeping them.

import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const [file, url] = process.argv.slice(2);
if (!file || !url) {
  console.error("usage: node cdp_connect.mjs connect.json https://example.com");
  process.exit(2);
}

const info = JSON.parse(readFileSync(file, "utf8"));
const browser = await chromium.connectOverCDP(info.cdp.url, { headers: info.cdp.headers });
const context = browser.contexts()[0] ?? (await browser.newContext());
const page = await context.newPage();

try {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  console.log(JSON.stringify({ title: await page.title(), url: page.url() }, null, 2));
} finally {
  // Close what you opened; leave the browser itself running so the user's
  // sessions stay logged in.
  await page.close();
}
