"""Signing in with scripts/llc.py, against a stand-in for LiveLLM's sign-in endpoints.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder clean
SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud" / "scripts" / "llc.py"
spec = importlib.util.spec_from_file_location("llc", SCRIPT)
llc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llc)


class FakeSignIn:
    """Hands out device codes and answers each poll the way `answer` says:
    pending, allow, deny, slow or expired."""

    def __init__(self, answer):
        self.answer, self.started, self.polls = answer, 0, 0
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("content-length", 0))).decode())
                with fake.lock:
                    if self.path == "/v1/oauth/device/code":
                        fake.started += 1
                        n = fake.started
                        return self.reply(200, {
                            "device_code": f"dev-{n}", "user_code": f"CODE-{n}",
                            "verification_uri_complete": f"https://console.test/device?code=CODE-{n}",
                            "interval": 1, "expires_in": 600, "scope": form.get("scope", [""])[0]})
                    if self.path == "/v1/oauth/token":
                        fake.polls += 1
                        what = fake.answer(form["device_code"][0], fake.polls)
                if self.path != "/v1/oauth/token":
                    return self.reply(404, {"error": "no such path"})
                if what == "allow":
                    return self.reply(200, {"access_token": "llt_a", "refresh_token": "r", "expires_in": 3600,
                                            "scope": "create", "workspace": "acme"})
                code, desc = {
                    "deny": ("access_denied", "the user denied the sign-in"),
                    "slow": ("slow_down", "poll every 6 seconds"),
                    "expired": ("expired_token", "the sign-in link expired; start again"),
                }.get(what, ("authorization_pending", "waiting for the user to allow the sign-in"))
                self.reply(400, {"error": code, "error_description": desc})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class LoginTest(unittest.TestCase):
    def use(self, answer):
        fake = FakeSignIn(answer)
        self.addCleanup(fake.server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY", "CREDENTIALS", "POLL_UNIT", "LOGIN_WAIT")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = fake.url, ""
        llc.CREDENTIALS = Path(tmp.name) / "credentials.json"
        llc.POLL_UNIT, llc.LOGIN_WAIT = 0.01, 1
        return fake

    def login(self, access="create", wait=False):
        """Runs login; answers what it printed on stdout, and the Problem it raised."""
        buf = io.StringIO()
        problem = None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                llc.login(types.SimpleNamespace(access=access, wait=wait))
            except llc.Problem as p:
                problem = p
        text = buf.getvalue()
        return (json.loads(text) if text.strip() else None), problem

    def test_first_call_prints_the_link_and_returns(self):
        fake = self.use(lambda *_: "pending")
        start = time.time()
        printed, problem = self.login()
        self.assertIsNone(problem)
        self.assertLess(time.time() - start, 0.5)
        self.assertEqual(printed["signedIn"], False)
        self.assertEqual(printed["link"], "https://console.test/device?code=CODE-1")
        self.assertEqual(printed["code"], "CODE-1")
        self.assertTrue(printed["expiresAt"].endswith("Z"))
        self.assertEqual((fake.started, fake.polls), (1, 0))
        path = llc.pending_path()
        self.assertEqual(path.name, "credentials.pending.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(llc.read_pending()["device_code"], "dev-1")

    def test_login_again_finishes_the_same_sign_in(self):
        allowed = threading.Event()
        fake = self.use(lambda code, _: "allow" if allowed.is_set() and code == "dev-1" else "pending")
        self.login()
        allowed.set()
        printed, problem = self.login()
        self.assertIsNone(problem)
        self.assertEqual(printed, {"signedIn": True, "workspace": "acme", "access": "create"})
        self.assertEqual(fake.started, 1)
        self.assertEqual(llc.read_credentials()[llc.API]["access_token"], "llt_a")
        self.assertFalse(llc.pending_path().exists())

    def test_login_again_before_allow_says_still_waiting(self):
        fake = self.use(lambda *_: "pending")
        llc.LOGIN_WAIT = 0.1
        self.login()
        start = time.time()
        printed, problem = self.login()
        self.assertLess(time.time() - start, 2)
        self.assertIsNone(printed)
        self.assertEqual(problem.code, llc.EXIT_NOT_READY)
        self.assertIn("still waiting", problem.next)
        self.assertIn("CODE-1", problem.message)
        self.assertEqual(fake.started, 1)
        self.assertGreaterEqual(fake.polls, 1)
        pending = llc.read_pending()
        self.assertEqual(pending["device_code"], "dev-1")
        self.assertTrue(pending["polled_at"])

    def test_a_denied_or_expired_link_is_replaced(self):
        for answer in ("deny", "expired"):
            with self.subTest(answer):
                fake = self.use(lambda code, _: answer if code == "dev-1" else "pending")
                self.login()
                printed, problem = self.login()
                self.assertIsNone(problem)
                self.assertEqual(printed["code"], "CODE-2")
                self.assertIn("note", printed)
                self.assertEqual(fake.started, 2)
                self.assertEqual(llc.read_pending()["device_code"], "dev-2")

    def test_a_stale_pending_sign_in_is_not_asked_about(self):
        fake = self.use(lambda *_: "pending")
        self.login()
        pending = llc.read_pending()
        pending["expires_at"] = time.time() - 60
        llc.write_pending(pending)
        printed, _ = self.login()
        self.assertEqual(printed["code"], "CODE-2")
        printed, _ = self.login(access="full")
        self.assertEqual(printed["code"], "CODE-3")
        self.assertEqual((fake.started, fake.polls), (3, 0))

    def test_wait_is_one_call_and_slows_down_when_asked(self):
        fake = self.use(lambda _, poll: "slow" if poll == 2 else ("allow" if poll >= 4 else "pending"))
        printed, problem = self.login(wait=True)
        self.assertIsNone(problem)
        self.assertEqual(printed["signedIn"], True)
        self.assertEqual((fake.started, fake.polls), (1, 4))
        self.assertIsNone(llc.read_pending())

    def test_reads_a_pending_sign_in_the_cli_wrote(self):
        self.use(lambda *_: "pending")
        llc.pending_path().write_text(json.dumps({llc.API: {
            "device_code": "dev-9", "user_code": "C", "link": "L", "interval": 5,
            "expires_at": int(time.time()) + 60, "polled_at": time.time(), "access": "create"}}))
        self.assertEqual(llc.read_pending()["device_code"], "dev-9")


if __name__ == "__main__":
    unittest.main()
