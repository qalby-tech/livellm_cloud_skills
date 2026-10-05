#!/usr/bin/env python3
"""Talk to LiveLLM Cloud: sign in, list and create resources, connect to them.

Only the Python standard library. Every command prints JSON on stdout. Errors
print JSON on stderr with a "next" field saying what to do, and set an exit
code: 2 the user must act, 3 not ready yet, 4 busy, 1 anything else.

    llc.py login [--wait] | logout | whoami | ssh-keys | logs <id>
    llc.py ls [--type TYPE]
    llc.py hosts                               (where resources can run: ids, regions, free room)
    llc.py create TYPE --json FILE --yes
    llc.py create browser --json FILE --engine chrome|camoufox --yes
    llc.py create apps --json FILE --yes       (several apps and their databases, linked)
    llc.py create apps --json FILE --join APP --yes   (add services to an existing app)
    llc.py wait ID [--timeout 600]
    llc.py connect ID [--tool cdp|view|api|computer]
                   [--screen-width PX] [--format png|jpeg] [--env]
    llc.py exec ID "COMMAND" [--session S] [--timeout N]
    llc.py share ID [--control] [--for 1h|24h|7d] | shares ID
    llc.py unshare ID SHARE_ID | release ID
    llc.py build ID [--wait] [--timeout 1800] | builds ID | deploy ID BUILD --yes | progress ID
    llc.py restart ID --yes
    llc.py backups ID | backup ID [--clean] [--name N]
    llc.py restore ID BACKUP --as NEW --password-env VAR [--at TIME]
                   [--host H | --region R] --yes           (a database: into a new one)
    llc.py restore ID BACKUP --yes                         (a machine: in place, stopped)
    llc.py stop ID --yes | start ID | set ID --json CHANGES --yes
    llc.py reach ID                            (who reaches it inside the workspace; ID or a Composable App's name)
    llc.py reach ID (--from a,b | --from '*' | --none | --add X | --remove X) --yes   (not a database)
    llc.py link ID DB [DB...] [--remove] --yes (an app, machine or Desktop App reaches a database; no variables)
    llc.py browser-api create NAME (--browsers a,b | --all) [--remote ID=WSS]
                   [--host H | --region R] --yes
    llc.py browser-api show NAME | (add | remove) NAME BROWSER --yes
    llc.py locales                             (languages and time zones a browser takes)
    llc.py engines                             (browser engines offered: Chrome, Camoufox)
    llc.py proxy show ID | proxy rotate ID [--to NAME] --yes | proxy set ID --json FILE --yes
    llc.py proxy clear ID --yes | proxy remove ID --yes      (clear: go direct; remove: restarts)
    llc.py profile show ID | profile snapshot ID [--name N] --yes
    llc.py profile restore|rm ID --snapshot S [--keep-current] --yes
    llc.py profile export ID --out FILE [--snapshot S] [--password-env VAR] --yes
    llc.py profile import ID --file FILE [--password-env VAR] [--force] --yes
    llc.py profile copy ID --from BROWSER [--snapshot S] --yes
    llc.py cookies ID --json FILE --yes
    llc.py templates | template show T | template save NAME --from ID [--description D]
    llc.py template use T NEW [--secret PATH=VALUE] [--secret-env PATH=VAR] [--json FILE]
                   [--host H | --region R | --automatic] --yes
    llc.py template rm T --yes
    llc.py activity [--actor you|platform|all] [--object ID] [--limit N] [--before EVENT]
    llc.py monitoring [ID] [--range 15m|1h|6h|24h|7d]
    llc.py rm ID [--with-databases] [--force] --yes

Sign-in is stored in ~/.config/livellm/credentials.json, readable only by you.
`login` prints a link and returns; run it again once the user has pressed Allow
(a sign-in started and not finished waits in credentials.pending.json).
Set LIVELLM_API_KEY instead for runs with nobody present, and LIVELLM_API_URL
to point at a self-hosted LiveLLM.
"""

import argparse
import base64
import contextlib
import http.client
import io
import json
import os
import select
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("LIVELLM_API_URL", "https://api.live-llm.com").rstrip("/")
API_KEY = os.environ.get("LIVELLM_API_KEY", "").strip()
CREDENTIALS = Path(os.environ.get("LIVELLM_CREDENTIALS", Path.home() / ".config" / "livellm" / "credentials.json"))
CLIENT_NAME = os.environ.get("LIVELLM_CLIENT_NAME", "").strip()
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TIMEOUT = 30
LOGIN_WAIT = 60  # seconds a second `login` waits for Allow before it says it is still waiting
POLL_UNIT = 1.0  # one second of the server's poll interval

EXIT_USER, EXIT_NOT_READY, EXIT_BUSY, EXIT_OTHER = 2, 3, 4, 1


class Problem(Exception):
    def __init__(self, message, nxt, code=EXIT_OTHER, status=None, oauth=None):
        super().__init__(message)
        self.message, self.next, self.code, self.status = message, nxt, code, status
        self.oauth = oauth  # the OAuth error code of a sign-in endpoint's refusal
        self.missing = []  # a create from a template: the secrets it still needs


def out(value):
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def request(method, path, body=None, token=None, form=False, timeout=TIMEOUT):
    """One API call. Returns the decoded body, or raises Problem."""
    url = path if path.startswith("http") else API + path
    data, headers = None, {"accept": "application/json"}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["content-type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            text = r.read().decode()
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode(errors="replace")
        finally:
            e.close()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"error": text.strip()[:200] or e.reason}
        raise http_problem(e.code, payload) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException, OSError) as e:
        raise unreachable(e) from None


def unreachable(e):
    reason = getattr(e, "reason", e)
    return Problem(f"cannot reach LiveLLM at {API}: {reason}",
                   "check the network, or LIVELLM_API_URL for a self-hosted LiveLLM")


def http_problem(status, payload):
    p = status_problem(status, payload)
    p.oauth = payload.get("error")
    missing = payload.get("missing")
    p.missing = missing if isinstance(missing, list) else []
    return p


def status_problem(status, payload):
    message = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
    code = payload.get("code") if isinstance(payload.get("code"), str) else ""
    inside = inside_problem(status, code, message)
    if inside:
        return inside
    browser = browser_problem(status, code, message)
    if browser:
        return browser
    if status == 401:
        return Problem(message, "run: llc.py login, give the user the link it prints, and run login again once they press Allow", EXIT_USER, status)
    if status == 402:
        return Problem(message, "the plan is full: show the user their usage and stop; never delete to make room", EXIT_USER, status)
    if status == 403:
        return Problem(message, "tell the user which permission this needs; they can turn it on for this agent on the console's Agents page "
                       "(for an API key, on the Keys page)", EXIT_USER, status)
    if status == 404:
        return Problem(message, "run: llc.py ls, the id is probably wrong", EXIT_OTHER, status)
    if status == 409 and "location can't change" in message:
        return Problem(message, "its first start never finished, so moving it can't help and retrying won't either: "
                       "ask the user before deleting it and creating it again (a restore can be run again from the same backup)",
                       EXIT_USER, status)
    if status == 409:
        return Problem(message, "if another agent holds the machine, wait until the time the message names or use another; "
                       "otherwise the resource is mid-change: wait a few seconds and retry once", EXIT_BUSY, status)
    if status == 422 and DB_REACH in message:
        return Problem(message, "leave reachableFrom out for a database; link it from what uses it instead: "
                       "llc.py link APP DB --yes, once the user agreed (SKILL.md rule 11)", EXIT_OTHER, status)
    if status == 422:
        return Problem(message, "fix the field the message names; do not retry unchanged", EXIT_OTHER, status)
    if status >= 500:
        return Problem(message, "platform trouble: retry twice with a pause, then tell the user", EXIT_OTHER, status)
    return Problem(message, "check the request", EXIT_OTHER, status)


NETWORK_NEXT = ("letting one resource reach another inside the workspace needs the Network permission: ask the user, "
                "saying what would reach what and why; a person turns on Network for this key on the Keys page, or for this "
                "agent on the Agents page, or sets Reachable from (or links the database) in the console. Never work around "
                "it, a public address included")

# The API's words when reachableFrom is sent for a database (422).
DB_REACH = "a database is reached only by what links it"


def inside_problem(status, code, message):
    """A refusal to let one resource reach another (the Network permission),
    known by its code or, when the answer carries none, by its words; None for
    any other answer."""
    low = message.lower()
    if status == 403 and (code == "network_permission" or "turn on network" in low or "network permission" in low):
        return Problem(message, NETWORK_NEXT, EXIT_USER, status)
    return None


def browser_problem(status, code, message):
    """A browser's proxy or profile refusal, with what to do about it; None
    for any other answer. A refusal is known by its code, or by the same word
    in the message when the answer carries no code."""
    low = message.lower()

    def said(token):
        return code == token or token in low

    if status == 403 and "profiles hold sign-ins" in low:
        return Problem(message, "this workspace keeps profiles to its own people: an export, an import or a copy by an agent or an "
                       "API key is refused unless it is the workspace owner's, and no permission changes that: tell the user, and stop",
                       EXIT_USER, status)
    if status == 409 and ("profiles are still starting" in low or "proxies are still starting" in low):
        return Problem(message, "the browser's helper is still starting: wait half a minute and run the same command again; "
                       "no restart is needed", EXIT_NOT_READY, status)
    if status == 409 and (said("needs_restart") or "restart this browser" in low):
        return Problem(message, "this browser needs one restart first: ask the user, then llc.py restart ID --yes "
                       "(its tabs close; the profile and its sign-ins are kept)", EXIT_USER, status)
    if status == 409 and (said("profile_newer") or "import anyway" in low) and "camoufox" in low:
        return Problem(message, "the profile comes from a newer Camoufox: ask the user, and only if they agree run the same import with --force",
                       EXIT_USER, status)
    if status == 409 and (said("profile_newer") or "import anyway" in low):
        return Problem(message, "the profile comes from a newer Chrome: ask the user, and only if they agree run the same import with --force",
                       EXIT_USER, status)
    if status == 409 and said("snapshot_key_changed"):
        return Problem(message, "this snapshot can't be restored any more, and retrying won't help: pick another one from llc.py profile show ID",
                       EXIT_OTHER, status)
    if status == 409 and (said("too_many_snapshots") or "most snapshots" in low):
        return Problem(message, "the browser keeps no more snapshots: show the user llc.py profile show ID and ask which one "
                       "to delete (llc.py profile rm ID --snapshot S --yes); never pick one yourself", EXIT_USER, status)
    if status == 409 and (said("nothing_to_rotate") or "nothing to rotate to" in low):
        return Problem(message, "one proxy without a change-IP address can't rotate: add a second proxy or its change-IP address (ask the user)",
                       EXIT_OTHER, status)
    if status == 429 and (said("change_ip_too_soon") or "too soon" in low):
        return Problem(message, "the mobile proxy's shortest time between IP changes hasn't passed: wait that long, then rotate again",
                       EXIT_BUSY, status)
    if status == 422 and (said("wrong_password") or "password doesn't open" in low
                          or said("password_required") or "password protected" in low):
        return Problem(message, "ask the user for this file's password, put it in an environment variable and pass --password-env VAR "
                       "(never on the command line); never guess one", EXIT_USER, status)
    # A browser's engine (Chrome or Camoufox): set when it is made, profiles
    # only between browsers of one engine. A Browser API has no engine: it
    # holds browsers of both.
    if status == 422 and (said("engine_unavailable") or "doesn't offer camoufox" in low):
        return Problem(message, "this LiveLLM makes no Camoufox browsers (llc.py engines lists what it offers): tell the user, "
                       "and make a Chrome browser only if they agree", EXIT_USER, status)
    # Known by the code or by naming a browser: a database's "engine can't
    # change" is a different refusal and keeps the plain answer.
    if status == 422 and (said("engine_fixed") or "browser's engine can't change" in low):
        return Problem(message, "a browser's engine is set when it is made: leave engine out of the change. For the other engine, "
                       "ask the user, then make a new browser and add this one's cookies to it (llc.py cookies)", EXIT_OTHER, status)
    if status == 422 and (said("extensions_unsupported") or "take no extensions" in low):
        return Problem(message, "Camoufox browsers take no extensions (an ad blocker is built in): leave extensions out, "
                       "or use a Chrome browser", EXIT_OTHER, status)
    # A profile copied from a browser of the other engine.
    if status == 422 and said("engine_mismatch"):
        return Problem(message, "a profile copies only between browsers of one engine: add the sign-ins as cookies instead "
                       "(llc.py cookies ID --json FILE --yes); llc.py ls names a Camoufox browser's engine, the rest are Chrome",
                       EXIT_OTHER, status)
    if status == 422 and (said("profile_engine") or "profiles move only between browsers of one engine" in low):
        return Problem(message, "profiles move only between browsers of one engine: add the sign-ins as cookies instead "
                       "(llc.py cookies ID --json FILE --yes); a cookie the browser can't take is counted in dropped",
                       EXIT_OTHER, status)
    # Worded by the browser's engine, so a Camoufox file into a Chrome browser
    # is refused this way too. A LiveLLM from before engines names neither
    # and keeps the plain answer.
    if status == 422 and (said("not_livellm_profile") or "only profiles exported from livellm" in low) \
            and ("camoufox" in low or "chrome" in low):
        engine = "Camoufox" if "camoufox" in low else "Chrome"
        return Problem(message, f"only files exported from a LiveLLM {engine} browser import into this one: for sign-ins from "
                       "another browser, or a browser of the other engine, add its cookies instead "
                       "(llc.py cookies ID --json FILE --yes)", EXIT_OTHER, status)
    if status == 507:
        return Problem(message, "the browser's storage is full: ask the user to grow it (llc.py set) or to pick a snapshot to delete",
                       EXIT_USER, status)
    if status == 413:
        return Problem(message, "the file is larger than LiveLLM takes: tell the user", EXIT_USER, status)
    return None


