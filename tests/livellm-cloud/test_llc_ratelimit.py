"""Too many requests (429 rate_limited, LiveLLM's request limits) in scripts/llc.py:
waited out as Retry-After says and sent again; any other 429 passed on at once.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder clean
SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud" / "scripts" / "llc.py"
spec = importlib.util.spec_from_file_location("llc_ratelimit", SCRIPT)
llc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llc)


class Fake:
    """Answers `refuse` 429s with `code` and Retry-After `after`, then 200."""

    def __init__(self, refuse, code="rate_limited", after="2"):
        self.refuse, self.code, self.after, self.calls, self.bodies = refuse, code, after, 0, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self):
                n = int(self.headers.get("content-length", 0) or 0)
                fake.bodies.append(self.rfile.read(n).decode() if n else "")
                fake.calls += 1
                if fake.calls <= fake.refuse:
                    raw = json.dumps({"error": "Too many requests. Try again shortly.", "code": fake.code}).encode()
                    self.send_response(429)
                    self.send_header("Retry-After", fake.after)
                else:
                    raw = b'{"ok": true}'
                    self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


class RateLimited(unittest.TestCase):
    def setUp(self):
        self.waits = []
        self.old_sleep = llc.SLEEP
        llc.SLEEP = self.waits.append

    def tearDown(self):
        llc.SLEEP = self.old_sleep

    def test_waits_as_retry_after_says_and_sends_it_again(self):
        fake = Fake(2)
        try:
            self.assertEqual(llc.request("POST", fake.url + "/v1/workloads", body={"id": "web"}, token="t"), {"ok": True})
        finally:
            fake.close()
        self.assertEqual(fake.calls, 3)
        self.assertEqual(set(fake.bodies), {'{"id": "web"}'})
        self.assertEqual(len(self.waits), 2)
        for w in self.waits:
            self.assertTrue(2 <= w <= 2.5, w)

    def test_past_the_tries_it_is_the_refusal_with_a_next_step(self):
        fake = Fake(100)
        try:
            with self.assertRaises(llc.Problem) as cm:
                llc.request("GET", fake.url + "/v1/status", token="t")
        finally:
            fake.close()
        self.assertEqual(fake.calls, llc.RETRY_TRIES + 1)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.code, llc.EXIT_BUSY)
        self.assertIn("wait a minute", cm.exception.next)

    def test_a_wait_of_minutes_is_passed_on_at_once(self):
        fake = Fake(1, after="900")
        try:
            with self.assertRaises(llc.Problem):
                llc.request("GET", fake.url + "/v1/status", token="t")
        finally:
            fake.close()
        self.assertEqual((fake.calls, self.waits), (1, []))

    def test_another_429_is_passed_on_at_once(self):
        for code in ("billing_rate", "change_ip_too_soon", "support_asked"):
            fake = Fake(1, code=code, after="60")
            try:
                with self.assertRaises(llc.Problem) as cm:
                    llc.request("POST", fake.url + "/v1/x", body={}, token="t")
            finally:
                fake.close()
            self.assertEqual((fake.calls, self.waits), (1, []), code)
            self.assertEqual(cm.exception.refusal, code)

    def test_retry_wait(self):
        self.assertIsNone(llc.retry_wait("61", 1))
        self.assertTrue(1 <= llc.retry_wait(None, 1) <= 1.25)
        self.assertTrue(4 <= llc.retry_wait("soon", 3) <= 5)
        self.assertTrue(0.2 <= llc.retry_wait("0", 1) <= 0.25)


if __name__ == "__main__":
    unittest.main()
