# Browsers

A browser is a real Chrome (or Camoufox, below) the user owns. It keeps its
profile, so logins survive between tasks, and a person can watch it or take
over at any time. In the console, browsers are made under New resource → Apps
→ Browser and listed under Apps.

## Reuse before you create

```
python3 scripts/llc.py ls --type browser
```

A browser that already has the user's session for a site is worth far more than
a fresh one. Create a second browser only when the user wants a separate
identity, or when two tasks must run at once.

## Create one

`browser.json`:

```json
{ "id": "shop", "cpu": "2", "memory": "4Gi", "storage": "4Gi" }
```

```
python3 scripts/llc.py create browser --json browser.json --yes
python3 scripts/llc.py wait shop
```

Ids are short and lowercase; the id shows up in the browser's addresses.

## Chrome or Camoufox

A browser runs one of two engines, chosen when it is made:

- **Chrome** (the default): driven over CDP, takes extensions.
- **Camoufox**: Firefox-based. Pick it when a site blocks Chrome as a bot or
  says you use a VPN when you don't; otherwise use Chrome. It is driven with
  Playwright 1.62 only, takes no extensions (an ad blocker, uBlock Origin, is
  built in), and is offered only where `llc.py engines` lists it.

```
python3 scripts/llc.py create browser --json browser.json --engine camoufox --yes
```

(or `"engine": "camoufox"` in the file). **The engine can't change later**: a
422 `engine_fixed` says so. For the other engine, make a new browser and add
the old one's cookies to it (`references/profiles.md`); profiles themselves
move only between browsers of one engine. `ls` shows `"engine": "camoufox"`
on a Camoufox browser; a browser without it is Chrome.

Language, time zone, location, proxies and the live view work the same on
both. A Camoufox browser keeps the same fingerprint (fonts, graphics, audio)
across restarts; an update to a new Firefox major version draws a new one,
which sites can see as a new device.

## Language, time zone and location

For a site that should see a Russian visitor in Moscow, add to the file:

```json
{ "id": "shop", "locale": "ru-RU", "timezone": "Europe/Moscow" }
```

- `locale` sets the browser's language, what pages read from it and the
  `Accept-Language` it sends. `languages` (optional) is that order in full,
  up to 6, the first one `locale`; left out, it is `ru-RU, ru, en-US, en`.
- `timezone` is an IANA name (`Europe/Berlin`, `America/New_York`, `UTC`).
- `geolocation`: `{"mode": "off"}` refuses location to every site;
  `{"mode": "fixed", "latitude": 55.75, "longitude": 37.62}` reports that place
  (`accuracy` in metres, default 100). Left out, sites ask as usual.
- `llc.py locales` lists the languages offered and the time zone names; any
  other is refused (422).

To change them later, `set shop --json lang.json --yes` with
`{"browser": {"locale": "de-DE", "timezone": "Europe/Berlin"}}`. **Changing
these restarts the browser**: its tabs close; the profile, its sign-ins and
the addresses are kept. Ask first. `""` (or `[]` for `languages`,
`{"mode": "prompt"}` for `geolocation`) goes back to the default.

A proxy's country doesn't set these: set them to match it yourself.

## Proxies and profiles

- Through the user's own proxies, mobile ones included, switched by hand, on a
  timer or per session: `references/proxies.md`.
- Snapshots to switch back to, a profile exported to a file or imported from
  one, copied to another browser, or cookies added: `references/profiles.md`.

## Drive it

```
python3 scripts/llc.py connect shop --tool cdp
```

You get:

- `cdp.url`: the automation address, a websocket.
- `cdp.headers`: the header that opens it, good for 15 minutes.
- `api.url`: the same address serves the browser's own API for sessions,
  cookies and extensions, under the same header.

Connect straight to `cdp.url`. Don't look for a discovery page: there isn't one
on that address. `assets/cdp_connect.py` and `assets/cdp_connect.mjs` are
working examples for Playwright in Python and Node.

### A Camoufox browser

The same `connect ID --tool cdp` answers `playwright` instead of `cdp`:

- `playwright.url` and `playwright.headers`: the address and the header,
  good for 15 minutes;
- `playwright.version`: the Playwright it takes, `1.62`. Any other version is
  refused (428): `pip install "playwright==1.62.*"` or `npm i playwright@1.62`.

```python
b = p.firefox.connect(info["playwright"]["url"], headers=info["playwright"]["headers"])
page = b.contexts[0].new_page()
```

- Work in `b.contexts[0]`: it holds the cookies and sign-ins. Never call
  `b.new_page()`. A context of your own needs `no_viewport=True`
  (`viewport: null` in Node), or its pages open at the wrong size.
- Close your pages; never close the context or the browser.
- `page.evaluate` runs apart from the page's own scripts, so they can't see
  it. Start the script with `mw:` to run it in the page itself, when you need
  the page's own variables.
- A context of your own with its own `proxy` goes around the browser's
  proxies; use the browser's (`references/proxies.md`).

`assets/playwright_connect.py` and `assets/playwright_connect.mjs` are working
examples; they check the Playwright version first and print the line to
install the right one. `connect ID --tool cdp --env` exports `LIVELLM_PLAYWRIGHT_URL` and
`LIVELLM_PLAYWRIGHT_VERSION` for a Camoufox browser.

A session that is already open keeps working after the 15 minutes are up.
Reconnecting needs a fresh `connect`.

## Several browsers behind one address

To spread many pages over several browsers, or give a service one address that
keeps working as browsers are added, put them in a Browser API:
`references/browser-api.md`. One Browser API holds Chrome and Camoufox
browsers together.

## Let the user watch or step in

```
python3 scripts/llc.py connect shop --tool view
```

`liveView.url` opens the browser's screen in a tab. Send it to the user when:

- the site asks for a login code, a password or a CAPTCHA;
- something needs a payment or a legal agreement;
- you are unsure whether to click something that can't be undone.

Say plainly what you need them to do, then wait and check the page again. Never
try to answer a CAPTCHA yourself.

## Habits that pay off

- Wait for what you need on the page, not for a number of seconds.
- One tab per task; close tabs you opened.
- Never type the user's password. Ask them to log in on the live view once; the
  profile keeps the session afterwards.
- Files a site hands you land inside the browser. Read them through the page, or
  upload them somewhere the user can reach.
- The browser has its own screen password in its settings; you never need it.

## Where it runs

Leave `placement` out and LiveLLM picks the host. To choose, put
`"placement": {"strategy": "region", "region": "<r>"}` or
`{"strategy": "host", "host": "<id>"}` in the file; `llc.py hosts` lists ids and regions.
Changing it (`set` with `{"browser": {"placement": …}}`, `null` for automatic) restarts
the browser there; one pinned to a host waits while that host is down.

## When it goes wrong

- **The address refuses you (401).** The token ran out: run `connect` again.
- **It is `starting`.** `wait shop` first; a fresh browser takes a moment.
- **The page looks logged out.** The site ended the session. Ask the user to log
  in again on the live view.
- **A site blocks automation.** Slow down, and drive the page the way a person
  would. Never try to get past a block or a CAPTCHA on your own. A site that
  blocks Chrome outright ("you use a VPN", a bot page) may let a Camoufox
  browser through, often with a CAPTCHA for the user to solve on the live
  view: that is the user's call, so ask before making one.
- **`cdp_connect` says "This browser runs Camoufox".** Use
  `assets/playwright_connect.py`. A 428 from the address is the wrong
  Playwright version: install the one `playwright.version` names.