# --- sign-in -----------------------------------------------------------------


def read_credentials():
    try:
        return json.loads(CREDENTIALS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_credentials(data):
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.touch(mode=0o600, exist_ok=True)
    os.chmod(CREDENTIALS, 0o600)
    CREDENTIALS.write_text(json.dumps(data, indent=2))


def client_name():
    if CLIENT_NAME:
        return CLIENT_NAME
    host = os.uname().nodename if hasattr(os, "uname") else "this computer"
    return f"An agent on {host}"


def token():
    """A usable access token: the API key if set, else the sign-in, renewed."""
    if API_KEY:
        return API_KEY
    creds = read_credentials().get(API, {})
    if not creds:
        raise Problem("not signed in", "run: llc.py login, give the user the link it prints, and run login again once they press Allow", EXIT_USER)
    if creds.get("expires_at", 0) - 60 > time.time():
        return creds["access_token"]
    if not creds.get("refresh_token"):
        raise Problem("sign-in expired", "run: llc.py login again", EXIT_USER)
    try:
        fresh = request("POST", "/v1/oauth/token",
                        {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]}, form=True)
    except Problem:
        raise Problem("sign-in expired or ended", "run: llc.py login, give the user the link it prints, and run login again once they press Allow", EXIT_USER) from None
    return save_tokens(fresh)["access_token"]


def save_tokens(payload):
    creds = read_credentials()
    entry = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        "scope": payload.get("scope", ""),
        "workspace": payload.get("workspace", ""),
    }
    creds[API] = entry
    write_credentials(creds)
    return entry


def pending_path():
    """Next to the credentials: a sign-in started and not finished yet."""
    return CREDENTIALS.with_name(CREDENTIALS.stem + ".pending" + CREDENTIALS.suffix)


def read_pending():
    try:
        entry = json.loads(pending_path().read_text()).get(API)
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return entry if isinstance(entry, dict) and entry.get("device_code") else None


def write_pending(entry):
    """Keep entry as this LiveLLM's pending sign-in, or forget it (None)."""
    path = pending_path()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if entry is None:
        data.pop(API, None)
    else:
        data[API] = entry
    if not data:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    # The device code is a credential until it is used.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    path.write_text(json.dumps(data, indent=2))


def start_login(access):
    start = request("POST", "/v1/oauth/device/code",
                    {"client_name": client_name(), "scope": access}, form=True)
    entry = {
        "device_code": start["device_code"],
        "user_code": start["user_code"],
        "link": start["verification_uri_complete"],
        "interval": int(start.get("interval", 5)),
        "expires_at": int(time.time() + int(start.get("expires_in", 600))),
        "access": access,
    }
    write_pending(entry)
    return entry


def link_answer(pending, note=None):
    answer = {
        "signedIn": False,
        "link": pending["link"],
        "code": pending["user_code"],
        "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(pending["expires_at"])),
        "next": "give the user the link; once they press Allow, run: llc.py login",
    }
    if note:
        answer["note"] = note
    return answer


def poll_login(pending, until):
    """Ask about the pending sign-in until it is allowed or until runs out.

    Returns (tokens, None) once allowed, (None, reason) when the link can't be
    finished any more (it expired, was denied or was already used), and
    (None, None) while it is still waiting.
    """
    pending["interval"] = max(int(pending.get("interval") or 5), 1)
    polled = float(pending.get("polled_at") or 0)
    # The server asks for a pause between polls, the last run's included.
    nxt = polled + pending["interval"] * POLL_UNIT if polled else time.time()
    while True:
        delay = nxt - time.time()
        if delay > 0:
            if time.time() + delay > until:
                return None, None
            time.sleep(delay)
        try:
            return request("POST", "/v1/oauth/token",
                           {"grant_type": DEVICE_GRANT, "device_code": pending["device_code"]}, form=True), None
        except Problem as p:
            pending["polled_at"] = time.time()
            if p.oauth == "authorization_pending":
                pass
            elif p.oauth == "slow_down" or p.status == 429:
                pending["interval"] += 5
            elif p.oauth in ("expired_token", "access_denied", "invalid_grant"):
                return None, p.message
            else:
                raise
        write_pending(pending)
        nxt = time.time() + pending["interval"] * POLL_UNIT


def finish_login(tokens):
    entry = save_tokens(tokens)
    write_pending(None)
    out({"signedIn": True, "workspace": entry["workspace"], "access": entry["scope"]})


def login(args):
    """Two calls: the first prints the link and returns, the next finishes it."""
    if API_KEY:
        out({"signedIn": True, "with": "LIVELLM_API_KEY"})
        return
    pending = read_pending()
    # Without --access, login again finishes the pending sign-in whatever
    # access it asked for (the CLI shares this file); only an explicit,
    # different --access starts over.
    if args.access is None:
        args.access = (pending or {}).get("access") or "create"
    if pending and (float(pending.get("expires_at") or 0) <= time.time() or pending.get("access") != args.access):
        pending = None
    fresh = pending is None
    if fresh:
        pending = start_login(args.access)

    if args.wait:
        while True:
            sys.stderr.write(f"\nAsk the user to open {pending['link']} and click Allow"
                             f" (code {pending['user_code']}).\n\n")
            tokens, gone = poll_login(pending, float(pending["expires_at"]))
            if tokens:
                finish_login(tokens)
                return
            if gone and not fresh:
                pending, fresh = start_login(args.access), True
                continue
            write_pending(None)
            if gone:
                raise Problem(gone, "ask the user to open the link again, then run: llc.py login", EXIT_USER)
            raise Problem("the sign-in link expired", "run: llc.py login again and give the user the new link", EXIT_USER)

    if fresh:
        out(link_answer(pending))
        return
    tokens, gone = poll_login(pending, min(time.time() + LOGIN_WAIT, float(pending["expires_at"])))
    if tokens:
        finish_login(tokens)
        return
    if gone:
        out(link_answer(start_login(args.access),
                        f"the last link can't be used any more ({gone}); give the user this new one"))
        return
    raise Problem(f"still waiting: the user hasn't pressed Allow at {pending['link']} yet",
                  "still waiting — once the user has pressed Allow, run: llc.py login again", EXIT_NOT_READY)


def logout(_args):
    creds = read_credentials()
    entry = creds.pop(API, None)
    if entry and entry.get("refresh_token"):
        try:
            request("POST", "/v1/oauth/revoke", {"token": entry["refresh_token"]}, form=True)
        except Problem:
            pass
    write_credentials(creds)
    out({"signedOut": True})


# --- workspace ---------------------------------------------------------------


def whoami(_args):
    tok = token()
    workspace = request("GET", "/v1/workspace", token=tok)
    usage = {}
    try:
        usage = request("GET", "/v1/billing", token=tok)
    except Problem:
        pass
    out({
        "workspace": workspace.get("name"),
        "plan": workspace.get("plan"),
        "signedInWith": "api key" if API_KEY else "sign-in",
        "access": read_credentials().get(API, {}).get("scope", "full" if API_KEY else ""),
        "usage": usage.get("usage", usage) if isinstance(usage, dict) else {},
    })


def resources(tok):
    spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
    status = {w["id"]: w for w in request("GET", "/v1/status", token=tok).get("workloads", [])}
    for w in spec.get("workloads", []):
        live = status.get(w["id"], {})
        yield {
            "id": w["id"],
            "type": w["type"],
            "state": live.get("phase", "unknown"),
            "ready": live.get("ready", False),
            "createdBy": (w.get("createdBy") or {}).get("name", "a person"),
            "endpoints": live.get("endpoints", []),
            "ssh": live.get("ssh", ""),
            # the database links of an app, a machine or a Desktop App as its settings hold them,
            # and the apps a database serves
            **({"databases": link_list(w)} if link_list(w) else {}),
            **({"usedBy": live["usedBy"]} if live.get("usedBy") else {}),
            # a Camoufox browser says so; a Chrome one carries no engine (a Browser API has none)
            **({"engine": "camoufox"} if (w.get("browser") or {}).get("engine") == "camoufox" else {}),
            # who else in the workspace may reach it, as its settings hold it
            **({"reachableFrom": w["reachableFrom"]} if "reachableFrom" in w else {}),
            # a database's login name (its password is never shown)
            **({"username": w["storage"]["credentials"]["username"]}
               if ((w.get("storage") or {}).get("credentials") or {}).get("username") else {}),
        }


def ls(args):
    items = [r for r in resources(token()) if not args.type or r["type"] == args.type]
    out({"resources": items})


ENGINE_TYPES = ("browser",)  # the types made with an engine


def with_engine(body, typ, engine):
    """The create body with --engine in it. Chrome is what a browser is when
    nothing says otherwise, so only camoufox is sent; a file that already
    names the other engine is refused rather than overridden."""
    if not engine:
        return body
    if typ not in ENGINE_TYPES:
        raise Problem(f"--engine is for a browser, not {typ}", "leave out --engine", EXIT_OTHER)
    said = body.get("engine") if isinstance(body, dict) else None
    if said and said != engine:
        raise Problem(f"the file says engine {said!r} and --engine says {engine!r}", "say it once", EXIT_OTHER)
    if engine == "camoufox":
        body = {**body, "engine": "camoufox"}
    return body


def stored_engine(wid, tok):
    """The engine a browser was made with, as LiveLLM keeps it: "camoufox",
    or "chrome" when it keeps none."""
    for w in request("GET", "/v1/workspace", token=tok).get("spec", {}).get("workloads", []):
        if w.get("id") == wid:
            return (w.get("browser") or {}).get("engine") or "chrome"
    return None


def create(args):
    body = json.loads(Path(args.json).read_text())
    body = with_engine(body, args.type, getattr(args, "engine", None))
    reminder = None if args.type == "apps" else check_settings_proxy(body, args.json)
    check_body_reach(body, args.json, typ=args.type)
    check_body_links(body, args.type, args.json)
    if args.type == "apps":
        # several apps at once, all or nothing: a list of app settings, or
        # {"apps": [...], "databases": [...]} with the databases to make with them
        apps = body if isinstance(body, list) else body.get("apps") or []
        dbs = [] if isinstance(body, list) else body.get("databases") or []
        # --join (or "join" in the file): add them to an app already there;
        # they take its stack, and an app on its own gets one named after itself
        join = getattr(args, "join", None) or (None if isinstance(body, list) else body.get("join"))
        if not apps:
            raise Problem("the file should hold a list of apps, or {\"apps\": [...], \"databases\": [...]}", "fix the file", EXIT_OTHER)
        made = request("POST", "/v1/workloads", {"apps": apps, **({"databases": dbs} if dbs else {}), **({"join": join} if join else {})},
                       token=token())
        ids = made.get("created", [])
        answer = {"created": ids, "next": f"llc.py wait {ids[-1] if ids else '<id>'} then llc.py connect the app with a public port"}
        if made.get("databases"):
            answer["databases"] = made["databases"]
        out(answer)
        return
    tok = token()
    request("POST", f"/v1/workloads/{urllib.parse.quote(args.type)}", body, token=tok)
    answer = {"created": body.get("id"), "type": args.type,
              "next": f"llc.py wait {body.get('id')} then llc.py connect {body.get('id')}"}
    if args.type in ENGINE_TYPES and isinstance(body, dict) and body.get("engine") == "camoufox":
        camoufox_made(body.get("id"), tok)
        answer["engine"] = "camoufox"
    if reminder:
        answer["delete"] = reminder
    out(answer)


def camoufox_made(wid, tok):
    """A LiveLLM that knows no engines takes the create and makes Chrome: say
    so instead of letting a Chrome browser pass for Camoufox."""
    got = stored_engine(wid, tok)
    if got is not None and got != "camoufox":
        raise Problem(f"this LiveLLM made {wid} with {got}, not Camoufox: it offers no engines yet",
                      f"tell the user; {wid} exists and is yours, so delete it (llc.py rm {wid} --yes) only if they want it gone",
                      EXIT_USER)


def wait(args):
    tok, deadline = token(), time.time() + args.timeout
    while time.time() < deadline:
        for r in resources(tok):
            if r["id"] != args.id:
                continue
            if r["ready"]:
                out({"id": r["id"], "state": r["state"], "ready": True})
                return
            break
        else:
            raise Problem(f"no resource {args.id}", "run: llc.py ls", EXIT_OTHER)
        time.sleep(5)
    events = request("GET", "/v1/status", token=tok).get("events", [])[:5]
    raise Problem(f"{args.id} is still not ready", "tell the user what the events say; do not guess", EXIT_NOT_READY)


