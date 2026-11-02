"""A machine's private address with scripts/llc.py, against a stand-in for
LiveLLM's API that records every call: create asks for one with
--private-address or makes the machine without one with --no-private-network
(machines only, one or the other, never against what the file says) and sends
nothing when neither is given; the answer, ls and connect show the address the
machine got, whoami the workspace's range, and reach lists the address among
the machine's inside addresses. A set never sends the fields (they are set when
the machine is made), and each refusal by its code keeps the API's words, has
the user choose and never says to retry unchanged, except an address a deleted
machine still holds, which is freed within a minute.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
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

RANGE = {"range": "172.16.0.0/16", "gateway": "172.16.0.1"}
# The workspace as a LiveLLM with the private network answers it.
WORKSPACE = {"name": "acme", "privateNetwork": RANGE, "spec": {"workloads": [
    {"id": "web", "type": "vm-ubuntu", "reachableFrom": ["worker"],
     "vm": {"privateNetwork": True, "privateAddress": "172.16.0.12", "privateMac": "02:4c:4c:3a:91:0c"}},
    {"id": "worker", "type": "vm-ubuntu", "reachableFrom": [], "vm": {"privateNetwork": False}},
    {"id": "site", "type": "pod", "reachableFrom": [], "pod": {"image": "nginx"}},
]}}

# Each refusal as the API sends it: (status, code).
REFUSALS = {
    "private_network_not_offered": 422, "private_address_invalid": 422, "private_address_outside": 422,
    "private_address_reserved": 422, "private_address_taken": 409, "private_address_releasing": 409,
    "private_address_fixed": 422, "private_range_full": 409,
}


class FakeAPI:
    """Answers GET /v1/workspace with self.workspace, POST …/connect with a
    machine, anything else 200 {}; records every call."""

    def __init__(self):
        self.calls = []
        self.workspace = WORKSPACE
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, body=None):
                fake.calls.append((self.command, self.path, body))
                if self.command == "GET" and self.path == "/v1/workspace":
                    payload = fake.workspace
                elif self.command == "POST" and self.path.endswith("/connect"):
                    payload = {"type": "vm-ubuntu", "token": "t"}
                else:
                    payload = {}
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def body(self):
                return json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"null")

            def do_POST(self):
                self.answer(self.body())

            def do_PATCH(self):
                self.answer(self.body())

            def do_GET(self):
                self.answer()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class PrivateAddressTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAPI()
        self.addCleanup(self.fake.server.server_close)  # runs after shutdown
        self.addCleanup(self.fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = self.fake.url, "llc_test"
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

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

    def file(self, body):
        path = Path(self.dir.name) / "body.json"
        path.write_text(json.dumps(body))
        return str(path)

    def create(self, body, typ="vm-ubuntu", address=None, off=False):
        return self.run_llc(llc.create, type=typ, json=self.file(body), join=None, engine=None,
                            private_address=address, no_private_network=off, yes=True)

    def sent(self):
        (method, path, body), = self.fake.writes()
        self.assertEqual(method, "POST")
        return path, body

    # --- create ---

    def test_neither_flag_sends_nothing_and_the_answer_names_the_address_it_got(self):
        res, problem = self.create({"id": "web"})
        self.assertIsNone(problem)
        self.assertEqual(self.sent(), ("/v1/workloads/vm-ubuntu", {"id": "web"}))
        self.assertEqual(res["privateAddress"], "172.16.0.12")

    def test_private_address_is_sent_flat_beside_the_id(self):
        _, problem = self.create({"id": "web"}, address="172.16.0.12")
        self.assertIsNone(problem)
        self.assertEqual(self.sent()[1], {"id": "web", "privateAddress": "172.16.0.12"})

    def test_no_private_network_sends_false_and_reads_nothing_back(self):
        res, problem = self.create({"id": "worker"}, off=True)
        self.assertIsNone(problem)
        self.assertEqual(self.sent()[1], {"id": "worker", "privateNetwork": False})
        self.assertNotIn("privateAddress", res)
        self.assertEqual([c for c in self.fake.calls if c[0] == "GET"], [])

    def test_the_flags_are_one_or_the_other(self):
        _, problem = self.create({"id": "web"}, address="172.16.0.12", off=True)
        self.assertIn("one or the other", problem.message)
        self.assertEqual(self.fake.calls, [])

    def test_the_flags_are_for_machines_only(self):
        for typ in ("pod", "desktop", "browser", "storage"):
            _, problem = self.create({"id": "x"}, typ=typ, off=True)
            self.assertIn("is for a machine", problem.message, typ)
        # several apps at once, in a list or not
        for body in ([{"id": "a", "image": "nginx"}], {"apps": [{"id": "a", "image": "nginx"}]}):
            _, problem = self.create(body, typ="apps", address="172.16.0.12")
            self.assertIn("is for a machine", problem.message)
        self.assertEqual(self.fake.calls, [])

    def test_a_file_that_says_otherwise_is_refused_not_overridden(self):
        for body, address, off in (({"id": "web", "privateNetwork": False}, "172.16.0.12", False),
                                   ({"id": "web", "privateAddress": "172.16.0.20"}, "172.16.0.12", False),
                                   ({"id": "web", "privateAddress": "172.16.0.20"}, None, True),
                                   ({"id": "web", "privateNetwork": True}, None, True)):
            _, problem = self.create(body, address=address, off=off)
            self.assertEqual(problem.next, "say it once", body)
        self.assertEqual(self.fake.calls, [])
        # the same address in both is said once
        _, problem = self.create({"id": "web", "privateAddress": "172.16.0.12"}, address="172.16.0.12")
        self.assertIsNone(problem)

    def test_an_address_asked_of_a_livellm_without_the_private_network_is_named(self):
        self.fake.workspace = {"name": "acme", "spec": {"workloads": [{"id": "web", "type": "vm-ubuntu", "vm": {}}]}}
        _, problem = self.create({"id": "web"}, address="172.16.0.12")
        self.assertIn("without a private address", problem.message)
        self.assertIn("only if they want it gone", problem.next)
        # asked for nothing: nothing to say
        self.fake.calls.clear()
        res, problem = self.create({"id": "web"})
        self.assertIsNone(problem)
        self.assertNotIn("privateAddress", res)

    # --- reading it ---

    def test_ls_shows_a_machines_private_address(self):
        rows = {r["id"]: r for r in llc.resources("t")}
        self.assertEqual(rows["web"]["privateAddress"], "172.16.0.12")
        self.assertNotIn("privateAddress", rows["worker"])
        self.assertNotIn("privateAddress", rows["site"])

    def test_whoami_shows_the_workspaces_range(self):
        res, problem = self.run_llc(llc.whoami)
        self.assertIsNone(problem)
        self.assertEqual(res["privateNetwork"], RANGE)
        self.fake.workspace = {"name": "acme", "privateNetwork": None}
        res, _ = self.run_llc(llc.whoami)
        self.assertNotIn("privateNetwork", res)

    def test_connect_names_a_machines_private_address(self):
        res, problem = self.run_llc(llc.connect, id="web", tool=None, screen_width=None, format=None, env=False)
        self.assertIsNone(problem)
        self.assertEqual(res["privateAddress"], "172.16.0.12")

    def test_reach_lists_the_private_address_among_the_inside_addresses(self):
        res, problem = self.run_llc(llc.reach, id="web", source=None, none=False, add=None, remove=None, yes=False)
        self.assertIsNone(problem)
        self.assertIn({"host": "172.16.0.12", "private": True}, res["addresses"])
        machine = {"id": "worker", "type": "vm-ubuntu", "vm": {"privateNetwork": False}}
        self.assertFalse([a for a in llc.inside_addresses("acme-worker", machine) if a.get("private")])

    # --- a set ---

    def test_a_set_never_sends_the_fields(self):
        for vm in ({"privateAddress": "172.16.0.40"}, {"privateNetwork": False}, {"privateNetwork": True}):
            _, problem = self.run_llc(llc.set_settings, id="web", json=self.file({"vm": vm}), yes=True)
            self.assertIn("set when it is made", problem.message, vm)
        # flat, as a create file holds them, too
        for flat in ({"privateAddress": "172.16.0.40"}, {"privateNetwork": False}):
            _, problem = self.run_llc(llc.set_settings, id="web", json=self.file(flat), yes=True)
            self.assertIn("set when it is made", problem.message, flat)
        self.assertEqual(self.fake.calls, [])

    # --- refusals ---

    def test_each_refusal_keeps_the_apis_words_and_never_retries_unchanged(self):
        for code, status in REFUSALS.items():
            words = f"workload web: {code}"
            p = llc.http_problem(status, {"error": words, "code": code})
            self.assertEqual((p.status, p.message, p.refusal), (status, words, code), code)
            self.assertNotIn("retry", p.next, code)
            if code == "private_address_releasing":
                self.assertEqual(p.code, llc.EXIT_BUSY)
                self.assertIn("wait a minute", p.next)
            else:
                self.assertNotIn("wait", p.next, code)

    def test_refusals_that_are_the_users_to_choose_ask_them(self):
        for code in ("private_address_outside", "private_address_reserved", "private_address_taken"):
            p = llc.http_problem(REFUSALS[code], {"error": "x", "code": code})
            self.assertEqual(p.code, llc.EXIT_USER, code)
            self.assertIn("ask the user", p.next, code)
            self.assertIn("leave privateAddress out for the next free one", p.next, code)
        p = llc.http_problem(409, {"error": "x", "code": "private_range_full"})
        self.assertIn("tell the user, and stop", p.next)
        self.assertIn("never delete a machine", p.next)
        p = llc.http_problem(422, {"error": "x", "code": "private_address_fixed"})
        self.assertIn("never change", p.next)

    def test_a_code_on_another_status_is_the_generic_answer(self):
        p = llc.http_problem(409, {"error": "x", "code": "private_address_invalid"})
        self.assertIn("retry once", p.next)


if __name__ == "__main__":
    unittest.main()
