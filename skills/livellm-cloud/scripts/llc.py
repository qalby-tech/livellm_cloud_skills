#!/usr/bin/env python3
"""Talk to LiveLLM Cloud: sign in, list and create resources, connect to them.

Only the Python standard library. Every command prints JSON on stdout. Errors
print JSON on stderr with a "next" field saying what to do, and set an exit
code: 2 the user must act, 3 not ready yet, 4 busy, 1 anything else.

    llc.py login | logout | whoami | ssh-keys | logs <id>
    llc.py ls [--type TYPE]
    llc.py create TYPE --json FILE --yes
    llc.py wait ID [--timeout 600]
    llc.py connect ID [--tool cdp|view|api|computer] [--desktop N]
                   [--screen-width PX] [--format png|jpeg] [--env]
    llc.py exec ID "COMMAND" [--session S] [--timeout N] [--desktop N]
    llc.py share ID [--control] [--for 1h|24h|7d] [--desktop N] | shares ID
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
    llc.py templates | template show T | template save NAME --from ID
    llc.py template use T NEW [--json FILE] --yes | template rm T --yes
    llc.py activity [--actor you|platform|all] [--object ID] [--limit N] [--before EVENT]
    llc.py monitoring [ID] [--range 15m|1h|6h|24h|7d]
    llc.py rm ID --yes

Sign-in is stored in ~/.config/livellm/credentials.json, readable only by you.
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

EXIT_USER, EXIT_NOT_READY, EXIT_BUSY, EXIT_OTHER = 2, 3, 4, 1


class Problem(Exception):
    def __init__(self, message, nxt, code=EXIT_OTHER, status=None):
        super().__init__(message)
        self.message, self.next, self.code, self.status = message, nxt, code, status


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
        text = e.read().decode(errors="replace")
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
    message = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
    if status == 401:
        return Problem(message, "run: llc.py login, and give the user the link it prints", EXIT_USER, status)
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
        raise Problem("not signed in", "run: llc.py login, and give the user the link it prints", EXIT_USER)
    if creds.get("expires_at", 0) - 60 > time.time():
        return creds["access_token"]
    if not creds.get("refresh_token"):
        raise Problem("sign-in expired", "run: llc.py login again", EXIT_USER)
    try:
        fresh = request("POST", "/v1/oauth/token",
                        {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]}, form=True)
    except Problem:
        raise Problem("sign-in expired or ended", "run: llc.py login, and give the user the link it prints", EXIT_USER) from None
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


def login(args):
    if API_KEY:
        out({"signedIn": True, "with": "LIVELLM_API_KEY"})
        return
    start = request("POST", "/v1/oauth/device/code",
                    {"client_name": client_name(), "scope": args.access}, form=True)
    sys.stderr.write(
        f"\nAsk the user to open {start['verification_uri_complete']} and click Allow"
        f" (code {start['user_code']}).\n\n")
    interval = int(start.get("interval", 5))
    deadline = time.time() + int(start.get("expires_in", 600))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            entry = save_tokens(request("POST", "/v1/oauth/token",
                                        {"grant_type": DEVICE_GRANT, "device_code": start["device_code"]}, form=True))
            out({"signedIn": True, "workspace": entry["workspace"], "access": entry["scope"],
                 "link": start["verification_uri_complete"]})
            return
        except Problem as p:
            text = str(p)
            if "slow_down" in text or p.status == 429:
                interval += 5
            elif "authorization_pending" in text or "waiting for the user" in text:
                continue
            else:
                raise Problem(text, "ask the user to open the link again, then run: llc.py login", EXIT_USER) from None
    raise Problem("the sign-in link expired", "run: llc.py login again and give the user the new link", EXIT_USER)


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
        }


def ls(args):
    items = [r for r in resources(token()) if not args.type or r["type"] == args.type]
    out({"resources": items})


def create(args):
    body = json.loads(Path(args.json).read_text())
    if args.type == "apps":
        # several apps at once, all or nothing: a list of app settings
        apps = body if isinstance(body, list) else body.get("apps") or []
        if not apps:
            raise Problem("the file should hold a list of apps, or {\"apps\": [...]}", "fix the file", EXIT_OTHER)
        made = request("POST", "/v1/workloads", {"apps": apps}, token=token())
        ids = made.get("created", [])
        out({"created": ids, "next": f"llc.py wait {ids[-1] if ids else '<id>'} then llc.py connect the app with a public port"})
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
    if args.desktop is not None:
        body["desktop"] = args.desktop
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


def run_command(args):
    """One bash command on a Linux machine or a Desktop App's desktop."""
    body = {"command": args.command, "timeout": args.timeout}
    if args.session:
        body["session"] = args.session
    if args.desktop is not None:
        body["desktop"] = args.desktop
    # The answer comes when the command ends, so wait a little longer than it may run.
    out(request("POST", f"/v1/workloads/{urllib.parse.quote(args.id)}/exec", body,
                token=token(), timeout=args.timeout + 30))


