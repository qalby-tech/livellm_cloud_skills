#!/usr/bin/env python3
"""Talk to LiveLLM Cloud: sign in, list and create resources, connect to them.

Only the Python standard library. Every command prints JSON on stdout. Errors
print JSON on stderr with a "next" field saying what to do, and set an exit
code: 2 the user must act, 3 not ready yet, 4 busy, 1 anything else.

    llc.py login [--wait] | logout | whoami | ssh-keys | logs <id>
    llc.py ls [--type TYPE]
    llc.py create TYPE --json FILE --yes
    llc.py create apps --json FILE --yes       (several apps and their databases, linked)
    llc.py wait ID [--timeout 600]
    llc.py connect ID [--tool cdp|view|api|computer]
                   [--screen-width PX] [--format png|jpeg] [--env]
    llc.py exec ID "COMMAND" [--session S] [--timeout N]
    llc.py share ID [--control] [--for 1h|24h|7d] | shares ID
    llc.py unshare ID SHARE_ID | release ID
    llc.py build ID [--wait] [--timeout 1800] | builds ID | deploy ID BUILD --yes | progress ID
    llc.py restart ID --yes
    llc.py backups ID | backup ID [--clean] [--name N]
    llc.py restore ID BACKUP --as NEW --password-env VAR [--at TIME] --yes
                                                           (a database: into a new one)
    llc.py restore ID BACKUP --yes                         (a machine: in place, stopped)
    llc.py stop ID --yes | start ID | set ID --json CHANGES --yes
    llc.py browser-api create NAME (--browsers a,b | --all) [--remote ID=WSS] --yes
    llc.py browser-api show NAME | add NAME BROWSER | remove NAME BROWSER --yes
    llc.py templates | template show T | template save NAME --from ID [--description D]
    llc.py template use T NEW [--secret PATH=VALUE] [--secret-env PATH=VAR] [--json FILE] --yes
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
import http.client
import json
import os
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
        reason = getattr(e, "reason", e)
        raise Problem(f"cannot reach LiveLLM at {API}: {reason}",
                      "check the network, or LIVELLM_API_URL for a self-hosted LiveLLM") from None


def http_problem(status, payload):
    p = status_problem(status, payload)
    p.oauth = payload.get("error")
    missing = payload.get("missing")
    p.missing = missing if isinstance(missing, list) else []
    return p


def status_problem(status, payload):
    message = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
    if status == 401:
        return Problem(message, "run: llc.py login, give the user the link it prints, and run login again once they press Allow", EXIT_USER, status)
    if status == 402:
        return Problem(message, "the plan is full: show the user their usage and stop; never delete to make room", EXIT_USER, status)
    if status == 403:
        return Problem(message, "tell the user which permission this needs; they can turn it on for this agent on the console's Agents page", EXIT_USER, status)
    if status == 404:
        return Problem(message, "run: llc.py ls, the id is probably wrong", EXIT_OTHER, status)
    if status == 409:
        return Problem(message, "if another agent holds the machine, wait until the time the message names or use another; "
                       "otherwise the resource is mid-change: wait a few seconds and retry once", EXIT_BUSY, status)
    if status == 422:
        return Problem(message, "fix the field the message names; do not retry unchanged", EXIT_OTHER, status)
    if status >= 500:
        return Problem(message, "platform trouble: retry twice with a pause, then tell the user", EXIT_OTHER, status)
    return Problem(message, "check the request", EXIT_OTHER, status)


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
            # an app's database links as its settings hold them, and the apps a database serves
            **({"databases": (w.get("pod") or {})["databases"]} if (w.get("pod") or {}).get("databases") else {}),
            **({"usedBy": live["usedBy"]} if live.get("usedBy") else {}),
            # a database's login name (its password is never shown)
            **({"username": w["storage"]["credentials"]["username"]}
               if ((w.get("storage") or {}).get("credentials") or {}).get("username") else {}),
        }


def ls(args):
    items = [r for r in resources(token()) if not args.type or r["type"] == args.type]
    out({"resources": items})


def create(args):
    body = json.loads(Path(args.json).read_text())
    if args.type == "apps":
        # several apps at once, all or nothing: a list of app settings, or
        # {"apps": [...], "databases": [...]} with the databases to make with them
        apps = body if isinstance(body, list) else body.get("apps") or []
        dbs = [] if isinstance(body, list) else body.get("databases") or []
        if not apps:
            raise Problem("the file should hold a list of apps, or {\"apps\": [...], \"databases\": [...]}", "fix the file", EXIT_OTHER)
        made = request("POST", "/v1/workloads", {"apps": apps, **({"databases": dbs} if dbs else {})}, token=token())
        ids = made.get("created", [])
        answer = {"created": ids, "next": f"llc.py wait {ids[-1] if ids else '<id>'} then llc.py connect the app with a public port"}
        if made.get("databases"):
            answer["databases"] = made["databases"]
        out(answer)
        return
    request("POST", f"/v1/workloads/{urllib.parse.quote(args.type)}", body, token=token())
    out({"created": body.get("id"), "type": args.type,
         "next": f"llc.py wait {body.get('id')} then llc.py connect {body.get('id')}"})


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
    patch_workload(args.id, changes, token())
    out({"changed": args.id})


# A Browser API is one address over several browsers; its type is "controller".
BROWSER_API = "controller"


def member_path(api, browser):
    return f"{workload_path(api)}/browsers/{urllib.parse.quote(browser)}"


def browser_api_body(name, browsers, every, remotes):
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
    return body


def browser_api(args):
    tok = token()
    if args.action == "create":
        if not args.yes:
            raise Problem("creating needs --yes", "only create a Browser API the user asked for, then pass --yes", EXIT_OTHER)
        request("POST", f"/v1/workloads/{BROWSER_API}", browser_api_body(args.name, args.browsers, args.all, args.remote), token=tok)
        out({"created": args.name, "type": BROWSER_API,
             "next": f"llc.py wait {args.name} then llc.py connect {args.name} --tool api"})
        return
    if args.action in ("add", "remove"):
        if not args.browser:
            raise Problem("which browser?", f"llc.py browser-api {args.action} {args.name} BROWSER", EXIT_OTHER)
        if args.action == "add":
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


def restore_body(new_id, at, password):
    """What a database's restore sends: the new database's id and password,
    and a moment when it restores to a minute rather than to the end of the
    backup."""
    body = {"id": new_id, "credentials": {"password": password}}
    if at:
        body["pointInTime"] = at
    return body


def restore(args):
    """A database restores into a NEW database and keeps running as it is; a
    machine goes back in place and has to be stopped first."""
    tok = token()
    spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
    w = next((x for x in spec.get("workloads", []) if x.get("id") == args.id), None)
    if w is None:
        raise Problem(f"no resource {args.id}", "run: llc.py ls, the id is probably wrong", EXIT_OTHER)
    kind = w.get("type", "")
    path = f"{backups_path(args.id)}/{urllib.parse.quote(args.backup)}/restore"
    if kind.startswith("vm-"):
        if args.as_id or args.at or args.password_env:
            raise Problem("a machine restores in place", "drop --as, --at and --password-env", EXIT_OTHER)
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
    res = request("POST", path, restore_body(args.as_id, args.at, password), token=tok) or {}
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
# template left out. Anything else would be dropped without a word.
TEMPLATE_BODY_KEYS = {"secretEnv", "imagePassword", "gitToken", "portPasswords", "credentials", "services"}


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


def put_secret(body, t, path, value):
    """One --secret into the create body. A path is what a refusal lists as
    missing (secretEnv.API_KEY, imagePassword, credentials.password,
    portPasswords.http.alice, services.web.secretEnv.API_KEY); a bare name is a
    secret env value. In a Composable App's template a secret belongs to a
    service: without services.<name> it goes to each service that has that
    secret env name, or to the only service there is."""
    parts = path.split(".")
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


def template_create_body(t, new_id, secrets, secret_envs, json_file):
    body = {}
    if json_file:
        body = json.loads(Path(json_file).read_text())
        if not isinstance(body, dict):
            raise Problem("the file should hold a JSON object with the secrets", "fix the file", EXIT_OTHER)
        for k in body:
            if k not in TEMPLATE_BODY_KEYS:
                raise Problem(f"{json_file}: {k!r} can't be given here — a create from a template takes only the secrets it needs "
                              "(secretEnv, imagePassword, gitToken, portPasswords, credentials, services)",
                              f"create it, then change settings with llc.py set {new_id} --json FILE --yes", EXIT_OTHER)
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
    body["name" if t.get("kind") == "stack" else "id"] = new_id
    return body


def template(args):
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
    body = template_create_body(t, args.new_id, args.secret, args.secret_env, args.json)
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

    create_p = sub.add_parser("create", help="create a resource from a JSON file (type apps: several at once, with their databases)")
    create_p.add_argument("type")
    create_p.add_argument("--json", required=True)
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
    exec_p.add_argument("--timeout", type=int, default=60, help="seconds, up to 600")
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

    bapi_p = sub.add_parser("browser-api", help="one address over several browsers: create, show, add, remove")
    bapi_p.add_argument("action", choices=["create", "show", "add", "remove"])
    bapi_p.add_argument("name")
    bapi_p.add_argument("browser", nargs="?", help="add/remove: the browser")
    bapi_p.add_argument("--browsers", help="create: the workspace browsers it drives, comma-separated")
    bapi_p.add_argument("--all", action="store_true", help="create: every browser in the workspace")
    bapi_p.add_argument("--remote", action="append", help="create: a browser running elsewhere, ID=wss://address")
    bapi_p.add_argument("--yes", action="store_true", help="create: the user asked for it; remove: the user agreed")
    bapi_p.set_defaults(fn=browser_api)

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
    tpl_p.add_argument("--json", help="use: a file with the secrets: secretEnv, imagePassword, gitToken, portPasswords, credentials, services")
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
