# Browser proxies

A browser can send its traffic through proxies the user brings: HTTP, HTTPS or
SOCKS5, with or without a login, mobile proxies included. It can switch between
them by hand, on a timer, or each time a Browser API session starts.

## Ask first

Before you change a browser's proxies (set, clear or rotate), ask the user and
wait for their agreement: it changes where the browser's traffic goes and the
address sites see. `proxy remove`, and a create or `set` that carries proxy
settings, count too. Use a proxy only when the user asked for one, with the
addresses and logins the user gave you.

`set`, `clear`, `remove` and `rotate`, and a create or `set` that carries proxy
settings, need what any change to the browser needs: Create (on a browser this
agent made) or Manage everything. Reading how a browser goes out (`proxy show`)
needs nothing extra.

## Set the proxies

`proxy.json`:

```json
{
  "upstreams": [
    {"name": "home", "server": "http://proxy.example.com:8080"},
    {"name": "mobile", "server": "socks5://m.example.net:1080",
     "username": "user", "password": "…",
     "changeIpUrl": "https://m.example.net/api/change?key=…", "changeIpMethod": "GET",
     "minChangeIpSeconds": 60}
  ],
  "rotation": {"mode": "off"}
}
```

```
python3 scripts/llc.py proxy set shop --json proxy.json --yes
rm proxy.json
```

- `server` is `scheme://host:port`, the scheme `http`, `https` or `socks5`.
  Never put the login in the address: it goes in `username` and `password`.
- `username`, `password` and `changeIpUrl` are kept by LiveLLM and never shown
  again; answers say `"hasAuth": true` and `"hasChangeIp": true` instead. Write
  the file, send it, then delete it.
- Sending the list again without a login keeps the stored one, as long as the
  proxy keeps its name and host (`"hasAuth": true`, or leave it out). A proxy
  moved to another host needs its login again (422). `"hasAuth": false`
  forgets it; the same goes for `changeIpUrl` with `hasChangeIp`.
- `changeIpMethod` is how the change-IP address is called: `GET` (default) or
  `POST`, as the provider's instructions say. Called the wrong way, every
  rotation ends with `lastError: change_ip_failed`.
- Up to 20 proxies. Names are short and lowercase.
- `checkUrl` (optional) is the address used to read the exit IP; leave it out.
  It is shown to everyone in the workspace, so one with a login, or a token,
  key or password in its query, is refused (422).

The same block can go into the create file as `"proxy": {...}`, or into
`set` as `{"browser": {"proxy": {...}}}`.

The first time a browser gets proxy settings it restarts once (its profile and
sign-ins are kept). After that, setting, changing, clearing and rotating never
restart it.

## How it goes out now

```
python3 scripts/llc.py proxy show shop
```

`mode` is `proxy`, `direct` or `waiting` (settings not applied yet: nothing
leaves the browser until they are). `via` names the proxy in use, `exitIp` and
`country` the address sites see, `measuredAt` when that was read, and
`lastError` what went wrong last, if anything: `upstream_unreachable`,
`upstream_login_refused`, `change_ip_failed`, `change_ip_too_soon`,
`exit_ip_unchanged`, `probe_failed`, `probe_unreadable` or `config_invalid`.

A proxy that can't be reached stops the browser's traffic; it never falls back
to going direct. With several proxies, it moves on to the next one.

## Rotate

```
python3 scripts/llc.py proxy rotate shop --yes
python3 scripts/llc.py proxy rotate shop --to mobile --yes
```

Rotate only when the user asked: it drops open connections for everyone on
the browser (below). Rotating moves to the next proxy (or the one named) and answers once the new
exit is measured. A mobile proxy with a `changeIpUrl` has that address called
first, which asks the provider for a new IP: it can take up to two minutes, and
calling it again before `minChangeIpSeconds` (10 to 3600, default 60) have
passed is refused with 429. Wait, then rotate again. With one proxy and no
change-IP address there is nothing to rotate to (409).

`rotation` rotates for you:

| `mode` | Rotates |
|---|---|
| `off` | only when you ask |
| `interval` | every `everyMinutes` (1 to 1440) |
| `session` | when a Browser API session starts on this browser, no other session there was used in the last 10 minutes, and at least 30 seconds (or the proxy's `minChangeIpSeconds`, if longer) have passed since the last rotation or since the settings were applied |

`order` is `sequential` (default) or `random`.

## What a proxy does and doesn't do

Say this to the user plainly when it matters:

- **One exit per browser.** Every tab, every Browser API session and every
  connected tool on that browser goes out through the same proxy. For one IP
  per session, use one session per browser.
- **Rotating drops open connections.** Tabs stay open; pages loading at that
  moment and their live connections drop and reconnect. A rotation by hand or
  on a timer changes the exit for everyone using the browser, a person in the
  live view included.
- **`session` mode** rotates only when no other session on the browser is in
  use and the last rotation (or the settings) is at least 30 seconds old, or
  the proxy's `minChangeIpSeconds`; otherwise the session starts on the
  current exit with `"proxyRotated": false` and a `reason` that says which.
  A session started right after `proxy set` keeps the first exit.
- **It covers the browser as LiveLLM starts it.** A tool connected to the
  browser (Chrome or Camoufox) can open its own context with a proxy of its own, and an extension
  with proxy permissions can change it. These settings reach only the browser
  as LiveLLM starts it, not a context or an extension a connected tool opens.

## Go direct, or drop the settings

```
python3 scripts/llc.py proxy clear shop --yes     # go direct now; no restart
python3 scripts/llc.py proxy remove shop --yes    # drop the proxy settings; it restarts
```

`clear` keeps the browser ready for a new list without a restart. Do either
only when the user asked.

## With the LiveLLM tools

The `browser_proxy` tool does the same: `status` reads it, `set`, `clear` and
`rotate` change it (Create on a browser this agent made, or Manage
everything). Ask the user first, as above.
