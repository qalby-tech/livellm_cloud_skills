#!/usr/bin/env python3
"""Talk to LiveLLM Cloud: sign in, list and create resources, connect to them.

Only the Python standard library. Every command prints JSON on stdout. Errors
print JSON on stderr with a "next" field saying what to do, and set an exit
code: 2 the user must act, 3 not ready yet, 4 busy, 1 anything else.

    llc.py login | logout | whoami
    llc.py ls [--type TYPE]
    llc.py create TYPE --json FILE --yes
    llc.py wait ID [--timeout 600]
    llc.py connect ID [--tool cdp|view|api] [--env]
    llc.py build ID | builds ID | deploy ID BUILD --yes | progress ID
    llc.py restart ID --yes
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


def request(method, path, body=None, token=None, form=False):
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
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:
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
        return Problem(message, "tell the user which access this needs; they can sign the agent in again with it", EXIT_USER, status)
    if status == 404:
        return Problem(message, "run: llc.py ls, the id is probably wrong", EXIT_OTHER, status)
    if status == 409:
        return Problem(message, "the resource is mid-change: wait a few seconds and retry once", EXIT_BUSY, status)
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


def connect(args):
    tok = token()
    body = {"tool": args.tool} if args.tool else {}
    info = request("POST", f"/v1/workloads/{urllib.parse.quote(args.id)}/connect", body, token=tok)
    if str(info.get("type", "")).startswith("vm-"):
        # A machine is reached over SSH; its address lives in the status.
        for w in request("GET", "/v1/status", token=tok).get("workloads", []):
            if w.get("id") == args.id and w.get("ssh"):
                host, _, port = w["ssh"].rpartition(":")
                info["ssh"] = {"address": w["ssh"], "host": host, "port": port}
                break
    if args.env:
        for key, value in [("LIVELLM_CDP_URL", (info.get("cdp") or {}).get("url")),
                           ("LIVELLM_CONNECT_TOKEN", info.get("token")),
                           ("LIVELLM_SSH_ADDRESS", (info.get("ssh") or {}).get("address"))]:
            if value:
                print(f"export {key}={json.dumps(value)}")
        return
    out(info)


def simple(method, path, ok):
    def run(args):
        request(method, path(args), token=token())
        out(ok(args))
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

    ls_p = sub.add_parser("ls", help="list resources")
    ls_p.add_argument("--type")
    ls_p.set_defaults(fn=ls)

    create_p = sub.add_parser("create", help="create a resource from a JSON file")
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
    connect_p.add_argument("--tool", choices=["cdp", "view", "api"])
    connect_p.add_argument("--env", action="store_true", help="print shell exports instead of JSON")
    connect_p.set_defaults(fn=connect)

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
