"""A browser's language, proxies and profile with scripts/llc.py, against a
stand-in for LiveLLM's API that records every call.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder clean
SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud" / "scripts" / "llc.py"
spec = importlib.util.spec_from_file_location("llc", SCRIPT)
llc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llc)

ARCHIVE = b"\x28\xb5\x2f\xfd" + bytes(range(256)) * 9000  # binary, more than one read
STATUS = {"mode": "proxy", "upstream": {"name": "a", "server": "http://p.example:8080"}, "exitIp": "203.0.113.7",
          "country": "DE", "generation": 2}


class FakeAPI:
    """Answers the browser routes; records (method, path, headers, body) of
    every call. `refuse` maps "METHOD path" to (status, payload). `early`
    does the same but answers before the body is read (after `early_read`
    bytes of it), the way a gate or a check of the file's first entry does,
    and then closes the connection with the rest unread."""

    def __init__(self):
        self.calls = []
        self.refuse = {}
        self.early = {}
        self.early_read = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def body(self):
                n = int(self.headers.get("content-length", 0))
                return self.rfile.read(n) if n else b""

            def reply(self, status, payload=None, raw=None, ctype="application/json"):
                data = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self):
                key = f"{self.command} {self.path}"
                if key in fake.early:
                    if fake.early_read:
                        self.rfile.read(fake.early_read)
                    fake.calls.append((self.command, self.path, dict(self.headers), None))
                    self.close_connection = True
                    status, payload = fake.early[key]
                    return self.reply(status, payload)
                raw = self.body()
                ctype = self.headers.get("content-type", "")
                body = json.loads(raw) if raw and ctype.startswith("application/json") else raw
                fake.calls.append((self.command, self.path, dict(self.headers), body))
                if key in fake.refuse:
                    status, payload = fake.refuse[key]
                    return self.reply(status, payload)
                if self.path.endswith("/profile/export"):
                    return self.reply(200, raw=ARCHIVE, ctype="application/octet-stream")
                if self.path.endswith("/proxy") and self.command == "GET":
                    return self.reply(200, {"proxy": {"upstreams": [{"name": "a", "hasAuth": True}]}, "status": STATUS})
                if "/proxy" in self.path:
                    return self.reply(200, STATUS)
                if self.path == "/v1/browsers/locales":
                    return self.reply(200, {"locales": [{"locale": "ru-RU"}], "timezones": ["Europe/Moscow"]})
                if self.path.endswith("/profile/import") or "/profile/import?" in self.path:
                    return self.reply(200, {"imported": True, "profilesReady": True})
                return self.reply(200 if self.command == "GET" else 202, {})

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def args(**kw):
    base = {"json": None, "to": None, "yes": False, "name": None, "snapshot": None, "keep_current": False,
            "out": None, "file": None, "password_env": None, "force": False, "source": None}
    base.update(kw)
    return base


class BrowserTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAPI()
        self.addCleanup(self.fake.server.server_close)
        self.addCleanup(self.fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = self.fake.url, "llc_test"
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_llc(self, fn, **kw):
        buf = io.StringIO()
        problem = None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                fn(types.SimpleNamespace(**args(**kw)))
            except llc.Problem as p:
                problem = p
        text = buf.getvalue()
        return (json.loads(text) if text.strip() else None), problem, text

    def file(self, name, value):
        p = self.tmp / name
        p.write_text(json.dumps(value))
        return str(p)

    # --- locales ---

    def test_locales_is_one_read(self):
        res, problem, _ = self.run_llc(llc.locales)
        self.assertIsNone(problem)
        self.assertEqual(res["timezones"], ["Europe/Moscow"])
        self.assertEqual([c[:2] for c in self.fake.calls], [("GET", "/v1/browsers/locales")])

    # --- proxies ---

    def test_proxy_show_and_rotate(self):
        res, problem, _ = self.run_llc(llc.proxy, action="show", id="shop")
        self.assertIsNone(problem)
        self.assertEqual(res["status"]["exitIp"], "203.0.113.7")
        res, problem, _ = self.run_llc(llc.proxy, action="rotate", id="shop", to="b", yes=True)
        self.assertIsNone(problem)
        self.assertEqual(self.fake.calls[-1][:2], ("POST", "/v1/workloads/shop/proxy/rotate"))
        self.assertEqual(self.fake.calls[-1][3], {"to": "b"})
        self.run_llc(llc.proxy, action="rotate", id="shop", yes=True)
        self.assertEqual(self.fake.calls[-1][3], {})

    def test_proxy_set_sends_the_file_and_never_prints_the_login(self):
        block = {"upstreams": [{"name": "a", "server": "socks5://p.example:1080", "username": "u-881", "password": "pw-secret-881",
                                "changeIpUrl": "https://p.example/rot?key=key-secret-881"}],
                 "rotation": {"mode": "session"}}
        f = self.file("proxy.json", {"proxy": block})
        res, problem, text = self.run_llc(llc.proxy, action="set", id="shop", json=f, yes=True)
        self.assertIsNone(problem)
        method, path, _, body = self.fake.calls[-1]
        self.assertEqual((method, path), ("PUT", "/v1/workloads/shop/proxy"))
        self.assertEqual(body, block)  # {"proxy": ...} unwrapped
        for secret in ("pw-secret-881", "key-secret-881", "u-881"):
            self.assertNotIn(secret, text)
        self.assertIn("delete", res["next"])

    def test_proxy_set_keeping_the_stored_login_says_nothing_about_deleting(self):
        f = self.file("p.json", {"upstreams": [{"name": "a", "server": "http://p.example:8080", "hasAuth": True}]})
        res, problem, _ = self.run_llc(llc.proxy, action="set", id="shop", json=f, yes=True)
        self.assertIsNone(problem)
        self.assertNotIn("next", res)

    def test_proxy_set_refuses_a_login_in_the_address_before_sending(self):
        f = self.file("p.json", {"upstreams": [{"name": "a", "server": "http://user:pw@p.example:8080"}]})
        _, problem, _ = self.run_llc(llc.proxy, action="set", id="shop", json=f, yes=True)
        self.assertIsNotNone(problem)
        self.assertIn("username", problem.next)
        f = self.file("q.json", {"upstreams": [{"name": "a", "server": "http://p.example:8080", "username": "u"}]})
        _, problem, _ = self.run_llc(llc.proxy, action="set", id="shop", json=f, yes=True)
        self.assertIsNotNone(problem)
        # no scheme: still a login in the address
        f = self.file("r.json", {"upstreams": [{"name": "a", "server": "user:pw-881@p.example:1080"}]})
        _, problem, _ = self.run_llc(llc.proxy, action="set", id="shop", json=f, yes=True)
        self.assertIsNotNone(problem)
        self.assertIn("login in the address", problem.message)
        self.assertNotIn("pw-881", problem.message)
        self.assertEqual(self.fake.calls, [])

    def test_create_and_set_check_a_proxy_block_like_proxy_set(self):
        bad = {"id": "shop", "proxy": {"upstreams": [{"name": "a", "server": "user:pw@p.example:1080"}]}}
        _, problem, _ = self.run_llc(llc.create, type="browser", json=self.file("b.json", bad), yes=True)
        self.assertIsNotNone(problem)
        bad = {"browser": {"proxy": {"upstreams": [{"name": "a", "server": "socks5://u:pw@p.example:1080"}]}}}
        _, problem, _ = self.run_llc(llc.set_settings, id="shop", json=self.file("s.json", bad), yes=True)
        self.assertIsNotNone(problem)
        self.assertEqual(self.fake.calls, [])
        good = {"id": "shop", "proxy": {"upstreams": [{"name": "a", "server": "http://p.example:8080",
                                                       "username": "u", "password": "pw-create-7"}]}}
        f = self.file("c.json", good)
        res, problem, text = self.run_llc(llc.create, type="browser", json=f, yes=True)
        self.assertIsNone(problem)
        self.assertIn(f"delete {f}", res["delete"])
        self.assertNotIn("pw-create-7", text)
        f = self.file("t.json", {"browser": {"proxy": good["proxy"]}})
        res, problem, _ = self.run_llc(llc.set_settings, id="shop", json=f, yes=True)
        self.assertIsNone(problem)
        self.assertIn(f"delete {f}", res["delete"])
        res, problem, _ = self.run_llc(llc.set_settings, id="shop", json=self.file("u.json", {"browser": {"locale": "ru-RU"}}), yes=True)
        self.assertIsNone(problem)
        self.assertNotIn("delete", res)

    def test_proxy_changes_need_yes(self):
        for action in ("set", "rotate", "clear", "remove"):
            _, problem, _ = self.run_llc(llc.proxy, action=action, id="shop", json=self.file("p.json", {}))
            self.assertIsNotNone(problem, action)
        self.assertEqual(self.fake.calls, [])

    def test_proxy_clear_goes_direct_and_remove_drops_the_block(self):
        self.run_llc(llc.proxy, action="clear", id="shop", yes=True)
        self.assertEqual(self.fake.calls[-1][:2], ("DELETE", "/v1/workloads/shop/proxy"))
        self.run_llc(llc.proxy, action="remove", id="shop", yes=True)
        self.assertEqual(self.fake.calls[-1][:2], ("DELETE", "/v1/workloads/shop/proxy?remove=true"))

    def test_refusals_say_what_to_do(self):
        cases = [
            ("POST /v1/workloads/shop/proxy/rotate", 403,
             {"error": "This agent can't change browser proxies. A person can allow it on the Agents page."}, "Agents page", llc.EXIT_USER),
            ("POST /v1/workloads/shop/proxy/rotate", 409,
             {"error": "Restart this browser once to turn on profiles.", "code": "needs_restart"}, "llc.py restart", llc.EXIT_USER),
            ("POST /v1/workloads/shop/proxy/rotate", 429,
             {"error": "too soon", "code": "change_ip_too_soon"}, "wait", llc.EXIT_BUSY),
            ("POST /v1/workloads/shop/proxy/rotate", 409, {"error": "nothing to rotate to"}, "second proxy", llc.EXIT_OTHER),
            # the same refusals with only an error message, no code
            ("POST /v1/workloads/shop/proxy/rotate", 429, {"error": "change_ip_too_soon"}, "wait", llc.EXIT_BUSY),
            ("POST /v1/workloads/shop/proxy/rotate", 409, {"error": "needs_restart"}, "llc.py restart", llc.EXIT_USER),
        ]
        for key, status, payload, words, code in cases:
            self.fake.refuse = {key: (status, payload)}
            _, problem, _ = self.run_llc(llc.proxy, action="rotate", id="shop", yes=True)
            self.assertIsNotNone(problem, payload)
            self.assertIn(words, problem.next, payload)
            self.assertEqual(problem.code, code, payload)

    def test_the_apis_own_refusals(self):
        # Word for word as tenant-api 0.48.1 answers them.
        cases = [
            # the helper starting up: wait, no restart
            (409, {"error": "The browser's profiles are still starting — try again in a moment.", "code": "needs_restart"},
             "run the same command again", llc.EXIT_NOT_READY),
            (409, {"error": "The browser's proxies are still starting — try again in a moment.", "code": "needs_restart"},
             "run the same command again", llc.EXIT_NOT_READY),
            (409, {"error": "Restart this browser once to turn on profiles.", "code": "needs_restart"}, "llc.py restart", llc.EXIT_USER),
            # too many snapshots, in both of the API's wordings
            (409, {"error": "This browser keeps at most 10 snapshots. Delete one first.", "code": "too_many_snapshots"},
             "which one to delete", llc.EXIT_USER),
            (409, {"error": "This browser has the most snapshots it can keep. Delete one first.", "code": "too_many_snapshots"},
             "which one to delete", llc.EXIT_USER),
            (409, {"error": "Nothing to rotate to: add another upstream, or a change-IP address.", "code": "nothing_to_rotate"},
             "second proxy", llc.EXIT_OTHER),
            (429, {"error": "This proxy's IP was changed moments ago. Wait for its shortest interval and try again.",
                   "code": "change_ip_too_soon"}, "wait", llc.EXIT_BUSY),
            (422, {"error": "The password doesn't open this file.", "code": "wrong_password"}, "--password-env", llc.EXIT_USER),
            (403, {"error": "This API key can't export or import browser profiles. A person can give it the profiles permission on the Keys page."},
             "profiles permission", llc.EXIT_USER),
            (403, {"error": "Profiles hold sign-ins. Only the workspace's people can export them."}, "no permission changes that", llc.EXIT_USER),
            # a database's refusal keeps its own answer
            (409, {"error": "pg is still starting, so its location can't change"}, "ask the user before deleting it", llc.EXIT_USER),
        ]
        for status, payload, words, code in cases:
            problem = llc.status_problem(status, payload)
            self.assertIn(words, problem.next, payload)
            self.assertEqual(problem.code, code, payload)

    # --- profiles ---

    def test_profile_changes_need_yes(self):
        for action in ("snapshot", "restore", "rm", "export", "import", "copy"):
            _, problem, _ = self.run_llc(llc.profile, action=action, id="shop", snapshot="s1")
            self.assertIsNotNone(problem, action)
            self.assertIn("ask the user", problem.next)
        self.assertEqual(self.fake.calls, [])

    def test_snapshot_restore_rm_copy(self):
        self.run_llc(llc.profile, action="snapshot", id="shop", name="before-login", yes=True)
        self.assertEqual(self.fake.calls[-1][1:4:2], ("/v1/workloads/shop/profile/snapshots", {"name": "before-login"}))
        self.run_llc(llc.profile, action="restore", id="shop", snapshot="s1", keep_current=True, yes=True)
        self.assertEqual(self.fake.calls[-1][1:4:2], ("/v1/workloads/shop/profile/snapshots/s1/restore", {"keepCurrent": True}))
        self.run_llc(llc.profile, action="rm", id="shop", snapshot="s1", yes=True)
        self.assertEqual(self.fake.calls[-1][:2], ("DELETE", "/v1/workloads/shop/profile/snapshots/s1"))
        self.run_llc(llc.profile, action="copy", id="shop-2", source="shop", yes=True)
        self.assertEqual(self.fake.calls[-1][1:4:2], ("/v1/workloads/shop-2/profile/copy", {"from": "shop"}))
        _, problem, _ = self.run_llc(llc.profile, action="restore", id="shop", yes=True)
        self.assertIn("profile show", problem.next)

    def test_export_streams_to_a_private_file_and_takes_the_password_from_the_environment(self):
        os.environ["PROFILE_PW"] = "pw-export-552"
        self.addCleanup(os.environ.pop, "PROFILE_PW", None)
        target = self.tmp / "shop.llcprofile.age"
        res, problem, text = self.run_llc(llc.profile, action="export", id="shop", out=str(target),
                                          password_env="PROFILE_PW", yes=True)
        self.assertIsNone(problem)
        self.assertEqual(target.read_bytes(), ARCHIVE)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(res["bytes"], len(ARCHIVE))
        self.assertTrue(res["encrypted"])
        self.assertNotIn("pw-export-552", text)
        self.assertEqual(self.fake.calls[-1][3], {"password": "pw-export-552"})
        self.assertFalse((self.tmp / "shop.llcprofile.age.part").exists())
        # never overwrites
        _, problem, _ = self.run_llc(llc.profile, action="export", id="shop", out=str(target), yes=True)
        self.assertIn("already exists", problem.message)

    def test_export_without_a_password_says_the_file_is_unencrypted(self):
        res, problem, _ = self.run_llc(llc.profile, action="export", id="shop", out=str(self.tmp / "p.llcprofile"),
                                       snapshot="s1", yes=True)
        self.assertIsNone(problem)
        self.assertFalse(res["encrypted"])
        self.assertIn("unencrypted", res["note"])
        self.assertEqual(self.fake.calls[-1][3], {"snapshot": "s1"})

    def test_a_refused_export_leaves_no_file(self):
        self.fake.refuse = {"POST /v1/workloads/shop/profile/export": (
            403, {"error": "This agent can't export or import browser profiles. A person can allow it on the Agents page."})}
        target = self.tmp / "x.llcprofile"
        _, problem, _ = self.run_llc(llc.profile, action="export", id="shop", out=str(target), yes=True)
        self.assertIn("profiles permission", problem.next)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_an_empty_password_variable_is_refused(self):
        os.environ["EMPTY_PW"] = ""
        self.addCleanup(os.environ.pop, "EMPTY_PW", None)
        _, problem, _ = self.run_llc(llc.profile, action="export", id="shop", out=str(self.tmp / "e"),
                                     password_env="EMPTY_PW", yes=True)
        self.assertIn("EMPTY_PW", problem.message)
        self.assertEqual(self.fake.calls, [])

    def test_import_sends_the_file_raw_with_the_password_in_a_header(self):
        src = self.tmp / "in.llcprofile.age"
        src.write_bytes(ARCHIVE)
        os.environ["PROFILE_PW"] = "pw-import-553"
        self.addCleanup(os.environ.pop, "PROFILE_PW", None)
        res, problem, text = self.run_llc(llc.profile, action="import", id="shop", file=str(src),
                                          password_env="PROFILE_PW", force=True, yes=True)
        self.assertIsNone(problem)
        method, path, headers, body = self.fake.calls[-1]
        self.assertEqual((method, path), ("POST", "/v1/workloads/shop/profile/import?force=1"))
        self.assertEqual(body, ARCHIVE)
        lower = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(lower["x-profile-password"], "pw-import-553")
        self.assertEqual(lower["content-type"], "application/octet-stream")
        self.assertNotIn("pw-import-553", text)
        self.assertTrue(res["imported"])

    def test_a_newer_profile_asks_before_force(self):
        src = self.tmp / "in.llcprofile"
        src.write_bytes(b"x")
        self.fake.refuse = {"POST /v1/workloads/shop/profile/import": (
            409, {"error": "This profile is from Chrome 155; this browser runs 154. Import anyway?", "code": "profile_newer"})}
        _, problem, _ = self.run_llc(llc.profile, action="import", id="shop", file=str(src), yes=True)
        self.assertIn("--force", problem.next)
        self.assertEqual(problem.code, llc.EXIT_USER)

    def test_refusal_codes_are_known_from_the_message_too(self):
        for payload in ({"error": "snapshot_key_changed"}, {"error": "x", "code": "snapshot_key_changed"}):
            p = llc.status_problem(409, payload)
            self.assertIn("pick another one", p.next, payload)
            self.assertEqual(p.code, llc.EXIT_OTHER)
        p = llc.status_problem(409, {"error": "profile_newer"})
        self.assertIn("--force", p.next)

    def test_owner_only_refusal_is_not_a_missing_permission(self):
        p = llc.status_problem(403, {"error": "Profiles hold sign-ins. Only the workspace's people can export them."})
        self.assertIn("no permission changes that", p.next)
        self.assertNotIn("Agents page", p.next)
        p = llc.status_problem(403, {"error": "This API key can't export or import browser profiles. "
                                              "A person can give it the profiles permission on the Keys page."})
        self.assertIn("profiles permission", p.next)

    def test_import_and_copy_name_manage_when_that_is_what_was_refused(self):
        src = self.tmp / "in.llcprofile"
        src.write_bytes(b"x")
        self.fake.refuse = {"POST /v1/workloads/shop/profile/import": (403, {"error": "This agent can't change shop."}),
                            "POST /v1/workloads/shop/profile/copy": (403, {"error": "This agent can't change shop."})}
        _, problem, _ = self.run_llc(llc.profile, action="import", id="shop", file=str(src), yes=True)
        self.assertIn("Manage", problem.next)
        _, problem, _ = self.run_llc(llc.profile, action="copy", id="shop", source="b1", yes=True)
        self.assertIn("Manage", problem.next)

    def test_an_import_refused_before_the_file_is_read_says_why(self):
        src = self.tmp / "big.llcprofile"
        src.write_bytes(os.urandom(8 << 20))  # more than the socket buffers take: the send gets cut
        refusal = {"error": "This agent can't export or import browser profiles. A person can allow it on the Agents page."}
        self.fake.early = {"POST /v1/workloads/shop/profile/import": (403, refusal)}
        _, problem, _ = self.run_llc(llc.profile, action="import", id="shop", file=str(src), yes=True)
        self.assertIsNotNone(problem)
        self.assertEqual(problem.status, 403)
        self.assertIn("profiles permission", problem.next)
        self.assertNotIn("network", problem.next)
        # after reading only the start of the file (its first entry)
        self.fake.early = {"POST /v1/workloads/shop/profile/import": (
            409, {"error": "This profile is from Chrome 155; this browser runs 154. Import anyway?"})}
        self.fake.early_read = 64 << 10
        _, problem, _ = self.run_llc(llc.profile, action="import", id="shop", file=str(src), yes=True)
        self.assertEqual(problem.status, 409)
        self.assertIn("--force", problem.next)
        self.fake.early, self.fake.early_read = {"POST /v1/workloads/shop/profile/import": (
            413, {"error": "That file is larger than this browser takes."})}, 0
        _, problem, _ = self.run_llc(llc.profile, action="import", id="shop", file=str(src), yes=True)
        self.assertEqual(problem.status, 413)

    def test_an_import_through_a_proxy_goes_where_urllib_would(self):
        saved = {k: os.environ.get(k) for k in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k in saved:
            os.environ.pop(k, None)
        os.environ["http_proxy"] = "http://u%40x:p%3A1@proxy.example:3128"
        conn, target, extra = llc.connection("http://api.example/v1/x?y=1", 5)
        self.assertEqual((conn.host, conn.port), ("proxy.example", 3128))
        self.assertEqual(target, "http://api.example/v1/x?y=1")
        self.assertEqual(extra["Proxy-Authorization"], "Basic " + __import__("base64").b64encode(b"u@x:p:1").decode())
        os.environ["https_proxy"] = "proxy.example:3128"
        conn, target, extra = llc.connection("https://api.example/v1/x", 5)
        self.assertEqual((conn.host, conn.port, conn._tunnel_host), ("proxy.example", 3128, "api.example"))
        self.assertEqual((target, extra), ("/v1/x", {}))
        os.environ["no_proxy"] = "api.example"
        conn, target, _ = llc.connection("https://api.example/v1/x", 5)
        self.assertEqual((conn.host, target), ("api.example", "/v1/x"))

    def test_export_leaves_a_file_it_did_not_make_and_asks_nothing_of_the_browser(self):
        target = self.tmp / "o.llcprofile"
        part = self.tmp / "o.llcprofile.part"
        part.write_bytes(b"another export")
        _, problem, _ = self.run_llc(llc.profile, action="export", id="shop", out=str(target), yes=True)
        self.assertIsNotNone(problem)
        self.assertIn("already exists", problem.message)
        self.assertEqual(part.read_bytes(), b"another export")
        self.assertFalse(target.exists())
        self.assertEqual(self.fake.calls, [])

    def test_a_file_that_appears_meanwhile_is_not_overwritten(self):
        target, part = self.tmp / "t.llcprofile", self.tmp / "t.llcprofile.part"
        part.write_bytes(b"new")
        target.write_bytes(b"theirs")
        with self.assertRaises(llc.Problem) as cm:
            llc.keep_as(part, target)
        self.assertIn(str(part), cm.exception.message)
        self.assertEqual(target.read_bytes(), b"theirs")
        self.assertEqual(part.read_bytes(), b"new")

    def test_storage_full(self):
        self.fake.refuse = {"POST /v1/workloads/shop/profile/snapshots": (
            507, {"error": "Not enough room in this browser's storage. Grow it or delete a snapshot."})}
        _, problem, _ = self.run_llc(llc.profile, action="snapshot", id="shop", yes=True)
        self.assertIn("grow", problem.next)

    # --- cookies ---

    def test_cookies_send_the_list_and_print_only_the_count(self):
        items = [{"name": "sid", "value": "cookie-secret-77", "domain": ".example.com", "path": "/"}]
        _, problem, _ = self.run_llc(llc.cookies, id="shop", json=self.file("c.json", items))
        self.assertIn("--yes", problem.message)
        self.assertEqual(self.fake.calls, [])
        res, problem, text = self.run_llc(llc.cookies, id="shop", json=self.file("c.json", items), yes=True)
        self.assertIsNone(problem)
        self.assertEqual(self.fake.calls[-1][1:4:2], ("/v1/workloads/shop/cookies", items))
        self.assertEqual(res["added"], 1)
        self.assertNotIn("cookie-secret-77", text)
        _, problem, _ = self.run_llc(llc.cookies, id="shop", json=self.file("d.json", {"name": "x"}), yes=True)
        self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main()