def logs(args):
    """Recent log lines from a resource, with how its containers are doing."""
    info = request("GET", f"/v1/workloads/{urllib.parse.quote(args.id)}/observe?tailLines={int(args.lines)}", token=token())
    out({
        "id": args.id,
        "pods": [{
            "name": p.get("name"),
            "phase": p.get("phase"),
            "ready": p.get("ready"),
            "containers": [{
                "name": c.get("name"),
                "state": c.get("state"),
                "restarts": c.get("restartCount"),
                "cpu": c.get("cpu"),
                "memory": c.get("memory"),
                "logs": [f"{l.get('ts','')} {l.get('body','')}".strip() for l in (c.get("recentLogs") or [])],
            } for c in (p.get("containers") or [])],
        } for p in (info.get("pods") or [])],
    })


def ssh_keys(_args):
    """The workspace's SSH keys. Only the user can change them, in the console."""
    out(request("GET", "/v1/ssh-keys", token=token()))


# What `hosts` keeps of each host: where it is, how much room it has, and
# whether it takes new resources (a location is accepted only on a host that
# is ready and schedulable, in one of the workspace's host groups).
HOST_FIELDS = ("id", "region", "zone", "nodeGroup", "cpuFree", "memFreeGi", "gpuType", "gpuFree",
               "ready", "schedulable")


def hosts_view(answer):
    """The host list trimmed to what choosing a location needs; a field the
    platform leaves out stays out, and so does an empty host group (general)."""
    return {"hosts": [{k: h[k] for k in HOST_FIELDS if k in h and not (k == "nodeGroup" and not h[k])}
                      for h in (answer or {}).get("hosts") or [] if isinstance(h, dict)]}


def hosts(_args):
    """Where resources can run: each host's id, region and free room."""
    out(hosts_view(request("GET", "/v1/fleet/hosts", token=token())))


def placement(host, region):
    """Where a new resource runs: pinned to a host, any host of a region, or
    None for automatic (LiveLLM picks the host). A flag given with no value is
    refused rather than quietly meaning automatic."""
    if host is not None and region is not None:
        raise Problem("--host and --region are one or the other",
                      "pass --host to pin one host, or --region for any host in it", EXIT_OTHER)
    for flag, value in (("--host", host), ("--region", region)):
        if value is not None and not value.strip():
            raise Problem(f"{flag} needs a value", "llc.py hosts lists ids and regions; leave it out for automatic", EXIT_OTHER)
    host, region = (host or "").strip(), (region or "").strip()
    if host:
        return {"strategy": "host", "host": host}
    if region:
        return {"strategy": "region", "region": region}
    return None


def connect(args):
    tok = token()
    body = {"tool": args.tool} if args.tool else {}
    screen = {k: v for k, v in (("width", args.screen_width), ("format", args.format)) if v}
    if screen:
        body["screen"] = screen
    info = request("POST", f"/v1/workloads/{urllib.parse.quote(args.id)}/connect", body, token=tok)
    if str(info.get("type", "")).startswith("vm-"):
        # A machine is reached over SSH; its address lives in the status.
        for w in request("GET", "/v1/status", token=tok).get("workloads", []):
            if w.get("id") != args.id:
                continue
            if w.get("ssh"):
                host, _, port = w["ssh"].rpartition(":")
                info["ssh"] = {"address": w["ssh"], "host": host, "port": port}
            if w.get("expiresAt"):
                info["stopsAt"] = w["expiresAt"]
            break
    raw = [u for u in info.get("urls") or [] if u.get("raw") and not u.get("address")]
    if raw:
        # A raw TCP/UDP port's host:port lives in the status, like a machine's.
        for w in request("GET", "/v1/status", token=tok).get("workloads", []):
            if w.get("id") != args.id:
                continue
            addrs = {e.get("name"): e for e in w.get("endpoints") or [] if e.get("addr")}
            for u in raw:
                e = addrs.get(u.get("port"))
                if e:
                    u["address"] = e["addr"]
                    u["protocol"] = "udp" if e.get("udp") else "tcp"
            break
    if args.env:
        for key, value in [("LIVELLM_CDP_URL", (info.get("cdp") or {}).get("url")),
                           # a Camoufox browser: its Playwright address, and the Playwright it takes
                           ("LIVELLM_PLAYWRIGHT_URL", (info.get("playwright") or {}).get("url")),
                           ("LIVELLM_PLAYWRIGHT_VERSION", (info.get("playwright") or {}).get("version")),
                           ("LIVELLM_COMPUTER_URL", (info.get("computer") or {}).get("url")),
                           ("LIVELLM_CONNECT_TOKEN", info.get("token")),
                           ("LIVELLM_SSH_ADDRESS", (info.get("ssh") or {}).get("address"))]:
            if value:
                print(f"export {key}={json.dumps(value)}")
        return
    out(info)


EXEC_POLL = 55  # seconds one exec call waits for its command (the API's most)
EXEC_SLACK = 120  # seconds past a command's time limit exec keeps looking at it


def run_command(args):
    """One command on a machine or a Desktop App: bash, or PowerShell on Windows.

    Waits until it ends. A command still going when a call answers keeps going
    on the machine; this looks at its run again, until it ends or its time
    limit (the platform stops it then, exit code 124) has long passed."""
    base = f"/v1/workloads/{urllib.parse.quote(args.id)}/exec"
    body = {"command": args.command, "timeout": args.timeout, "wait": EXEC_POLL}
    if args.session:
        body["session"] = args.session
    # Each call waits up to EXEC_POLL for the command; getting onto the machine comes on top.
    answer = request("POST", base, body, token=token(), timeout=EXEC_POLL + 35)
    deadline = time.monotonic() + args.timeout + EXEC_SLACK
    while answer.get("done") is False:  # an answer without done is from before runs: the end
        run = answer.get("runId")
        if not run:
            raise Problem("LiveLLM answered a command still going without its run id", "run the command again")
        if time.monotonic() > deadline:
            out(answer)
            raise Problem(f"{args.id} is still running after its time limit", "it was left as it is; check the machine", EXIT_BUSY)
        answer = look_again(f"{base}/{urllib.parse.quote(run)}?wait={EXEC_POLL}")
    out(answer)


def look_again(path, tries=3):
    """A read that may be repeated: tried again when the network, not LiveLLM, failed it."""
    for attempt in range(tries):
        try:
            return request("GET", path, token=token(), timeout=EXEC_POLL + 35)
        except Problem as p:
            if p.status is not None or attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1) * POLL_UNIT)


def share(args):
    """A link that opens the screen in any browser. Its address is shown only now."""
    body = {"mode": "control" if args.control else "view", "for": args.duration}
    out(request("POST", f"/v1/workloads/{urllib.parse.quote(args.id)}/shares", body, token=token()))


def simple(method, path, ok):
    def run(args):
        request(method, path(args), token=token())
        out(ok(args))
    return run


# The resource types that honour "stopped": machines, desktops and apps. A
# browser or a database keeps running whatever the flag says.
STOPPABLE = {"vm-ubuntu", "vm-ubuntu-desktop", "vm-windows", "desktop", "pod"}


def workload_path(wid):
    return f"/v1/workloads/{urllib.parse.quote(wid)}"


def patch_workload(wid, changes, tok):
    """Change only the fields sent (a JSON merge patch; null removes one)."""
    return request("PATCH", workload_path(wid), changes, token=tok)


def set_stopped(stop):
    """Stop or start a resource. It is read only to refuse what can't stop and
    to skip a change that changes nothing; the write patches "stopped" alone,
    so a change someone made meanwhile is kept."""
    def run(args):
        tok = token()
        spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
        w = next((x for x in spec.get("workloads", []) if x.get("id") == args.id), None)
        if w is None:
            raise Problem(f"no resource {args.id}", "run: llc.py ls, the id is probably wrong", EXIT_OTHER)
        if w.get("type") not in STOPPABLE:
            raise Problem(f"{args.id} can't be stopped or started: only machines and apps can",
                          f"llc.py rm {args.id} deletes it", EXIT_OTHER)
        if bool(w.get("stopped")) == stop:
            out({"id": args.id, "already": "stopped" if stop else "running"})
            return
        patch_workload(args.id, {"stopped": stop}, tok)
        if stop:
            out({"stopping": args.id, "next": f"its disks are kept; llc.py start {args.id} runs it again"})
        else:
            out({"starting": args.id, "next": f"llc.py wait {args.id}"})
    return run


def set_settings(args):
    """Change some settings: the file holds only what changes."""
    changes = json.loads(Path(args.json).read_text())
    if not isinstance(changes, dict) or not changes:
        raise Problem("the file should hold a JSON object with the settings that change", "fix the file", EXIT_OTHER)
    reminder = check_settings_proxy(changes, args.json)
    check_body_reach(changes, args.json, patch=True)
    patch_workload(args.id, changes, token())
    out({"changed": args.id, **({"delete": reminder} if reminder else {})})


# --- inside the workspace: who may reach a resource -------------------------

WHOLE = "*"  # reachableFrom ["*"]: every resource in the workspace, also ones made later


def check_reach(value, where):
    """Refuse a reachableFrom the API would refuse for its shape: a list of
    resource ids, or ["*"] alone. Names are left to the API (422 for one that
    isn't in the workspace)."""
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise Problem(f"{where}: reachableFrom should be a list of resource ids, or [\"*\"] for the whole workspace",
                      "fix the file; [] means nothing reaches it", EXIT_OTHER)
    if WHOLE in value and len(value) > 1:
        raise Problem(f'{where}: reachableFrom: "*" (the whole workspace) goes alone',
                      'send ["*"], or only the resources that may reach it', EXIT_OTHER)
    twice = sorted({x for x in value if value.count(x) > 1})
    if twice:
        raise Problem(f"{where}: reachableFrom lists {', '.join(twice)} twice", "name each resource once", EXIT_OTHER)


def db_reach_problem(db, where=None):
    """A database has no reachableFrom: what links it reaches it, nothing else.
    The API's own words (422), checked before anything is sent."""
    return Problem(f"{where + ': ' if where else ''}reachableFrom: {DB_REACH}: add {db} to the databases of the app, "
                   "machine or Desktop App that uses it",
                   f"leave reachableFrom out; once the user agreed (SKILL.md rule 11), link it: llc.py link APP {db} --yes",
                   EXIT_OTHER)


def check_body_reach(body, where, patch=False, typ=None):
    """A create or set file keeps its reachableFrom as written (every entry of
    a create apps file too); only its shape is checked here. In a set, null
    closes the resource like []. A database (a create storage file, or the
    databases of a create apps file) takes none: [] or null pass, as the API
    drops them, and anything else is refused with the API's words."""
    entries = []  # (entry, is a database)
    if isinstance(body, list):
        entries = [(e, False) for e in body]
    elif isinstance(body, dict):
        entries = [(body, typ == "storage")] + [
            (e, k == "databases") for k in ("apps", "databases") if isinstance(body.get(k), list) for e in body[k]]
    for e, is_db in entries:
        if isinstance(e, dict) and "reachableFrom" in e:
            if is_db:
                if e["reachableFrom"] not in (None, []):
                    raise db_reach_problem(e.get("id", "the database"), where)
                continue
            if patch and e["reachableFrom"] is None:
                continue
            check_reach(e["reachableFrom"], f"{where} ({e.get('id', 'the resource')})")


# The types that may link databases, and where their settings keep the links.
LINK_BLOCK = {"pod": "pod", "desktop": "desktop"}  # and every vm-* type: "vm"
MAX_LINKS = 8


def link_block(t):
    """Where a type keeps its database links ("pod", "vm" or "desktop"), or
    None for a type that links none."""
    t = t or ""
    return "vm" if t.startswith("vm-") else LINK_BLOCK.get(t)


def link_list(w):
    """A resource's database links as its settings hold them."""
    block = link_block(w.get("type"))
    links = ((w.get(block) or {}).get("databases") if block else None) or []
    return [d for d in links if isinstance(d, dict)]


def machine_kind(t):
    return "a Desktop App" if t == "desktop" else "a machine"


def check_body_links(body, typ, where):
    """A machine or a Desktop App links a database to reach it, never for
    variables: a link of theirs carrying env is refused with the API's words."""
    if not isinstance(body, dict) or link_block(typ) not in ("vm", "desktop"):
        return
    links = body.get("databases")
    if links is None:
        return
    if not isinstance(links, list):
        raise Problem(f"{where}: databases should be a list of {{\"id\": \"<database>\"}}", "fix the file", EXIT_OTHER)
    for d in links:
        if isinstance(d, dict) and d.get("env"):
            raise Problem(f"{where}: databases ({d.get('id')}): {machine_kind(typ)} gets no variables from a link: "
                          "it only lets it reach the database",
                          'send {"id": "<database>"} alone', EXIT_OTHER)


def split_names(text):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def stack_of(w):
    if w.get("type") != "pod":
        return ""
    return ((w.get("pod") or {}).get("stack") or "").strip()