def share(args):
    """A link that opens the screen in any browser. Its address is shown only now."""
    body = {"mode": "control" if args.control else "view", "for": args.duration}
    if args.desktop is not None:
        body["desktop"] = args.desktop
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

# Where each resource type keeps its settings on the resource.
KIND_BLOCK = {"vm-ubuntu": "vm", "vm-ubuntu-desktop": "vm", "vm-windows": "vm", "pod": "pod",
              "browser": "browser", "desktop": "desktop", "controller": "controller", "storage": "storage"}


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


def template_config(w):
    """A resource's settings as a template keeps them: never its login, env
    values or pull credential (the console leaves out the same)."""
    kind = w.get("type", "")
    block = KIND_BLOCK.get(kind)
    if not block or kind == "controller":
        raise Problem(f"a {kind} can't be saved as a template", "save a machine, an app, a browser, a desktop or a database", EXIT_OTHER)
    spec = dict(w.get(block) or {})
    if block in ("vm", "storage"):
        spec.pop("credentials", None)
    if block == "pod":
        for k in ("env", "secretEnv", "imageAuth"):
            spec.pop(k, None)
        if isinstance(spec.get("source"), dict):
            spec["source"] = {"git": spec["source"].get("git")}
    return kind, {block: spec}


def template(args):
    tok = token()
    if args.action == "save":
        if not args.source:
            raise Problem("save which resource?", f"llc.py template save {args.ref} --from ID", EXIT_OTHER)
        spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
        w = next((x for x in spec.get("workloads", []) if x.get("id") == args.source), None)
        if w is None:
            raise Problem(f"no resource {args.source}", "run: llc.py ls", EXIT_OTHER)
        kind, config = template_config(w)
        out(request("POST", "/v1/templates", {"name": args.ref, "kind": kind, "config": config}, token=tok))
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
    # use: a new resource from the template, with the file's settings on top
    if not args.new_id or not args.yes:
        raise Problem("a new resource needs its id and --yes",
                      f"llc.py template use {args.ref} NEW-ID [--json FILE] --yes, once the user asked for it", EXIT_OTHER)
    body = dict((t.get("config") or {}).get(KIND_BLOCK.get(t.get("kind"), ""), {}) or {})
    if args.json:
        body.update(json.loads(Path(args.json).read_text()))
    body["id"] = args.new_id
    request("POST", f"/v1/workloads/{urllib.parse.quote(t['kind'])}", body, token=tok)
    out({"created": args.new_id, "type": t["kind"], "template": t.get("name"),
         "next": f"llc.py wait {args.new_id}"})


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


def rm(args):
    request("DELETE", f"/v1/workloads/{urllib.parse.quote(args.id)}", token=token())
    out({"deleted": args.id})


def main():
    p = argparse.ArgumentParser(prog="llc.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    login_p = sub.add_parser("login", help="sign this agent in; prints a link for the user")
    login_p.add_argument("--access", choices=["use", "create", "full"], default="create")
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

    create_p = sub.add_parser("create", help="create a resource from a JSON file (type apps: several at once)")
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
    connect_p.add_argument("--desktop", type=int, help="for a Desktop App: which desktop, from 0")
    connect_p.add_argument("--screen-width", type=int, help="computer: shrink screenshots to this many pixels wide (320-3840)")
    connect_p.add_argument("--format", choices=["png", "jpeg"], help="computer: jpeg makes screenshots much smaller")
    connect_p.add_argument("--env", action="store_true", help="print shell exports instead of JSON")
    connect_p.set_defaults(fn=connect)

    exec_p = sub.add_parser("exec", help="run one command on a Linux machine or a Desktop App")
    exec_p.add_argument("id")
    exec_p.add_argument("command")
    exec_p.add_argument("--session", help="commands in the same session share a working folder")
    exec_p.add_argument("--timeout", type=int, default=60, help="seconds, up to 600")
    exec_p.add_argument("--desktop", type=int, help="for a Desktop App: which desktop, from 0")
    exec_p.set_defaults(fn=run_command)

    share_p = sub.add_parser("share", help="a link to a screen, to watch or to use")
    share_p.add_argument("id")
    share_p.add_argument("--control", action="store_true", help="let whoever opens it use the screen, not only watch")
    share_p.add_argument("--for", dest="duration", choices=["1h", "24h", "7d"], default="1h")
    share_p.add_argument("--desktop", type=int, help="for a Desktop App: which desktop, from 0")
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
    tpl_p.add_argument("new_id", nargs="?", help="use: the new resource's id")
    tpl_p.add_argument("--from", dest="source", help="save: the resource whose settings to keep")
    tpl_p.add_argument("--json", help="use: settings to add, such as a machine's credentials")
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
