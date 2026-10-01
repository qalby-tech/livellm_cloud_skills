"""Where resources run with scripts/llc.py: the host list, and the location a
restore or a new Browser API sends, against a stand-in for LiveLLM's API that
records every call.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
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

# The host list as the platform answers it: more than choosing a place needs.
HOSTS = {"hosts": [
    {"id": "h1", "ip": "10.0.0.1", "region": "eu-west", "zone": "eu-west-a", "nodeGroup": "",
     "cpuTotal": 32, "cpuFree": 10.5, "memTotalGi": 125.4, "memFreeGi": 31.2,
     "gpuTotal": 0, "gpuFree": 0, "utilization": 0.22, "ready": True},
    {"id": "h2", "region": "eu-west", "zone": "eu-west-b", "cpuFree": 4, "memFreeGi": 8,
     "gpuType": "L4", "gpuTotal": 2, "gpuFree": 1, "ready": False},
]}
WORKSPACE = {"spec": {"workloads": [
    {"id": "db", "type": "storage", "storage": {"engine": "postgres"}},
    {"id": "box", "type": "vm-ubuntu", "vm": {}},
]}}


class FakeAPI:
    """Answers GET /v1/fleet/hosts with HOSTS, GET /v1/workspace with
    WORKSPACE, anything else 202 {}; records every call."""

    def __init__(self):
        self.calls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, body=None):
                fake.calls.append((self.command, self.path, body))
                payload = {"GET /v1/fleet/hosts": HOSTS, "GET /v1/workspace": WORKSPACE}.get(
                    f"{self.command} {self.path}", {})
                raw = json.dumps(payload).encode()
                self.send_response(200 if self.command == "GET" else 202)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                self.answer(json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"null"))

            def do_GET(self):
                self.answer()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class PlacementTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAPI()
        self.addCleanup(self.fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = self.fake.url, "llc_test"
        os.environ["NEW_DB_PASSWORD"] = "s3cret-pass"
        self.addCleanup(os.environ.pop, "NEW_DB_PASSWORD", None)

    def run_llc(self, fn, **kw):
        buf = io.StringIO()
        problem = None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                fn(types.SimpleNamespace(**kw))
            except llc.Problem as p:
                problem = p
        text = buf.getvalue()
        return (json.loads(text) if text.strip() else None), problem

    # --- hosts ---

    def test_hosts_keeps_only_what_choosing_a_place_needs(self):
        res, problem = self.run_llc(llc.hosts)
        self.assertIsNone(problem)
        self.assertEqual(self.fake.calls, [("GET", "/v1/fleet/hosts", None)])
        self.assertEqual(res, {"hosts": [
            {"id": "h1", "region": "eu-west", "zone": "eu-west-a", "cpuFree": 10.5, "memFreeGi": 31.2,
             "gpuFree": 0, "ready": True},
            {"id": "h2", "region": "eu-west", "zone": "eu-west-b", "cpuFree": 4, "memFreeGi": 8,
             "gpuType": "L4", "gpuFree": 1, "ready": False},
        ]})
        for h in res["hosts"]:
            for gone in ("ip", "cpuTotal", "memTotalGi", "utilization", "nodeGroup", "gpuTotal"):
                self.assertNotIn(gone, h)

    def test_hosts_view_of_an_empty_answer(self):
        self.assertEqual(llc.hosts_view({}), {"hosts": []})
        self.assertEqual(llc.hosts_view({"hosts": None}), {"hosts": []})

    # --- the location itself ---

    def test_placement(self):
        self.assertIsNone(llc.placement(None, None))
        self.assertIsNone(llc.placement("", ""))
        self.assertEqual(llc.placement("h1", None), {"strategy": "host", "host": "h1"})
        self.assertEqual(llc.placement(None, "eu-west"), {"strategy": "region", "region": "eu-west"})
        with self.assertRaises(llc.Problem):
            llc.placement("h1", "eu-west")

    # --- restore ---

    def restore(self, wid="db", **kw):
        args = {"id": wid, "backup": "b1", "as_id": "db-restored" if wid == "db" else None, "at": None,
                "password_env": "NEW_DB_PASSWORD" if wid == "db" else None, "host": None, "region": None,
                "yes": True}
        args.update(kw)
        return self.run_llc(llc.restore, **args)

    def restore_sent(self):
        posts = self.fake.writes()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], "/v1/workloads/db/backups/b1/restore")
        return posts[0][2]

    def test_restore_without_a_location_is_automatic(self):
        _, problem = self.restore()
        self.assertIsNone(problem)
        body = self.restore_sent()
        self.assertNotIn("placement", body)
        self.assertEqual(body, {"id": "db-restored", "credentials": {"password": "s3cret-pass"}})

    def test_restore_onto_a_host(self):
        _, problem = self.restore(host="h1")
        self.assertIsNone(problem)
        self.assertEqual(self.restore_sent()["placement"], {"strategy": "host", "host": "h1"})

    def test_restore_into_a_region(self):
        _, problem = self.restore(region="eu-west", at="2026-09-25T14:05:00Z")
        self.assertIsNone(problem)
        body = self.restore_sent()
        self.assertEqual(body["placement"], {"strategy": "region", "region": "eu-west"})
        self.assertEqual(body["pointInTime"], "2026-09-25T14:05:00Z")

    def test_restore_with_host_and_region_sends_nothing(self):
        _, problem = self.restore(host="h1", region="eu-west")
        self.assertIsNotNone(problem)
        self.assertEqual(self.fake.calls, [])

    def test_a_machine_restore_takes_no_location(self):
        _, problem = self.restore(wid="box", host="h1")
        self.assertIsNotNone(problem)
        self.assertIn("in place", problem.message)
        self.assertEqual(self.fake.writes(), [])

    def test_restore_body(self):
        self.assertEqual(llc.restore_body("n", None, "p"), {"id": "n", "credentials": {"password": "p"}})
        self.assertEqual(llc.restore_body("n", None, "p", {"strategy": "host", "host": "h1"})["placement"],
                         {"strategy": "host", "host": "h1"})

    # --- Browser API ---

    def browser_api(self, action="create", **kw):
        args = {"action": action, "name": "scrapers", "browser": None, "browsers": "agent-1,agent-2",
                "all": False, "remote": None, "host": None, "region": None, "yes": True}
        args.update(kw)
        return self.run_llc(llc.browser_api, **args)

    def test_browser_api_create_onto_a_host(self):
        _, problem = self.browser_api(host="h1")
        self.assertIsNone(problem)
        (method, path, body), = self.fake.writes()
        self.assertEqual((method, path), ("POST", "/v1/workloads/controller"))
        self.assertEqual(body, {"id": "scrapers", "autodiscover": False, "browsers": ["agent-1", "agent-2"],
                                "placement": {"strategy": "host", "host": "h1"}})

    def test_browser_api_create_into_a_region(self):
        _, problem = self.browser_api(region="eu-west", browsers=None, all=True)
        self.assertIsNone(problem)
        (_, _, body), = self.fake.writes()
        self.assertEqual(body["placement"], {"strategy": "region", "region": "eu-west"})

    def test_browser_api_create_without_a_location(self):
        _, problem = self.browser_api()
        self.assertIsNone(problem)
        (_, _, body), = self.fake.writes()
        self.assertNotIn("placement", body)

    def test_browser_api_with_host_and_region_sends_nothing(self):
        _, problem = self.browser_api(host="h1", region="eu-west")
        self.assertIsNotNone(problem)
        self.assertEqual(self.fake.calls, [])

    def test_a_location_is_only_for_create(self):
        _, problem = self.browser_api(action="add", browser="agent-3", host="h1")
        self.assertIsNotNone(problem)
        self.assertEqual(self.fake.calls, [])

    def test_a_database_still_starting_is_not_retried(self):
        msg = ('workloads[0]: database "db" is still starting, so its location can\'t change yet '
               '(delete it and create it again to start it elsewhere)')
        problem = llc.status_problem(409, {"error": msg})
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertNotIn("retry once", problem.next)
        self.assertIn("ask the user", problem.next)

    def test_other_409s_keep_the_retry_hint(self):
        problem = llc.status_problem(409, {"error": "web is mid-change"})
        self.assertEqual(problem.code, llc.EXIT_BUSY)
        self.assertIn("retry once", problem.next)


if __name__ == "__main__":
    unittest.main()