def reach_group(workloads, target):
    """The ids that count as one resource with target: its Composable App's
    services, or target alone."""
    stack = stack_of(target)
    if not stack:
        return {target.get("id")}
    return {x.get("id") for x in workloads if stack_of(x) == stack}


def find_reach_target(workloads, name):
    """A resource by its id, or a Composable App by its name (its first
    service stands for the whole app)."""
    w = next((x for x in workloads if x.get("id") == name), None)
    if w is None:
        w = next((x for x in workloads if stack_of(x) == name), None)
    if w is None:
        raise Problem(f"no resource {name}", "run: llc.py ls, the id (or the Composable App's name) is probably wrong", EXIT_OTHER)
    return w


def implicit_callers(spec_workloads, target):
    """Who reaches a resource whatever its setting, read from the settings: the
    other services of its own Composable App; the apps that link it or wait for
    it (or for any service of its Composable App), each with its whole
    Composable App (a service that doesn't link it itself names the one that
    does in "via"); the machines and Desktop Apps that link it (a database);
    a Browser API that drives it, with what reaches that Browser API in
    "through"."""
    group = reach_group(spec_workloads, target)
    tid = target.get("id")

    def link_why(x):
        if any(d.get("id") in group for d in link_list(x)):
            return "links it"
        if x.get("type") == "pod" and any(d in group for d in (x.get("pod") or {}).get("dependsOn") or []):
            return "waits for it"
        return ""

    by_stack = {}  # a linking Composable App: its first service that links
    for x in spec_workloads:
        s = stack_of(x)
        if x.get("type") == "pod" and x.get("id") not in group and s and s not in by_stack:
            why = link_why(x)
            if why:
                by_stack[s] = (x.get("id"), why)
    found = []
    for x in spec_workloads:
        xid = x.get("id")
        if xid in group:
            if xid != tid:
                found.append({"id": xid, "why": "same app"})
            continue
        if x.get("type") == "pod":
            why, via, s = link_why(x), None, stack_of(x)
            if not why and s in by_stack:
                via, why = by_stack[s]
            if not why:
                continue
            e = {"id": xid, "why": why}
            if s:
                e["app"] = s
            if via:
                e["via"] = via
            found.append(e)
        elif link_block(x.get("type")) in ("vm", "desktop"):
            if link_why(x):
                found.append({"id": xid, "why": "links it"})
        elif x.get("type") == BROWSER_API and target.get("type") == "browser":
            c = x.get("controller") or {}
            if c.get("autodiscover") or tid in (c.get("browsers") or []):
                found.append({"id": xid, "why": "drives it", "through": x.get("reachableFrom")})
    return found


def inside_addresses(res, w):
    """The names a resource answers on inside the workspace (res is
    <workspace>-<id>), by type, as the platform names them."""
    t = w.get("type") or ""

    def hp(host, port, **kw):
        return {"host": host, "port": port, **kw}

    if t == "pod":
        pod = w.get("pod") or {}
        host = (pod.get("hostname") or w.get("id")) if stack_of(w) else None
        found = []
        for p in pod.get("ports") or []:
            n = (p or {}).get("port")
            if not n:
                continue
            raw = not p.get("internal") and (p.get("udp") or p.get("tcp"))
            e = hp(f"{res}-raw" if raw else res, n)
            if raw and p.get("udp"):
                e["protocol"] = "udp"
            if p.get("name"):
                e["name"] = p["name"]
            if host:
                e["inStack"] = f"{host}:{n}"
            found.append(e)
        return found
    if t.startswith("vm-"):
        found = [hp(res, 22, name="ssh")]
        for p in (w.get("vm") or {}).get("ports") or []:
            n = (p or {}).get("port")
            if not n:
                continue
            if p.get("internal"):
                host = f"{res}-internal"
            elif p.get("udp") or p.get("tcp"):
                host = res
            else:
                host = f"{res}-http"
            e = hp(host, n)
            if p.get("name"):
                e["name"] = p["name"]
            if p.get("udp") and not p.get("internal"):
                e["protocol"] = "udp"
            found.append(e)
        if t in ("vm-windows", "vm-ubuntu-desktop"):
            found.append(hp(f"{res}-rdp", 3389, name="rdp"))
        return found
    if t == "storage":
        if (w.get("storage") or {}).get("engine") == "redis":
            return [hp(res, 6379)]
        return [hp(f"{res}-rw", 5432)]
    if t == "browser":
        return [hp(res, 9222), hp(res, 9000)]
    if t == BROWSER_API:
        return [hp(res, 8000)]
    if t == "desktop":
        return [hp(f"{res}-0.{res}", 5900)]
    return []


DB_NOTE = ("a database is reached only by what links it (alsoFrom); to let an app, a machine or a Desktop App "
           "reach it, link it once the user agreed: llc.py link APP DB --yes")

UNSET_NOTE = ("not set: made before this setting existed. Until the platform gave it a value this meant the whole "
              "workspace; after that, nothing. Set it with --from or --none")


def reach(args):
    """Who may reach a resource from inside the workspace; with --from, --none,
    --add or --remove, change it (only what the user agreed to). It reads the
    workspace only: connect would hold a machine or hand out a token."""
    tok = token()
    ws = request("GET", "/v1/workspace", token=tok)
    workloads = (ws.get("spec") or {}).get("workloads", [])
    w = find_reach_target(workloads, args.id)
    wid = w.get("id")
    current = w.get("reachableFrom")  # None: set before this setting existed
    adds, removes = split_names(args.add), split_names(args.remove)
    whole_list = args.source is not None or args.none
    if args.source is not None and args.none:
        raise Problem("--from and --none are one or the other", "--none means nothing reaches it", EXIT_OTHER)
    if whole_list and (adds or removes):
        raise Problem("--from and --none set the whole list; --add and --remove change one entry",
                      "use one way or the other", EXIT_OTHER)
    if not whole_list and not adds and not removes:
        out(reach_view(w, workloads, current, ws.get("name")))
        return
    if w.get("type") == "storage":
        raise db_reach_problem(wid)
    if not args.yes:
        raise Problem("changing who reaches a resource needs --yes",
                      "who may reach a resource is the user's call: ask the user and wait for their agreement "
                      "(SKILL.md rule 11), then pass --yes", EXIT_OTHER)
    if args.none:
        new = []
    elif args.source is not None:
        new = split_names(args.source)
        if not new:
            raise Problem("--from needs resource ids, or '*' for the whole workspace", "--none means nothing reaches it", EXIT_OTHER)
    else:
        # Read again just before writing, so a change made since the first
        # read is kept (a change landing between this read and the write is
        # still lost: the API takes the whole list).
        fresh = next((x for x in workspace_workloads(tok) if x.get("id") == wid), None)
        if fresh is None:
            raise Problem(f"no resource {args.id}", "it was deleted meanwhile; run: llc.py ls", EXIT_OTHER)
        current = fresh.get("reachableFrom")
        if current is None:
            raise Problem(f"{args.id} has no setting yet, so there is nothing to add to or take from",
                          "set the whole list: --from with the resources that may reach it, or --none", EXIT_OTHER)
        base = list(current)
        if base == [WHOLE]:
            if removes:
                raise Problem(f"{args.id} lets the whole workspace in, so there is nothing to take out",
                              "pass --from with the resources that should keep reaching it, or --none", EXIT_OTHER)
            out({"id": args.id, "reachableFrom": [WHOLE], "already": "the whole workspace reaches it"})
            return
        new = [x for x in base if x not in removes] + [a for a in adds if a not in base]
    check_reach(new, args.id)
    if current is not None and new == current:
        out({"id": args.id, "reachableFrom": current, "already": "nothing changes"})
        return
    patch_workload(wid, {"reachableFrom": new}, tok)
    answer = {"id": args.id, "reachableFrom": new, "before": current}
    stack = stack_of(w)
    if stack:
        answer["note"] = f"every service of the Composable App {stack} takes it"
    out(answer)


def reach_view(w, workloads, current, workspace):
    """The setting, who else reaches the resource and its inside addresses,
    read from the workspace's settings (never from connect, which holds a
    machine or hands out a token)."""
    view = {"id": w.get("id"), "type": w.get("type")}
    stack = stack_of(w)
    if stack:
        view["app"] = stack
    also = implicit_callers(workloads, w)
    if w.get("type") == "storage":
        # no setting: what links it reaches it, nothing else
        view["note"] = DB_NOTE
        current = None
    else:
        view["reachableFrom"] = current
        if current is None:
            view["note"] = UNSET_NOTE
    view["alsoFrom"] = also
    if (current or also) and workspace:
        view["addresses"] = inside_addresses(f"{workspace}-{w.get('id')}", w)
    return view


def link(args):
    """Link databases to an app, a machine or a Desktop App so that it reaches
    them, with no variables (reach only: nothing restarts), or take links out
    with --remove. Links with variables already there are kept as they are."""
    tok = token()
    workloads = workspace_workloads(tok)
    w = next((x for x in workloads if x.get("id") == args.id), None)
    if w is None:
        services = sorted(x.get("id") for x in workloads if stack_of(x) == args.id)
        if services:
            raise Problem(f"{args.id} is a Composable App: a link goes on one of its services ({', '.join(services)})",
                          "name the service that uses the database; the whole app reaches it", EXIT_OTHER)
        raise Problem(f"no resource {args.id}", "run: llc.py ls, the id is probably wrong", EXIT_OTHER)
    block = link_block(w.get("type"))
    if block is None:
        raise Problem(f"{args.id} is a {w.get('type')}: only an app, a machine or a Desktop App links a database",
                      "link the database from what uses it", EXIT_OTHER)
    names = list(dict.fromkeys(d.strip() for d in args.databases if d.strip()))
    if not names:
        raise Problem("name the databases to link", "llc.py link ID DB [DB...] --yes", EXIT_OTHER)
    by_id = {x.get("id"): x for x in workloads}
    for d in names:
        x = by_id.get(d)
        if x is None and not args.remove:
            raise Problem(f"no database {d}", "run: llc.py ls --type storage, the id is probably wrong", EXIT_OTHER)
        if x is not None and x.get("type") != "storage":
            raise Problem(f"{d} is a {x.get('type')}, not a database: only databases are linked",
                          f"to let {args.id} reach it, ask the user, then: llc.py reach {d} --add {args.id} --yes",
                          EXIT_OTHER)
    if not args.yes:
        raise Problem("changing what a resource links needs --yes",
                      "a link lets the resource reach the database, and taking one out cuts it off: ask the user and "
                      "wait for their agreement (SKILL.md rule 11), then pass --yes", EXIT_OTHER)
    current = link_list(w)
    linked = [d.get("id") for d in current]
    if args.remove:
        new = [d for d in current if d.get("id") not in names]
        missing = [d for d in names if d not in linked]
    else:
        new = current + [{"id": d} for d in names if d not in linked]
        missing = [d for d in names if d in linked]
    if new == current:
        out({"id": args.id, "databases": current,
             "already": "none of them is linked" if args.remove else "every one is linked already"})
        return
    if len(new) > MAX_LINKS:
        raise Problem(f"{args.id} would link {len(new)} databases: at most {MAX_LINKS}",
                      "take a link out first, with the user's agreement", EXIT_OTHER)
    patch_workload(args.id, {block: {"databases": new or None}}, tok)
    answer = {"id": args.id, "databases": new, "before": current}
    if missing:
        answer["notLinked" if args.remove else "alreadyLinked"] = missing
    gone_env = [d.get("id") for d in current if d.get("env") and d.get("id") in names] if args.remove else []
    if gone_env:
        answer["note"] = (f"the variables {args.id} took from {', '.join(gone_env)} are gone: it restarts once, "
                          "and no longer reaches them")
    elif args.remove:
        answer["note"] = f"{args.id} no longer reaches them; nothing restarts"
    else:
        whole = f", with its whole Composable App {stack_of(w)}" if stack_of(w) else ""
        answer["note"] = f"reach only: {args.id} reaches them{whole}; no variables are added and nothing restarts"
    out(answer)


# A Browser API is one address over several browsers; its type is "controller".
BROWSER_API = "controller"


def member_path(api, browser):
    return f"{workload_path(api)}/browsers/{urllib.parse.quote(browser)}"


def browser_api_body(name, browsers, every, remotes, place=None):
    names = [b.strip() for b in (browsers or "").split(",") if b.strip()]
    if every and names:
        raise Problem("--all already means every browser in the workspace", "leave out --browsers", EXIT_OTHER)
    ext = []
    for r in remotes or []:
        rid, _, ws = r.partition("=")
        if not rid or not ws.startswith(("ws://", "wss://")):
            raise Problem(f"--remote takes ID=wss://address, got {r!r}", "fix --remote", EXIT_OTHER)
        ext.append({"id": rid, "wsUrl": ws})
    if not every and not names and not ext:
        raise Problem("a Browser API needs browsers",
                      "pass --browsers a,b, or --all for every browser in the workspace", EXIT_OTHER)
    body = {"id": name, "autodiscover": bool(every)}
    if names:
        body["browsers"] = names
    if ext:
        body["externalBrowsers"] = ext
    if place:
        body["placement"] = place
    return body


