# Browsers

A browser is a real Chrome the user owns. It keeps its profile, so logins
survive between tasks, and a person can watch it or take over at any time.

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

A session that is already open keeps working after the 15 minutes are up.
Reconnecting needs a fresh `connect`.

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

## When it goes wrong

- **The address refuses you (401).** The token ran out: run `connect` again.
- **It is `starting`.** `wait shop` first; a fresh browser takes a moment.
- **The page looks logged out.** The site ended the session. Ask the user to log
  in again on the live view.
- **A site blocks automation.** Slow down, and drive the page the way a person
  would. Don't try to hide what you are.
