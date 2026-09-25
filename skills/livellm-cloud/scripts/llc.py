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
    llc.py build ID | builds ID | deploy ID BUILD --yes | progress ID
    llc.py restart ID --yes
    llc.py stop ID --yes | start ID
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


def set_stopped(stop):
    """Stop or start a resource as the console does: read it, change only
    "stopped", write the whole of it back. Write-only values are never read,
    and the platform keeps the ones it has."""
    def run(args):
        tok = token()
        spec = request("GET", "/v1/workspace", token=tok).get("spec", {})
        w = next((x for x in spec.get("workloads", []) if x.get("id") == args.id), None)
        if w is None:
            raise Problem(f"no resource {args.id}", "run: llc.py ls, the id is probably wrong", EXIT_OTHER)
        if bool(w.get("stopped")) == stop:
            out({"id": args.id, "already": "stopped" if stop else "running"})
            return
        w["stopped"] = stop
        request("PUT", f"/v1/workloads/{urllib.parse.quote(args.id)}", w, token=tok)
        if stop:
            out({"stopping": args.id, "next": f"its disks are kept; llc.py start {args.id} runs it again"})
        else:
            out({"starting": args.id, "next": f"llc.py wait {args.id}"})
    return run


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
    build_p.set_defaults(fn=simple("POST", lambda a: f"/v1/workloads/{urllib.parse.quote(a.id)}/build",
                                   lambda a: {"building": a.id}))
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

    stop_p = sub.add_parser("stop", help="stop an app or a machine; its disks are kept")
    stop_p.add_argument("id")
    stop_p.add_argument("--yes", action="store_true", required=True, help="the user agreed to stop it")
    stop_p.set_defaults(fn=set_stopped(True))
    start_p = sub.add_parser("start", help="start a stopped app or machine again")
    start_p.add_argument("id")
    start_p.set_defaults(fn=set_stopped(False))

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