def browser_api(args):
    host, region = getattr(args, "host", None), getattr(args, "region", None)
    if args.action != "create" and (host is not None or region is not None):
        raise Problem("--host and --region are for create",
                      f"to move it: llc.py set {args.name} --json FILE --yes, the file holding {{\"controller\": {{\"placement\": ...}}}}", EXIT_OTHER)
    tok = token()
    if args.action == "create":
        if not args.yes:
            raise Problem("creating needs --yes",
                          "only create a Browser API the user asked for; the browsers you put in it are reached by "
                          "whatever reaches it, so ask the user and wait for their agreement (SKILL.md rule 11), "
                          "then pass --yes", EXIT_OTHER)
        body = browser_api_body(args.name, args.browsers, args.all, args.remote, placement(host, region))
        request("POST", f"/v1/workloads/{BROWSER_API}", body, token=tok)
        out({"created": args.name, "type": BROWSER_API,
             "next": f"llc.py wait {args.name} then llc.py connect {args.name} --tool api"})
        return
    if args.action in ("add", "remove"):
        if not args.browser:
            raise Problem("which browser?", f"llc.py browser-api {args.action} {args.name} BROWSER", EXIT_OTHER)
        if args.action == "add":
            if not args.yes:
                raise Problem("putting a browser in needs --yes",
                              "whatever reaches the Browser API then drives the browser: ask the user and wait for "
                              "their agreement (SKILL.md rule 11), then pass --yes", EXIT_OTHER)
            request("PUT", member_path(args.name, args.browser), {}, token=tok)
            out({"added": args.browser, "to": args.name})
        else:
            if not args.yes:
                raise Problem("taking a browser out needs --yes",
                              "its open sessions end; ask the user first, then pass --yes", EXIT_OTHER)
            request("DELETE", member_path(args.name, args.browser), token=tok)
            out({"tookOut": args.browser, "of": args.name})
        return
    # show: the browsers it drives, and how each is doing
    spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
    w = next((x for x in spec.get("workloads", []) if x.get("id") == args.name), None)
    if w is None or w.get("type") != BROWSER_API:
        raise Problem(f"no Browser API {args.name}", f"llc.py ls --type {BROWSER_API}", EXIT_OTHER)
    c = w.get("controller") or {}
    live = next((x for x in request("GET", "/v1/status", token=tok).get("workloads", []) if x.get("id") == args.name), {})
    out({
        "id": args.name,
        "drives": "every browser in the workspace" if c.get("autodiscover") else "only these",
        "browsers": c.get("browsers") or [],
        "remoteBrowsers": [e.get("id") for e in c.get("externalBrowsers") or []],
        "state": live.get("phase", "unknown"),
        "ready": live.get("ready", False),
        "answering": live.get("browsers", []),
    })


# --- browsers: language, proxies, profiles ----------------------------------

ROTATE_TIMEOUT = 160  # a rotation with a mobile change-IP call takes up to two minutes
PROFILE_TIMEOUT = 660  # taking or restoring a snapshot: up to ten minutes
ARCHIVE_TIMEOUT = 3660  # an export, an import or a copy: up to an hour
CHUNK = 1 << 20
# Fields of a proxy that are written and never shown again.
PROXY_SECRETS = ("username", "password", "changeIpUrl")


def proxy_path(wid):
    return f"{workload_path(wid)}/proxy"


def profile_path(wid):
    return f"{workload_path(wid)}/profile"


def secret_env(var, what):
    """The value of an environment variable the user filled, or None when no
    variable was named. Secrets never go on the command line."""
    if var is None:
        return None
    value = os.environ.get(var, "")
    if not value:
        raise Problem(f"{var} is empty or not set", f"ask the user for {what} and put it in {var}; never on the command line", EXIT_USER)
    return value


def read_json_file(path, what):
    try:
        return json.loads(Path(path).read_text())
    except OSError as e:
        raise Problem(f"can't read {path}: {e.strerror}", f"write {what} to the file first", EXIT_OTHER) from None
    except json.JSONDecodeError as e:
        raise Problem(f"{path} is not JSON: {e.msg} at line {e.lineno}", "fix the file", EXIT_OTHER) from None


def check_proxy_block(block, where="the proxy settings"):
    """Refuse a proxy block that would put a login where settings are shown;
    True when it carries a login or a change-IP address (the file holding it
    should be deleted once sent)."""
    if not isinstance(block, dict):
        raise Problem(f"{where} should be a JSON object: {{\"upstreams\": [...], \"rotation\": {{...}}}}", "fix the file", EXIT_OTHER)
    ups = block.get("upstreams")
    if ups is not None and not isinstance(ups, list):
        raise Problem(f"{where}: upstreams should be a list", "fix the file", EXIT_OTHER)
    secret = False
    for i, u in enumerate(ups or []):
        if not isinstance(u, dict):
            raise Problem(f"{where}: upstreams[{i}] should be an object", "fix the file", EXIT_OTHER)
        if "@" in str(u.get("server") or ""):
            raise Problem(f"{where}: upstreams[{i}].server has a login in the address",
                          "put the login in \"username\" and \"password\" and keep the address as scheme://host:port", EXIT_OTHER)
        if ("username" in u) != ("password" in u):
            raise Problem(f"{where}: upstreams[{i}] needs username and password together",
                          "send both, or neither to keep the stored login", EXIT_OTHER)
        secret = secret or any(u.get(k) for k in PROXY_SECRETS)
    return secret


def settings_proxy(body):
    """The proxy block inside a create or set file, if any: top level or
    under "browser"."""
    if not isinstance(body, dict):
        return None
    inner = body.get("browser")
    if isinstance(inner, dict) and "proxy" in inner:
        return inner["proxy"]
    return body.get("proxy")


def check_settings_proxy(body, path):
    """A create or set file: check its proxy block like proxy set does; the
    reminder to delete the file when it holds a login, else None."""
    block = settings_proxy(body)
    if block is None or (isinstance(block, dict) and block.get("remove") is True):
        return None
    if check_proxy_block(block, f"the proxy settings in {path}"):
        return f"delete {path}: it holds the proxy's login, which LiveLLM keeps and never shows again"
    return None


def proxy_body(path):
    """The proxy settings to send: the file holds the whole block, or
    {"proxy": {...}}. A login written into the address is refused here, so it
    never reaches settings that are shown."""
    body = read_json_file(path, "the proxy settings")
    if isinstance(body, dict) and set(body) == {"proxy"}:
        body = body["proxy"]
    return body, check_proxy_block(body)


def proxy(args):
    tok = token()
    if args.action == "show":
        out(request("GET", proxy_path(args.id), token=tok))
        return
    if not args.yes:
        why = ("it drops open connections and changes the exit for everyone on the browser, a person in the live view included"
               if args.action == "rotate" else "it changes how the browser goes out")
        raise Problem(f"proxy {args.action} needs --yes",
                      f"{why}: ask the user and wait for their agreement (SKILL.md rule 10), then pass --yes", EXIT_OTHER)
    if args.action == "rotate":
        body = {"to": args.to} if args.to else {}
        out(request("POST", f"{proxy_path(args.id)}/rotate", body, token=tok, timeout=ROTATE_TIMEOUT))
        return
    if args.action == "set":
        if not args.json:
            raise Problem("proxy set needs --json FILE", "write the proxy settings to a file (references/proxies.md)", EXIT_OTHER)
        body, secret = proxy_body(args.json)
        answer = request("PUT", proxy_path(args.id), body, token=tok, timeout=ROTATE_TIMEOUT) or {}
        if secret:
            answer = {**answer, "next": f"delete {args.json}: it holds the proxy's login, which LiveLLM keeps and never shows again"}
        out(answer)
        return
    if args.action == "clear":
        out(request("DELETE", proxy_path(args.id), token=tok) or
            {"direct": args.id, "note": "the browser goes out directly now; its proxy list is empty and it did not restart"})
        return
    # remove: the proxy settings go altogether, and the browser restarts
    out(request("DELETE", proxy_path(args.id) + "?remove=true", token=tok) or
        {"removed": "proxy", "of": args.id, "next": f"it restarts: llc.py wait {args.id}"})


def open_stream(method, path, tok, data=None, headers=None, timeout=ARCHIVE_TIMEOUT):
    """An API call whose answer (or body) is a file: returns the open answer
    for the caller to read and close, or raises Problem."""
    url = API + path
    h = {"authorization": "Bearer " + tok, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode(errors="replace")
        finally:
            e.close()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"error": text.strip()[:200] or e.reason}
        raise http_problem(e.code, payload if isinstance(payload, dict) else {"error": str(payload)}) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException, OSError) as e:
        raise unreachable(e) from None


def export_profile(args, tok):
    if not args.out:
        raise Problem("export needs --out FILE", "name the file the profile is saved to", EXIT_OTHER)
    target = Path(args.out)
    if target.exists():
        raise Problem(f"{target} already exists", "pick a new file name; an export never overwrites one", EXIT_OTHER)
    body = {}
    if args.snapshot:
        body["snapshot"] = args.snapshot
    password = secret_env(args.password_env, "the password that protects the file")
    if password:
        body["password"] = password
    part = target.with_name(target.name + ".part")
    # The file is made before the export is asked for (an export of the profile
    # as it is now closes the browser's tabs), and only by this run. It holds
    # the browser's sign-ins: only this user may read it.
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Problem(f"{part} already exists: another export to {target} is running, or one broke off",
                      f"if no other export is running, delete {part} and run this again; or pick another --out", EXIT_OTHER) from None
    except OSError as e:
        raise Problem(f"can't write {part}: {e.strerror}", "pick a folder you can write to for --out", EXIT_OTHER) from None
    size, saved = 0, False
    try:
        with os.fdopen(fd, "wb") as f:
            resp = open_stream("POST", f"{profile_path(args.id)}/export", tok, json.dumps(body).encode(),
                               {"content-type": "application/json", "accept": "application/octet-stream"})
            with resp:
                try:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        size += len(chunk)
                except (http.client.HTTPException, ConnectionError, TimeoutError) as e:
                    raise Problem(f"the export broke off after {size} bytes: {e}", "run the export again", EXIT_OTHER) from None
                if resp.length:  # the answer said how long it is, and less came
                    raise Problem(f"the export broke off after {size} bytes", "run the export again", EXIT_OTHER)
        saved = keep_as(part, target)
    finally:
        if not saved:
            try:
                part.unlink()
            except OSError:
                pass
    answer = {"exported": args.id, "file": str(target), "bytes": size, "encrypted": bool(password)}
    answer["note"] = ("the file holds the browser's sign-ins; give it only to the user" if password else
                      "the file holds the browser's sign-ins unencrypted; give it only to the user and delete your copy")
    out(answer)


def keep_as(part, target):
    """Give the finished file its name without replacing a file of that name
    that appeared meanwhile. True once it has it."""
    try:
        os.link(part, target)
    except FileExistsError:
        raise Problem(f"{target} appeared while the profile was being saved; the export is kept as {part}",
                      f"rename {part} to a new name; neither file was overwritten", EXIT_OTHER) from None
    except OSError:
        # No hard links here: claim the name, then put the file in its place.
        try:
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            raise Problem(f"{target} appeared while the profile was being saved; the export is kept as {part}",
                          f"rename {part} to a new name; neither file was overwritten", EXIT_OTHER) from None
        os.replace(part, target)
        return True
    try:
        part.unlink()
    except OSError:
        pass
    return True


class _Answer(io.RawIOBase):
    """What the server already sent, then the rest of it from the socket."""

    def __init__(self, sent, sock):
        self.sent, self.sock = bytearray(sent), sock

    def readable(self):
        return True

    def readinto(self, b):
        if self.sent:
            n = min(len(b), len(self.sent))
            b[:n] = self.sent[:n]
            del self.sent[:n]
            return n
        try:
            return self.sock.recv_into(b)
        except ConnectionResetError:
            return 0  # the server closed once it had answered


class _Answered:
    """Stands in for the socket so http.client reads an answer whose start
    was already taken off it."""

    def __init__(self, sent, sock):
        self.sent, self.sock = sent, sock

    def makefile(self, *_args, **_kw):
        return io.BufferedReader(_Answer(self.sent, self.sock))


def connection(url, timeout):
    """An http.client connection for url, through the proxy urllib would use.
    Returns it with the request target and any proxy header."""
    parts = urllib.parse.urlsplit(url)
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    ctx = ssl.create_default_context()
    proxy = urllib.request.getproxies().get(parts.scheme)
    if proxy and urllib.request.proxy_bypass(parts.hostname or ""):
        proxy = None
    if not proxy:
        if parts.scheme == "https":
            return http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout, context=ctx), target, {}
        return http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout), target, {}
    pp = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
    auth = {}
    if pp.username is not None:
        login = urllib.parse.unquote(pp.username) + ":" + urllib.parse.unquote(pp.password or "")
        auth = {"Proxy-Authorization": "Basic " + base64.b64encode(login.encode()).decode()}
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(pp.hostname, pp.port or 80, timeout=timeout, context=ctx)
        conn.set_tunnel(parts.hostname, parts.port, headers=auth)
        return conn, target, {}
    return http.client.HTTPConnection(pp.hostname, pp.port or 80, timeout=timeout), url, auth


