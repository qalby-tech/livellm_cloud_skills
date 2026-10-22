"""A machine's dedicated cores with scripts/llc.py: the speeds a machine may ask
for (llc.py cores), against a stand-in for LiveLLM's API that records every
call, and what the agent is told about a cores refusal: too few cores of one
speed (409 core_speed_short, core_speeds_mixed), a speed no server has (422
core_speed_unknown) and no server with room (409 server_full). Each keeps the
API's words, has the user choose, and never says to retry unchanged.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
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

# The API's refusals, in the owner's words.
SHORT = {"error": "Only 6 cores at 5.7 GHz are available. Continue with mixed core speeds?", "code": "core_speed_short",
         "workload": "box", "cores": 8, "fit": 6, "speedMHz": 5700}
MIXED = {"error": "Mixed core speeds: this machine's cores run at different clock speeds.", "code": "core_speeds_mixed",
         "workload": "box", "cores": 20, "fit": 16}
UNKNOWN = {"error": "No server this machine may run on has cores at 3 GHz.", "code": "core_speed_unknown",
           "workload": "box", "cores": 8, "fit": 0, "speedMHz": 3000}
FULL = {"error": "This server is full: it has no room for this machine.", "code": "server_full", "workload": "box"}
NO_ROOM = {"error": "No server has room for this machine.", "code": "server_full", "workload": "box"}

SPEEDS = {"showSpeed": True, "speeds": [{"speedMHz": 5700, "fit": 16}, {"speedMHz": 4400, "fit": 16}], "dedicatedFit": 16}


class CoresRefusals(unittest.TestCase):

    def test_too_few_of_one_speed_offers_the_cores_that_fit_or_mixed_speeds(self):
        for status, payload, fit, asked in ((409, SHORT, 6, 8), (409, MIXED, 16, 20)):
            p = llc.http_problem(status, payload)
            self.assertEqual((p.code, p.status, p.message, p.refusal),
                             (llc.EXIT_USER, status, payload["error"], payload["code"]), payload)
            self.assertIn("ask which they want", p.next)
            self.assertIn(f'{fit} cores of one speed ("cpus": {fit})', p.next)
            self.assertIn(f'{asked} cores at mixed speeds ("mixedSpeeds": true)', p.next)
            self.assertIn("never unchanged", p.next)
            self.assertNotIn("retry", p.next)

    def test_none_of_that_speed_free_offers_mixed_speeds_or_another_speed(self):
        p = llc.http_problem(409, {**SHORT, "error": "Only 0 cores at 5.7 GHz are available. Continue with mixed core speeds?",
                                   "fit": 0})
        self.assertIn('8 cores at mixed speeds ("mixedSpeeds": true)', p.next)
        self.assertIn("another coreSpeed (llc.py cores", p.next)
        self.assertNotIn('"cpus"', p.next)

    def test_a_speed_no_server_has_points_at_the_speeds_there_are(self):
        p = llc.http_problem(422, UNKNOWN)
        self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 422, UNKNOWN["error"]))
        self.assertIn("llc.py cores lists the speeds", p.next)
        self.assertIn("leave coreSpeed out", p.next)

    def test_no_server_with_room_tells_the_user_and_stops(self):
        for payload in (FULL, NO_ROOM):
            p = llc.http_problem(409, payload)
            self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 409, payload["error"]), payload)
            self.assertIn("tell the user, and stop", p.next)
            self.assertIn("never delete to make room", p.next)
            self.assertNotIn("retry", p.next)

    def test_known_by_the_code_alone(self):
        # the same words without the code are any other 409; the code without
        # the numbers still asks the user
        p = llc.http_problem(409, {"error": MIXED["error"]})
        self.assertIn("retry once", p.next)
        p = llc.http_problem(409, {"error": "x", "code": "core_speeds_mixed"})
        self.assertIn("the same cores at mixed speeds", p.next)


WORKSPACE = {"spec": {"workloads": [
    {"id": "fast", "type": "vm-ubuntu", "vm": {"cpus": 8, "dedicatedCores": True}},
    {"id": "desk", "type": "vm-ubuntu-desktop", "vm": {"cpus": 4}},
    {"id": "web", "type": "pod", "pod": {"image": "nginx"}},
]}}
STATUS = {"workloads": [
    {"id": "fast", "phase": "Running", "ready": True, "cores": {"speedMHz": 5700}},
    {"id": "desk", "phase": "Running", "ready": True, "cores": {"mixed": True, "message": MIXED["error"]}},
    {"id": "web", "phase": "Running", "ready": True},
]}


class FakeAPI:
    """GET /v1/machine-cores answers `answer` with `status`, GET /v1/workspace
    WORKSPACE and GET /v1/status STATUS; records every call."""

    def __init__(self, status=200, answer=None):
        self.calls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fake.calls.append((self.command, self.path))
                if self.path in ("/v1/workspace", "/v1/status"):
                    self.reply(200, WORKSPACE if self.path == "/v1/workspace" else STATUS)
                    return
                self.reply(status, answer if answer is not None else {})

            def reply(self, status, answer):
                raw = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class CoresCommand(unittest.TestCase):

    def use(self, status=200, answer=None):
        fake = FakeAPI(status, answer)
        self.addCleanup(fake.server.server_close)
        self.addCleanup(fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = fake.url, "llc_test"
        return fake

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

    def test_cores_gives_the_speeds_as_the_platform_answers(self):
        fake = self.use(answer=SPEEDS)
        res, problem = self.run_llc(llc.cores)
        self.assertIsNone(problem)
        self.assertEqual(res, SPEEDS)
        self.assertEqual(fake.calls, [("GET", "/v1/machine-cores")])

    def test_ls_shows_the_clock_a_running_machines_cores_got(self):
        self.use()
        res, problem = self.run_llc(llc.ls, type=None)
        self.assertIsNone(problem)
        got = {r["id"]: r.get("cores") for r in res["resources"]}
        self.assertEqual(got, {"fast": {"speedMHz": 5700}, "desk": {"mixed": True, "message": MIXED["error"]}, "web": None})

    def test_a_livellm_without_dedicated_cores_says_so(self):
        self.use(status=404, answer={"error": "404 page not found"})
        _, problem = self.run_llc(llc.cores)
        self.assertEqual(problem.message, "this LiveLLM has no dedicated cores yet")
        self.assertIn("leave dedicatedCores, coreSpeed and mixedSpeeds out", problem.next)


if __name__ == "__main__":
    unittest.main()
