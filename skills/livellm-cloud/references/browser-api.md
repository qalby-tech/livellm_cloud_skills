# Browser API

A Browser API is one address in front of several browsers. Clients call the
address; the Browser API picks a browser, opens the page, and answers with the
text, the HTML, a screenshot or extracted values. Add browsers to handle more
work: the address and the clients stay the same. In the console it is made
under New resource → Apps → Browser API.

Use it when the user wants many pages fetched or scraped at once, or one
address to hand to a service. For a single browser driven step by step with
Playwright, use the browser itself (`references/browsers.md`).

## Create one

```
python3 scripts/llc.py ls --type browser
python3 scripts/llc.py browser-api create scrapers --browsers agent-1,agent-2 --yes
python3 scripts/llc.py wait scrapers
```

- `--browsers a,b`: only these browsers.
- `--all`: every browser in the workspace, including ones made later.
- `--remote office=wss://…`: a browser running somewhere else, by its CDP
  address. Repeat it for more.

A Browser API always has browsers; create the browsers first. A workspace
browser belongs to at most one Browser API. When one is already in another,
the answer is a 422 naming it: ask the user before taking it out there.

`create controller --json FILE --yes` takes the same settings as a file:
`{"id": "scrapers", "browsers": ["agent-1", "agent-2"]}`.

## Which browsers it drives

```
python3 scripts/llc.py browser-api show scrapers
python3 scripts/llc.py browser-api add scrapers agent-3
python3 scripts/llc.py browser-api remove scrapers agent-3
```

`show` lists its browsers and, under `answering`, each one's open tabs. `add`
and `remove` change one browser and leave the rest as they are. A browser that
is taken out stops answering, and its sessions end.

## Call it

```
python3 scripts/llc.py connect scrapers --tool api
```

`api.url` is the address and `api.headers` the header that opens it, good for
15 minutes. For a client that runs longer, send a workspace API key instead:
`Authorization: Bearer llc_…`. Never put the key in code or in a page.

```
curl -X POST "$URL/content" -H "$HEADER" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "text"}'
```

The address also answers under `/parser` (`$URL/parser/content`); both are the
same.

| Call | Does |
|---|---|
| `POST /content` | page text, HTML or a screenshot (`output_action`: `text`, `html`, `screenshot`, `screenshot_full`) |
| `POST /interact` | click, type and scroll, then return the page |
| `POST /attribute` | values picked out by CSS or XPath selectors |
| `POST /search`, `/search_news`, `/search_images`, `/search_videos` | search results |
| `POST /start_session`, `DELETE /end_session` | keep one tab across calls |
| `GET /browsers` | its browsers and their open tabs |

## Which browser answers

Every call lands on one browser, in one of three ways:

1. **No browser named**: the one with the fewest open tabs. Nothing waits and
   nothing is refused; a browser that can't be reached is skipped.
2. **`X-Session-Id`**: the browser the session started on. Send that header
   alone.
3. **`/browsers/<name>/…` or `X-Browser-Id: <name>`**: that browser.
   `POST /browsers/agent-2/content` is `POST /content` on agent-2.

Every answer carries `X-Browser-Id` naming the browser that answered.

## Sessions

A session is one tab that stays open between calls: logins, forms, pages
clicked through in steps.

```
curl -X POST "$URL/start_session" -H "$HEADER"
# {"session_id": "s_81f", "browser_id": "agent-2", ...}
curl -X POST "$URL/interact" -H "$HEADER" -H "X-Session-Id: s_81f" -H "Content-Type: application/json" -d '{...}'
curl -X DELETE "$URL/end_session" -H "$HEADER" -H "X-Session-Id: s_81f"
```

To start the session on a chosen browser, add `X-Browser-Id` to
`start_session`. End sessions you started. A restart of the Browser API ends
them all: start a new one.

## When it goes wrong

| Answer | Means | Do |
|---|---|---|
| 401 | The token ran out | `connect` again, or use the workspace key |
| 404 | No browser of that name here, or the session is gone | `browser-api show`; start a new session |
| 409 | The browser named contradicts the session's browser | Send `X-Session-Id` alone |
| 400 | The path names one browser and `X-Browser-Id` another | Name it once |
| 502 | The named browser can't be reached | `wait` for it, or leave the name out |
| 503 | No browsers, or none can be reached | `browser-api show`; tell the user |