def answer_waiting(sock):
    """What the server sent while the file was still going out: bytes of its
    answer, b"" when nothing of an answer has come (TLS housekeeping), None
    when it closed."""
    saved = sock.gettimeout()
    sock.settimeout(0)
    try:
        data = sock.recv(1 << 16)
        return data if data else None
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError, InterruptedError):
        return b""
    except ConnectionResetError:
        return None
    finally:
        sock.settimeout(saved)


SEND = 1 << 16


def upload(path, tok, f, headers, timeout=ARCHIVE_TIMEOUT):
    """POST a file and return the decoded answer, or raise Problem. The
    server may answer before the whole file is sent (a refusal, a file it
    won't take): that answer is read and reported, not taken for a network
    fault."""
    conn, target, extra = connection(API + path, timeout)
    try:
        try:
            conn.putrequest("POST", target, skip_accept_encoding=True)
            for k, v in {**extra, "authorization": "Bearer " + tok, **headers}.items():
                conn.putheader(k, v)
            conn.endheaders()
        except (OSError, http.client.HTTPException) as e:
            raise unreachable(e) from None
        sock, sent, cut = conn.sock, None, None
        deadline = time.monotonic() + timeout
        try:
            while True:
                pending = isinstance(sock, ssl.SSLSocket) and sock.pending()
                readable, writable, _ = select.select([sock], [sock], [], 0 if pending else max(0.0, deadline - time.monotonic()))
                if pending or readable:
                    sent = answer_waiting(sock)
                    if sent != b"":
                        break  # an answer came early, or the server closed
                    sent = None
                    if not writable:
                        continue
                if not writable:
                    if time.monotonic() >= deadline:
                        raise unreachable(TimeoutError("timed out sending the file"))
                    continue
                chunk = f.read(SEND)
                if not chunk:
                    break
                sock.sendall(chunk)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError) as e:
            cut = e  # the server stopped taking the file; its answer may be waiting
        try:
            if sent is not None or cut is not None:
                resp = http.client.HTTPResponse(_Answered(sent or b"", sock), method="POST")
                resp.begin()
            else:
                resp = conn.getresponse()
            status, reason = resp.status, resp.reason
            text = resp.read().decode(errors="replace")
        except (OSError, http.client.HTTPException) as e:
            raise unreachable(cut or e) from None
    finally:
        conn.close()
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        payload = {"error": text.strip()[:200] or reason} if status >= 400 else {}
    if not isinstance(payload, dict):
        payload = {"error": str(payload)} if status >= 400 else {}
    if status >= 400:
        raise http_problem(status, payload)
    return payload


def import_profile(args, tok):
    if not args.file:
        raise Problem("import needs --file FILE", "name the profile file exported from a LiveLLM browser", EXIT_OTHER)
    src = Path(args.file)
    try:
        size = src.stat().st_size
        f = src.open("rb")
    except OSError as e:
        raise Problem(f"can't read {src}: {e.strerror}", "check the file name", EXIT_OTHER) from None
    headers = {"content-type": "application/octet-stream", "content-length": str(size), "accept": "application/json"}
    password = secret_env(args.password_env, "the file's password")
    if password:
        headers["x-profile-password"] = password
    path = f"{profile_path(args.id)}/import" + ("?force=1" if args.force else "")
    with f, may_place_profile():
        answer = upload(path, tok, f, headers)
    out(answer or {"imported": str(src), "into": args.id, "note": "its tabs closed while the profile was put in place"})


@contextlib.contextmanager
def may_place_profile():
    """Import and copy put a profile in a browser: a plain 403 is the right to
    change that browser (a copy also reads the one it copies from)."""
    try:
        yield
    except Problem as p:
        if p.status == 403 and "profile" not in p.message.lower():
            p.next = ("putting a profile in a browser needs Manage, or Create on a browser this agent made (a copy also needs "
                      "Connect on the browser it copies from): tell the user; a person changes what this agent may do on the "
                      "console's Agents page (for an API key, on the Keys page)")
        raise


def profile(args):
    tok = token()
    if args.action == "show":
        out(request("GET", profile_path(args.id), token=tok))
        return
    if not args.yes:
        why = {"snapshot": "its tabs close for a few seconds",
               "restore": "it replaces the browser's profile and its tabs close for a few seconds",
               "rm": "a snapshot can't be brought back",
               "export": "the file holds the browser's sign-ins",
               "import": "it replaces the browser's profile",
               "copy": "it replaces this browser's profile with the other's"}[args.action]
        rule = " (SKILL.md rule 10)" if args.action in ("export", "import", "copy") else ""
        raise Problem(f"profile {args.action} needs --yes", f"{why}: ask the user and wait for their agreement{rule}, then pass --yes",
                      EXIT_OTHER)
    if args.action in ("restore", "rm") and not args.snapshot:
        raise Problem("which snapshot?", f"llc.py profile show {args.id} lists them; pass --snapshot ID", EXIT_OTHER)
    snap = f"{profile_path(args.id)}/snapshots"
    if args.action == "snapshot":
        body = {"name": args.name} if args.name else {}
        out(request("POST", snap, body, token=tok, timeout=PROFILE_TIMEOUT))
    elif args.action == "restore":
        out(request("POST", f"{snap}/{urllib.parse.quote(args.snapshot)}/restore", {"keepCurrent": bool(args.keep_current)},
                    token=tok, timeout=PROFILE_TIMEOUT))
    elif args.action == "rm":
        request("DELETE", f"{snap}/{urllib.parse.quote(args.snapshot)}", token=tok)
        out({"deleted": args.snapshot, "of": args.id})
    elif args.action == "export":
        export_profile(args, tok)
    elif args.action == "import":
        import_profile(args, tok)
    else:  # copy
        if not args.source:
            raise Problem("copy needs --from BROWSER", "name the browser whose profile to copy", EXIT_OTHER)
        body = {"from": args.source}
        if args.snapshot:
            body["snapshot"] = args.snapshot
        with may_place_profile():
            answer = request("POST", f"{profile_path(args.id)}/copy", body, token=tok, timeout=ARCHIVE_TIMEOUT)
        out(answer or {"copied": args.source, "into": args.id})


def cookies(args):
    """Add cookies to a running browser. The values are never printed."""
    if not args.yes:
        raise Problem("cookies needs --yes", "cookies are sign-ins: add only the ones the user gave you, ask the user and wait for "
                      "their agreement (SKILL.md rule 10), then pass --yes", EXIT_OTHER)
    items = read_json_file(args.json, "the cookies")
    if isinstance(items, dict) and isinstance(items.get("cookies"), list):
        items = items["cookies"]
    if not isinstance(items, list) or not items:
        raise Problem("the file should hold a JSON list of cookies: [{\"name\", \"value\", \"domain\", \"path\"}, ...]",
                      "fix the file", EXIT_OTHER)
    answer = request("POST", f"{workload_path(args.id)}/cookies", items, token=token())
    result = {"added": len(items), "to": args.id, **({k: v for k, v in answer.items() if k != "cookies"} if isinstance(answer, dict) else {})}
    if isinstance(result.get("dropped"), int) and result["dropped"] > 0:
        # a Camoufox browser leaves out what Firefox refuses (a SameSite=None
        # cookie without Secure, for one); the values are still never printed
        result["note"] = f"{result['dropped']} of the cookies were not taken: the browser refuses them as they are"
    out(result)


def engines(_args):
    """The browser engines this LiveLLM offers: Chrome, and Camoufox where it is offered.
    Asked with the sign-in or key when there is one; without one, or with one
    the list won't take, the public list."""
    path = "/v1/browsers/engines"
    try:
        tok = token()
    except Problem:
        tok = None  # not signed in: the public list
    try:
        try:
            answer = request("GET", path, token=tok)
        except Problem as p:
            if not tok or p.status not in (401, 403):
                raise
            answer = request("GET", path)  # a sign-in this list won't take: the public list
    except Problem as p:
        if p.status != 404:
            raise
        # a LiveLLM from before engines: its browsers are all Chrome
        answer = {"engines": [{"id": "chrome", "name": "Chrome", "protocol": "cdp", "default": True}],
                  "note": "this LiveLLM offers only Chrome browsers"}
    out(answer)


def locales(_args):
    """The languages a browser can take, and the time zone names."""
    out(request("GET", "/v1/browsers/locales"))


def backups_path(wid):
    return f"{workload_path(wid)}/backups"


def take_backup(args):
    """Back up a machine or a database now. A machine's is live unless --clean
    (the machine must be stopped); a database's is a full copy."""
    body = {}
    if args.clean:
        body["mode"] = "clean"
    if args.name:
        body["name"] = args.name
    res = request("POST", backups_path(args.id), body, token=token())
    out(res or {"backingUp": args.id, "next": f"llc.py backups {args.id}"})


def restore_body(new_id, at, password, place=None):
    """What a database's restore sends: the new database's id and password,
    a moment when it restores to a minute rather than to the end of the
    backup, and where the new database runs (automatic when left out, never
    copied from the original)."""
    body = {"id": new_id, "credentials": {"password": password}}
    if at:
        body["pointInTime"] = at
    if place:
        body["placement"] = place
    return body


def restore(args):
    """A database restores into a NEW database and keeps running as it is; a
    machine goes back in place and has to be stopped first."""
    host, region = getattr(args, "host", None), getattr(args, "region", None)
    place = placement(host, region)  # refuses both before anything is sent
    tok = token()
    spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
    w = next((x for x in spec.get("workloads", []) if x.get("id") == args.id), None)
    if w is None:
        raise Problem(f"no resource {args.id}", "run: llc.py ls, the id is probably wrong", EXIT_OTHER)
    kind = w.get("type", "")
    path = f"{backups_path(args.id)}/{urllib.parse.quote(args.backup)}/restore"
    if kind.startswith("vm-"):
        if args.as_id or args.at or args.password_env or place:
            raise Problem("a machine restores in place", "drop --as, --at, --password-env, --host and --region", EXIT_OTHER)
        res = request("POST", path, token=tok)
        out(res or {"restoring": args.backup, "to": args.id, "next": f"llc.py start {args.id} once it is done"})
        return
    if kind != "storage":
        raise Problem(f"{args.id} has no backups", "only machines and databases have backups", EXIT_OTHER)
    if not args.as_id:
        raise Problem(f"a database restores into a new one; {args.id} keeps running as it is",
                      f"pass --as NEW-ID, e.g. --as {args.id}-restored", EXIT_OTHER)
    password = os.environ.get(args.password_env or "", "")
    if not password:
        raise Problem("the new database needs a password",
                      "generate one, put it in an environment variable and pass --password-env VAR", EXIT_OTHER)
    res = request("POST", path, restore_body(args.as_id, args.at, password, place), token=tok) or {}
    res.setdefault("id", args.as_id)
    res["next"] = f"llc.py wait {args.as_id}"
    out(res)


def build(args):
    """Build an app from its repository; with --wait, follow that build until
    it is live or has failed."""
    tok = token()
    started = request("POST", f"{workload_path(args.id)}/build", {}, token=tok) or {}
    if not args.wait:
        out({"building": args.id, "buildId": started.get("buildId", ""),
             "next": f"llc.py progress {args.id}, or build {args.id} --wait"})
        return
    want, deadline = started.get("buildId", ""), time.time() + args.timeout
    while True:
        p = request("GET", f"{workload_path(args.id)}/build-progress", token=tok)
        ours = not want or not p.get("buildId") or p.get("buildId") == want
        if ours and p.get("failed"):
            tail = [l.get("body", "") for l in (p.get("logs") or [])][-20:]
            raise Problem(f"the build failed: {p.get('message', '')}\n" + "\n".join(tail),
                          "read the log lines, fix the repository, then build again", EXIT_OTHER, 422)
        if ours and p.get("done"):
            out({"id": args.id, "live": True, "buildId": p.get("buildId"), "commit": p.get("commit")})
            return
        if ours and p.get("stage") == "none":
            raise Problem(f"{args.id} isn't built from a repository", "only an app with a Git source builds", EXIT_OTHER)
        if time.time() > deadline:
            raise Problem(f"the build isn't live yet ({p.get('stage')}: {p.get('message', '')})",
                          f"llc.py progress {args.id} later; don't start another build", EXIT_NOT_READY)
        time.sleep(BUILD_POLL)


BUILD_POLL = 10


def find_template(ref, tok):
    items = request("GET", "/v1/templates", token=tok).get("templates") or []
    for t in items:
        if t.get("id") == ref:
            return t
    named = [t for t in items if t.get("name") == ref]
    if len(named) == 1:
        return named[0]
    if not named:
        raise Problem(f"no template {ref}", "llc.py templates lists them", EXIT_OTHER)
    raise Problem(f"{len(named)} templates are called {ref}", "use the id from llc.py templates", EXIT_OTHER)


# What a create from a template takes besides the new id: the secrets the
# template left out, and where everything it makes runs. Anything else would
# be dropped without a word.
TEMPLATE_BODY_KEYS = {"secretEnv", "imagePassword", "gitToken", "portPasswords", "credentials", "services", "placement"}


def stack_services(t, with_secret=None):
    """The names of a Composable App template's services (those with the secret env name)."""
    names = []
    for sv in ((t.get("config") or {}).get("stack") or {}).get("services") or []:
        env = [e.get("name") for e in ((sv.get("pod") or {}).get("secretEnv") or [])]
        if sv.get("name") and (with_secret is None or with_secret in env):
            names.append(sv["name"])
    return names


def set_path(body, parts, value, path):
    m = body
    for i, k in enumerate(parts):
        if not k:
            raise Problem(f"--secret {path}: an empty name in the path", "fix the path", EXIT_OTHER)
        if i == len(parts) - 1:
            m[k] = value
            return
        if k in m and not isinstance(m[k], dict):
            raise Problem(f"--secret {path}: {'.'.join(parts[:i + 1])} already holds a value", "fix the path", EXIT_OTHER)
        m = m.setdefault(k, {})


def secret_path(path):
    """A --secret path split at its dots, a port password's username kept
    whole: a username may hold dots (alice.smith, a@b.com); service, port and
    secret env names never do."""
    parts = path.split(".")
    if parts[0] == "portPasswords":
        return path.split(".", 2)
    if parts[0] == "services" and len(parts) > 2 and parts[2] == "portPasswords":
        return path.split(".", 4)
    return parts


def put_secret(body, t, path, value):
    """One --secret into the create body. A path is what a refusal lists as
    missing (secretEnv.API_KEY, imagePassword, credentials.password,
    portPasswords.http.alice, services.web.secretEnv.API_KEY); a bare name is a
    secret env value. In a Composable App's template a secret belongs to a
    service: without services.<name> it goes to each service that has that
    secret env name, or to the only service there is."""
    parts = secret_path(path)
    if len(parts) == 1 and path in TEMPLATE_BODY_KEYS and path not in ("imagePassword", "gitToken"):
        raise Problem(f"--secret {path}: say what in it, e.g. {path}.NAME=…", "fix the path", EXIT_OTHER)
    if len(parts) == 1 and path not in TEMPLATE_BODY_KEYS:
        parts = ["secretEnv", path]
    if t.get("kind") != "stack":
        if parts[0] == "services":
            raise Problem(f"--secret {path}: {t.get('name')} isn't a Composable App's template", "leave out services.<name>", EXIT_OTHER)
        set_path(body, parts, value, path)
        return
    if parts[0] == "services":
        set_path(body, parts, value, path)
        return
    if parts[0] == "secretEnv" and len(parts) == 2:
        svcs = stack_services(t, parts[1])
        if not svcs:
            raise Problem(f"--secret {path}: no service of the template {t.get('name')} has a secret {parts[1]}",
                          f"llc.py template show {t.get('id')} lists what each service needs", EXIT_OTHER)
    else:
        svcs = stack_services(t)
        if len(svcs) != 1:
            raise Problem(f"--secret {path}: say which service it is for",
                          f"services.<name>.{path}=…, the services being {', '.join(svcs)}", EXIT_OTHER)
    for svc in svcs:
        set_path(body, ["services", svc] + parts, value, path)


def secret_flags(missing):
    """The flags that give the secrets a template still needs, each value from a variable."""
    flags = []
    for m in missing:
        name = m[len("secretEnv."):] if m.startswith("secretEnv.") and m.count(".") == 1 else m
        flags.append(f"--secret-env {name}=VAR")
    return " ".join(flags)


def template_create_body(t, new_id, secrets, secret_envs, json_file, place=None):
    """What a create from a template sends: the new id, the secrets it left
    out, and `place` (where everything it makes runs) when given. Without a
    location the template's own is kept; {"strategy": "auto"} makes them
    automatic."""
    body = {}
    if json_file:
        body = json.loads(Path(json_file).read_text())
        if not isinstance(body, dict):
            raise Problem("the file should hold a JSON object with the secrets", "fix the file", EXIT_OTHER)
        for k in body:
            if k not in TEMPLATE_BODY_KEYS:
                raise Problem(f"{json_file}: {k!r} can't be given here — a create from a template takes only the secrets it needs "
                              "(secretEnv, imagePassword, gitToken, portPasswords, credentials, services) and where it runs (placement)",
                              f"create it, then change settings with llc.py set {new_id} --json FILE --yes", EXIT_OTHER)
            if k == "placement":
                if not isinstance(body[k], dict) or not body[k].get("strategy"):
                    raise Problem(f"{json_file}: placement states its strategy here",
                                  'use {"strategy": "region", "region": R}, {"strategy": "host", "host": H} '
                                  'or {"strategy": "auto"}', EXIT_OTHER)
                if place:
                    raise Problem(f"{json_file}: placement is in the file", "leave out --host, --region and --automatic", EXIT_OTHER)
                continue
            if (t.get("kind") == "stack") != (k == "services"):
                raise Problem(f"{json_file}: {k!r} doesn't fit a template of kind {t.get('kind')}",
                              "a Composable App's secrets go under services.<name>; any other template's at the top", EXIT_OTHER)
    given = []
    for s in secrets or []:
        path, eq, value = s.partition("=")
        if not eq or not path:
            raise Problem(f"--secret takes PATH=VALUE, got {path!r}", "fix the flag", EXIT_OTHER)
        given.append((path, value))
    for s in secret_envs or []:
        path, eq, var = s.partition("=")
        if not eq or not path or not var:
            raise Problem(f"--secret-env takes PATH=VAR (the variable holding the value), got {s!r}", "fix the flag", EXIT_OTHER)
        if not os.environ.get(var):
            raise Problem(f"--secret-env {path}: the variable {var} is empty or unset", f"set {var} first", EXIT_OTHER)
        given.append((path, os.environ[var]))
    for path, value in given:
        put_secret(body, t, path, value)
    if place:
        body["placement"] = place
    body["name" if t.get("kind") == "stack" else "id"] = new_id
    return body


def template_placement(args):
    """Where a create from a template puts what it makes: --host, --region,
    --automatic ({"strategy": "auto"}: it must be sent, or the template's own
    location is kept), or None to keep the template's."""
    place = placement(getattr(args, "host", None), getattr(args, "region", None))
    if getattr(args, "automatic", False):
        if place:
            raise Problem("--automatic, --host or --region: one of them", "pick one", EXIT_OTHER)
        return {"strategy": "auto"}
    return place


def template(args):
    # a location is for use only, and refused before anything is sent
    if args.action != "use" and (getattr(args, "host", None) is not None or getattr(args, "region", None) is not None
                                 or getattr(args, "automatic", False)):
        raise Problem("--host, --region and --automatic are for use", f"llc.py template use {args.ref} NEW-ID --region R --yes", EXIT_OTHER)
    place = template_placement(args) if args.action == "use" else None
    tok = token()
    if args.action == "save":
        if not args.source:
            raise Problem("save which resource?", f"llc.py template save {args.ref} --from ID", EXIT_OTHER)
        # LiveLLM reads the resource's settings, never a secret: an app of a
        # Composable App saves the whole app, with its databases and links
        body = {"name": args.ref, "from": args.source}
        if args.description:
            body["description"] = args.description
        out(request("POST", "/v1/templates", body, token=tok))
        return
    t = find_template(args.ref, tok)
    if args.action == "show":
        out(t)
        return
    if args.action == "rm":
        if not args.yes:
            raise Problem("deleting a template needs --yes", "ask the user first", EXIT_OTHER)
        request("DELETE", f"/v1/templates/{urllib.parse.quote(t['id'])}", token=tok)
        out({"deleted": t["id"], "name": t.get("name")})
        return
    # use: a new resource from the template, with the secrets it left out
    if not args.new_id or not args.yes:
        raise Problem("a new resource needs its id and --yes",
                      f"llc.py template use {args.ref} NEW-ID [--secret-env NAME=VAR] --yes, once the user asked for it", EXIT_OTHER)
    body = template_create_body(t, args.new_id, args.secret, args.secret_env, args.json, place)
    try:
        made = request("POST", f"/v1/templates/{urllib.parse.quote(t['id'])}/create", body, token=tok)
    except Problem as p:
        if p.missing:
            p.next = ("put each value in an environment variable (generate a password, ask the user for a key) and run it "
                      "again with " + secret_flags(p.missing))
        raise
    created = made.get("created") or [args.new_id]
    answer = {"created": created if t.get("kind") == "stack" else args.new_id, "type": t["kind"], "template": t.get("name"),
              "next": f"llc.py wait {created[0]}"}
    if made.get("databases"):
        answer["databases"] = made["databases"]
    out(answer)


def activity(args):
    q = {k: v for k, v in (("actor", args.actor), ("limit", args.limit), ("before", args.before)) if v}
    if args.object:
        q["object"] = args.object if ":" in args.object else "workload:" + args.object
    path = "/v1/activity" + ("?" + urllib.parse.urlencode(q) if q else "")
    out(request("GET", path, token=token()))


def monitoring(args):
    if not args.id:
        out(request("GET", "/v1/monitoring", token=token()))
        return
    path = f"{workload_path(args.id)}/monitor" + (f"?range={urllib.parse.quote(args.range)}" if args.range else "")
    out(request("GET", path, token=token()))


# A delete clears away what belonged to the resource before it answers, which
# can take a while; hanging up early could leave some of it behind.
RM_TIMEOUT = 180


def workspace_workloads(tok):
    return request("GET", "/v1/workspace", token=tok).get("spec", {}).get("workloads", [])


def rm(args):
    tok = token()
    q = {k: "true" for k, on in (("force", args.force), ("withDatabases", args.with_databases)) if on}
    path = workload_path(args.id) + ("?" + urllib.parse.urlencode(q) if q else "")
    # the databases made with the app, to say which went should the answer be lost
    made_with = []
    if args.with_databases:
        made_with = [w["id"] for w in workspace_workloads(tok)
                     if args.id in ((w.get("storage") or {}).get("createdWith") or [])]
    try:
        gone = request("DELETE", path, token=tok, timeout=RM_TIMEOUT)
    except Problem as p:
        if p.status is not None:
            raise  # refused: nothing was deleted
        # no answer came back; LiveLLM may have done it all the same: look
        left = {w["id"] for w in workspace_workloads(tok)}
        if args.id in left:
            raise
        answer = {"deleted": args.id, "note": "LiveLLM's answer was lost on the way; the workspace shows it deleted"}
        if args.with_databases:
            answer["databases"] = {"deleted": [d for d in made_with if d not in left],
                                   "kept": [d for d in made_with if d in left]}
        out(answer)
        return
    answer = {"deleted": args.id}
    if isinstance(gone, dict) and gone.get("databases"):
        answer["databases"] = gone["databases"]  # {deleted, kept}
    out(answer)


def main():
    p = argparse.ArgumentParser(prog="llc.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    login_p = sub.add_parser("login", help="sign this agent in: prints a link for the user; run it again once they allow it")
    login_p.add_argument("--access", choices=["use", "create", "full"], default=None,
                         help="use, create or full (default: create, or the pending sign-in's)")
    login_p.add_argument("--wait", action="store_true", help="for a person at a terminal: wait here until the link is allowed")
    login_p.set_defaults(fn=login)
    sub.add_parser("logout", help="end this sign-in").set_defaults(fn=logout)
    sub.add_parser("whoami", help="workspace, access and usage").set_defaults(fn=whoami)

    logs_p = sub.add_parser("logs", help="recent log lines and how the containers are doing")
    logs_p.add_argument("id")
    logs_p.add_argument("--lines", type=int, default=100)
    logs_p.set_defaults(fn=logs)

    sub.add_parser("ssh-keys", help="the workspace's SSH keys (read-only)").set_defaults(fn=ssh_keys)

    ls_p = sub.add_parser("ls", help="list resources")
    ls_p.add_argument("--type")
    ls_p.set_defaults(fn=ls)

    sub.add_parser("hosts", help="where resources can run: each host's id, region and free room").set_defaults(fn=hosts)

    create_p = sub.add_parser("create", help="create a resource from a JSON file (type apps: several at once, with their databases)")
    create_p.add_argument("type")
    create_p.add_argument("--json", required=True)
    create_p.add_argument("--join", help="type apps: add them to this existing app (or stack) in the same step")
    create_p.add_argument("--engine", choices=["chrome", "camoufox"],
                          help="browser: the browser engine, set for good when it is made (default chrome)")
    create_p.add_argument("--yes", action="store_true", required=True, help="the user asked for this resource")
    create_p.set_defaults(fn=create)

    wait_p = sub.add_parser("wait", help="wait until a resource is ready")
    wait_p.add_argument("id")
    wait_p.add_argument("--timeout", type=int, default=600)
    wait_p.set_defaults(fn=wait)

    connect_p = sub.add_parser("connect", help="how to reach a resource's tool")
    connect_p.add_argument("id")
    connect_p.add_argument("--tool", choices=["cdp", "view", "api", "computer"])
    connect_p.add_argument("--screen-width", type=int, help="computer: shrink screenshots to this many pixels wide (320-3840)")
    connect_p.add_argument("--format", choices=["png", "jpeg"], help="computer: jpeg makes screenshots much smaller")
    connect_p.add_argument("--env", action="store_true", help="print shell exports instead of JSON")
    connect_p.set_defaults(fn=connect)

    exec_p = sub.add_parser("exec", help="run one command on a machine (PowerShell on Windows) or a Desktop App")
    exec_p.add_argument("id")
    exec_p.add_argument("command")
    exec_p.add_argument("--session", help="commands in the same session share a working folder")
    exec_p.add_argument("--timeout", type=int, default=300, help="seconds it may run before it is stopped, up to 600")
    exec_p.set_defaults(fn=run_command)

    share_p = sub.add_parser("share", help="a link to a screen, to watch or to use")
    share_p.add_argument("id")
    share_p.add_argument("--control", action="store_true", help="let whoever opens it use the screen, not only watch")
    share_p.add_argument("--for", dest="duration", choices=["1h", "24h", "7d"], default="1h")
    share_p.set_defaults(fn=share)
    shares_p = sub.add_parser("shares", help="a screen's open links")
    shares_p.add_argument("id")
    shares_p.set_defaults(fn=lambda a: out(request("GET", f"/v1/workloads/{urllib.parse.quote(a.id)}/shares", token=token())))
    unshare_p = sub.add_parser("unshare", help="close a screen link now")
    unshare_p.add_argument("id")
    unshare_p.add_argument("share")
    unshare_p.set_defaults(fn=simple("DELETE", lambda a: f"/v1/workloads/{urllib.parse.quote(a.id)}/shares/{urllib.parse.quote(a.share)}",
                                     lambda a: {"closed": a.share}))
    release_p = sub.add_parser("release", help="let a machine you worked on go, for other agents")
    release_p.add_argument("id")
    release_p.set_defaults(fn=lambda a: out(request("DELETE", f"/v1/workloads/{urllib.parse.quote(a.id)}/reservation", token=token())))

    build_p = sub.add_parser("build", help="build an app from its repository now")
    build_p.add_argument("id")
    build_p.add_argument("--wait", action="store_true", help="follow the build until it is live or has failed")
    build_p.add_argument("--timeout", type=int, default=1800, help="with --wait: seconds")
    build_p.set_defaults(fn=build)
    builds_p = sub.add_parser("builds", help="an app's builds")
    builds_p.add_argument("id")
    builds_p.set_defaults(fn=lambda a: out(request("GET", f"/v1/workloads/{urllib.parse.quote(a.id)}/builds", token=token())))
    progress_p = sub.add_parser("progress", help="how the newest build is going")
    progress_p.add_argument("id")
    progress_p.set_defaults(fn=lambda a: out(request("GET", f"/v1/workloads/{urllib.parse.quote(a.id)}/build-progress", token=token())))

    deploy_p = sub.add_parser("deploy", help="run an earlier build again (rollback)")
    deploy_p.add_argument("id")
    deploy_p.add_argument("build")
    deploy_p.add_argument("--yes", action="store_true", required=True)
    deploy_p.set_defaults(fn=simple("POST", lambda a: f"/v1/workloads/{urllib.parse.quote(a.id)}/builds/{urllib.parse.quote(a.build)}/deploy",
                                    lambda a: {"deployed": a.build, "on": a.id}))

    restart_p = sub.add_parser("restart", help="restart a resource")
    restart_p.add_argument("id")
    restart_p.add_argument("--yes", action="store_true", required=True)
    restart_p.set_defaults(fn=simple("POST", lambda a: f"/v1/workloads/{urllib.parse.quote(a.id)}/restart",
                                     lambda a: {"restarted": a.id}))

    backups_p = sub.add_parser("backups", help="a machine's or a database's backups")
    backups_p.add_argument("id")
    backups_p.set_defaults(fn=lambda a: out(request("GET", backups_path(a.id), token=token())))
    backup_p = sub.add_parser("backup", help="back up a machine or a database now")
    backup_p.add_argument("id")
    backup_p.add_argument("--clean", action="store_true", help="a machine: back up with it stopped (stop it first)")
    backup_p.add_argument("--name", help="a machine: the backup's name")
    backup_p.set_defaults(fn=take_backup)
    restore_p = sub.add_parser("restore", help="a database: into a new database; a machine: in place, stopped")
    restore_p.add_argument("id")
    restore_p.add_argument("backup")
    restore_p.add_argument("--as", dest="as_id", help="a database: the id of the new database")
    restore_p.add_argument("--at", help="a database with continuous backups: the minute to restore to, e.g. 2026-09-25T14:05:00Z")
    restore_p.add_argument("--password-env", help="a database: the environment variable holding the new database's password")
    restore_p.add_argument("--host", help="a database: pin the new one to this host (ids from llc.py hosts)")
    restore_p.add_argument("--region", help="a database: run the new one on any host in this region; neither: automatic")
    restore_p.add_argument("--yes", action="store_true", required=True, help="the user asked for this restore")
    restore_p.set_defaults(fn=restore)

    stop_p = sub.add_parser("stop", help="stop an app or a machine; its disks are kept")
    stop_p.add_argument("id")
    stop_p.add_argument("--yes", action="store_true", required=True, help="the user agreed to stop it")
    stop_p.set_defaults(fn=set_stopped(True))
    start_p = sub.add_parser("start", help="start a stopped app or machine again")
    start_p.add_argument("id")
    start_p.set_defaults(fn=set_stopped(False))

    set_p = sub.add_parser("set", help="change some of a resource's settings; the file holds only what changes")
    set_p.add_argument("id")
    set_p.add_argument("--json", required=True)
    set_p.add_argument("--yes", action="store_true", required=True, help="the user agreed to this change")
    set_p.set_defaults(fn=set_settings)

    reach_p = sub.add_parser("reach", help="who may reach a resource from inside the workspace; change it with the user's agreement (a database: link)")
    reach_p.add_argument("id")
    reach_p.add_argument("--from", dest="source", help="exactly these resources, comma-separated, or '*' for the whole workspace")
    reach_p.add_argument("--none", action="store_true", help="nothing in the workspace reaches it")
    reach_p.add_argument("--add", help="these resources too, comma-separated; the rest kept")
    reach_p.add_argument("--remove", help="not these resources any more, comma-separated")
    reach_p.add_argument("--yes", action="store_true", help="any change: the user agreed to it")
    reach_p.set_defaults(fn=reach)

    link_p = sub.add_parser("link", help="let an app, a machine or a Desktop App reach databases (no variables); --remove takes links out")
    link_p.add_argument("id")
    link_p.add_argument("databases", nargs="+")
    link_p.add_argument("--remove", action="store_true", help="take these links out")
    link_p.add_argument("--yes", action="store_true", help="any change: the user agreed to it")
    link_p.set_defaults(fn=link)

    bapi_p = sub.add_parser("browser-api", help="one address over several browsers: create, show, add, remove")
    bapi_p.add_argument("action", choices=["create", "show", "add", "remove"])
    bapi_p.add_argument("name")
    bapi_p.add_argument("browser", nargs="?", help="add/remove: the browser")
    bapi_p.add_argument("--browsers", help="create: the workspace browsers it drives, comma-separated")
    bapi_p.add_argument("--all", action="store_true", help="create: every browser in the workspace")
    bapi_p.add_argument("--remote", action="append", help="create: a browser running elsewhere, ID=wss://address")
    bapi_p.add_argument("--host", help="create: pin it to this host (ids from llc.py hosts)")
    bapi_p.add_argument("--region", help="create: run it on any host in this region; neither: automatic")
    bapi_p.add_argument("--yes", action="store_true", help="create, add: the user agreed to what it lets in; remove: the user agreed")
    bapi_p.set_defaults(fn=browser_api)

    sub.add_parser("locales", help="the languages and time zones a browser can take").set_defaults(fn=locales)
    sub.add_parser("engines", help="the browser engines offered: Chrome, and Camoufox where it is").set_defaults(fn=engines)

    proxy_p = sub.add_parser("proxy", help="a browser's proxies: show, set, rotate, clear (go direct), remove")
    proxy_p.add_argument("action", choices=["show", "set", "rotate", "clear", "remove"])
    proxy_p.add_argument("id", help="the browser")
    proxy_p.add_argument("--json", help="set: a file with the proxy settings (logins in it are kept and never shown)")
    proxy_p.add_argument("--to", help="rotate: go to this proxy, by its name")
    proxy_p.add_argument("--yes", action="store_true", help="set, rotate, clear, remove: only once the user agreed to this change (SKILL.md rule 10)")
    proxy_p.set_defaults(fn=proxy)

    prof_p = sub.add_parser("profile", help="a browser's profile: show, snapshot, restore, rm, export, import, copy")
    prof_p.add_argument("action", choices=["show", "snapshot", "restore", "rm", "export", "import", "copy"])
    prof_p.add_argument("id", help="the browser (copy: the one that receives the profile)")
    prof_p.add_argument("--name", help="snapshot: its name")
    prof_p.add_argument("--snapshot", help="restore, rm: the snapshot; export, copy: send this snapshot instead of the profile now")
    prof_p.add_argument("--keep-current", action="store_true", help="restore: keep the profile it replaces as a snapshot")
    prof_p.add_argument("--out", help="export: the file to save it to (never overwritten)")
    prof_p.add_argument("--file", help="import: the profile file, exported from a LiveLLM browser")
    prof_p.add_argument("--password-env", help="export, import: the environment variable holding the file's password")
    prof_p.add_argument("--force", action="store_true", help="import: take a profile from a newer Chrome or Camoufox (only if the user agreed)")
    prof_p.add_argument("--from", dest="source", help="copy: the browser whose profile to copy")
    prof_p.add_argument("--yes", action="store_true", help="every change: only once the user agreed to it (export, import, copy: SKILL.md rule 10)")
    prof_p.set_defaults(fn=profile)

    cookies_p = sub.add_parser("cookies", help="add cookies to a running browser from a JSON file")
    cookies_p.add_argument("id")
    cookies_p.add_argument("--json", required=True, help="a JSON list of cookies: name, value, domain, path, ...")
    cookies_p.add_argument("--yes", action="store_true", help="only once the user gave these cookies and agreed to add them (SKILL.md rule 10)")
    cookies_p.set_defaults(fn=cookies)

    sub.add_parser("templates", help="the workspace's saved templates").set_defaults(
        fn=lambda a: out(request("GET", "/v1/templates", token=token())))
    tpl_p = sub.add_parser("template", help="a saved template: show, save one from a resource, use, rm")
    tpl_p.add_argument("action", choices=["show", "save", "use", "rm"])
    tpl_p.add_argument("ref", help="the template's id or name (save: the new template's name)")
    tpl_p.add_argument("new_id", nargs="?", help="use: the new resource's id (a Composable App: its name)")
    tpl_p.add_argument("--from", dest="source", help="save: the resource whose settings to keep (an app of a Composable App: the whole app)")
    tpl_p.add_argument("--description", help="save: a line about what it is for")
    tpl_p.add_argument("--secret", action="append", help="use: a secret it needs, PATH=VALUE (API_KEY=…, credentials.password=…)")
    tpl_p.add_argument("--secret-env", action="append", help="use: the same, the value read from an environment variable: PATH=VAR")
    tpl_p.add_argument("--json", help="use: a file with the secrets (secretEnv, imagePassword, gitToken, portPasswords, credentials, services) and placement")
    tpl_p.add_argument("--host", help="use: put everything it makes on this host (ids from llc.py hosts)")
    tpl_p.add_argument("--region", help="use: put everything it makes on any host in this region")
    tpl_p.add_argument("--automatic", action="store_true", help="use: everything it makes is automatic, whatever the template says")
    tpl_p.add_argument("--yes", action="store_true", help="use: the user asked for it; rm: the user agreed")
    tpl_p.set_defaults(fn=template)

    act_p = sub.add_parser("activity", help="what happened in the workspace, newest first")
    act_p.add_argument("--actor", choices=["you", "platform", "all"])
    act_p.add_argument("--object", help="one resource: its id, or TYPE:ID")
    act_p.add_argument("--limit", type=int)
    act_p.add_argument("--before", type=int, help="the next page: events older than this event id")
    act_p.set_defaults(fn=activity)

    mon_p = sub.add_parser("monitoring", help="up or down, uptime, use and alerts; with a machine's id, that machine")
    mon_p.add_argument("id", nargs="?")
    mon_p.add_argument("--range", choices=["15m", "1h", "6h", "24h", "7d"])
    mon_p.set_defaults(fn=monitoring)

    rm_p = sub.add_parser("rm", help="delete a resource")
    rm_p.add_argument("id")
    rm_p.add_argument("--with-databases", action="store_true", help="an app: also delete the databases made with it that no other app uses")
    rm_p.add_argument("--force", action="store_true", help="delete even though another app's settings name it")
    rm_p.add_argument("--yes", action="store_true", required=True, help="the user agreed to this deletion")
    rm_p.set_defaults(fn=rm)

    args = p.parse_args()
    try:
        args.fn(args)
    except Problem as e:
        json.dump({"error": e.message, "next": e.next}, sys.stderr, indent=2)
        sys.stderr.write("\n")
        sys.exit(e.code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
