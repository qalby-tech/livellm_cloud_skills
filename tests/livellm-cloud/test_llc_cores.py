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

# The API's refusals, in the owner's words.
SHORT = {"error": "Only 6 cores at 5.7 GHz are available. Continue with mixed core speeds?", "code": "core_speed_short",
         "workload": "box", "cores": 8, "fit": 6, "speedMHz": 5700}
MIXED = {"error": "Mixed core speeds: this machine's cores run at different clock speeds.", "code": "core_speeds_mixed",
         "workload": "box", "cores": 20, "fit": 16}
UNKNOWN = {"error": "No server this machine may run on has cores at 3 GHz.", "code": "core_speed_unknown",
           "workload": "box", "cores": 8, "fit": 0, "speedMHz": 3000}
FULL = {"error": "This server is full: it has no room for this machine.", "code": "server_full", "workload": "box"}
NO_ROOM = {"error": "No server has room for this machine.", "code": "server_full", "workload": "box"}
# Every kind that reserves on a server is held to its fill limit, an app too.
APP_NO_ROOM = {"error": "No server has room for this app.", "code": "server_full", "workload": "web"}

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

    def test_none_free_in_the_apis_words_for_none_never_offers_0_cores(self):
        # the API may word none free as "No cores at ... are available", and
        # leave fit out of a core_speeds_mixed: never "0 cores" or "cpus": 0
        for status, payload in (
                (409, {**SHORT, "error": "No cores at 5.7 GHz are available. Continue with mixed core speeds?", "fit": 0}),
                (409, {k: v for k, v in MIXED.items() if k != "fit"})):
            p = llc.http_problem(status, payload)
            self.assertEqual(p.message, payload["error"])
            self.assertIn(f'{payload["cores"]} cores at mixed speeds ("mixedSpeeds": true)', p.next)
            self.assertNotIn('"cpus"', p.next)
            self.assertNotIn("0 cores of one speed", p.next)

    def test_never_fewer_cores_than_the_machine_runs_on(self):
        # a desktop machine or Windows 11 runs on 2 cores at least: 1 that
        # fits is not offered, 2 is
        one = {**SHORT, "error": "Only 1 core at 5.7 GHz is available. Continue with mixed core speeds?", "fit": 1}
        for payload in (one, {**MIXED, "fit": 1}):
            p = llc.cores_problem(409, payload["code"], payload["error"], payload, floor=2)
            self.assertNotIn('"cpus"', p.next, payload)
            self.assertIn("at mixed speeds", p.next)
            self.assertIn('"cpus": 1', llc.cores_problem(409, payload["code"], payload["error"], payload).next)
        p = llc.cores_problem(409, "core_speed_short", SHORT["error"], {**SHORT, "fit": 2}, floor=2)
        self.assertIn('2 cores of one speed ("cpus": 2)', p.next)

    def test_one_core_is_one_core(self):
        p = llc.http_problem(409, {**MIXED, "cores": 1, "fit": 0})
        self.assertIn('1 core at mixed speeds ("mixedSpeeds": true)', p.next)
        p = llc.http_problem(409, {**SHORT, "error": "Only 1 core at 5.7 GHz is available. Continue with mixed core speeds?",
                                   "fit": 1})
        self.assertIn('1 core of one speed ("cpus": 1), or 8 cores at mixed speeds', p.next)

    def test_core_floor_by_kind(self):
        for typ, vm, floor in (("vm-ubuntu", {}, 1), ("vm-ubuntu-desktop", {}, 2), ("vm-windows", {}, 2),
                               ("vm-windows", {"windowsEdition": "desktop"}, 2), ("vm-windows", {"windowsEdition": "server"}, 1),
                               ("vm-windows", None, 2), ("pod", {}, 1), (None, None, 2)):
            self.assertEqual(llc.core_floor(typ, vm), floor, (typ, vm))

    def test_a_speed_no_server_has_points_at_the_speeds_there_are(self):
        p = llc.http_problem(422, UNKNOWN)
        self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 422, UNKNOWN["error"]))
        self.assertIn("llc.py cores lists the speeds", p.next)
        self.assertIn("leave coreSpeed out", p.next)

    def test_no_server_with_room_tells_the_user_and_stops(self):
        for payload in (FULL, NO_ROOM, APP_NO_ROOM):
            p = llc.http_problem(409, payload)
            self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 409, payload["error"]), payload)
            self.assertIn("tell the user, and stop", p.next)
            self.assertIn("never delete to make room", p.next)
            self.assertNotIn("retry", p.next)
            # the words name the kind; what to do is the same for each
            self.assertNotIn("machine", p.next)

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
    {"id": "win", "type": "vm-windows", "vm": {"cpus": 4, "windowsEdition": "desktop"}},
    {"id": "winsrv", "type": "vm-windows", "vm": {"cpus": 4, "windowsEdition": "server"}},
    {"id": "web", "type": "pod", "pod": {"image": "nginx"}},
]}}
STATUS = {"workloads": [
    {"id": "fast", "phase": "Running", "ready": True, "cores": {"speedMHz": 5700}},
    {"id": "desk", "phase": "Running", "ready": True, "cores": {"mixed": True, "message": MIXED["error"]}},
    {"id": "web", "phase": "Running", "ready": True},
]}


class FakeAPI:
    """GET /v1/machine-cores answers `answer` with `status`, GET /v1/workspace
    WORKSPACE (500 with `workspace_down`) and GET /v1/status STATUS; a POST
    or PATCH answers `refuse` (status, body) when given, else 201 {}; records
    every call."""

    def __init__(self, status=200, answer=None, refuse=None, workspace_down=False):
        self.calls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fake.calls.append((self.command, self.path))
                if self.path == "/v1/workspace" and workspace_down:
                    self.reply(500, {"error": "down"})
                    return
                if self.path in ("/v1/workspace", "/v1/status"):
                    self.reply(200, WORKSPACE if self.path == "/v1/workspace" else STATUS)
                    return
                self.reply(status, answer if answer is not None else {})

            def write(self):
                n = int(self.headers.get("content-length") or 0)
                fake.calls.append((self.command, self.path, json.loads(self.rfile.read(n) or b"{}")))
                self.reply(*(refuse or (201, {})))

            do_POST = do_PATCH = write

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

    def use(self, status=200, answer=None, **kw):
        fake = FakeAPI(status, answer, **kw)
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
        self.assertEqual(got, {"fast": {"speedMHz": 5700}, "desk": {"mixed": True, "message": MIXED["error"]}, "win": None,
                               "winsrv": None, "web": None})

    def machine_file(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "machine.json"
        path.write_text(json.dumps(body))
        return str(path)

    def test_a_create_refused_offers_no_fewer_cores_than_the_kind_runs_on(self):
        # 1 core of 5.7 GHz fits: offered for a server and Windows Server,
        # never for a desktop machine or Windows 11 (2 at least)
        one = {**SHORT, "error": "Only 1 core at 5.7 GHz is available. Continue with mixed core speeds?", "fit": 1}
        for typ, body, offered in (("vm-ubuntu", {}, True), ("vm-ubuntu-desktop", {}, False),
                                   ("vm-windows", {}, False), ("vm-windows", {"windowsEdition": "server"}, True)):
            fake = self.use(refuse=(409, one))
            f = self.machine_file({"id": "box", "cpus": 8, "dedicatedCores": True, "coreSpeed": 5700, **body})
            _, problem = self.run_llc(llc.create, json=f, type=typ)
            self.assertEqual((problem.message, problem.status, problem.refusal), (one["error"], 409, "core_speed_short"))
            self.assertEqual('1 core of one speed ("cpus": 1)' in problem.next, offered, (typ, body, problem.next))
            self.assertIn('8 cores at mixed speeds ("mixedSpeeds": true)', problem.next)
            self.assertEqual([c[:2] for c in fake.calls], [("POST", f"/v1/workloads/{typ}")])

    def test_a_set_refused_reads_the_machines_kind_for_its_floor(self):
        one = {**MIXED, "fit": 1, "cores": 6}
        f = self.machine_file({"vm": {"cpus": 6, "dedicatedCores": True}})
        for wid, offered in (("fast", True), ("desk", False), ("win", False), ("winsrv", True)):
            fake = self.use(refuse=(409, {**one, "workload": wid}))
            _, problem = self.run_llc(llc.set_settings, json=f, id=wid)
            self.assertEqual(problem.refusal, "core_speeds_mixed")
            self.assertEqual('"cpus": 1' in problem.next, offered, (wid, problem.next))
            self.assertIn('6 cores at mixed speeds ("mixedSpeeds": true)', problem.next)
            self.assertEqual([c[:2] for c in fake.calls], [("PATCH", f"/v1/workloads/{wid}"), ("GET", "/v1/workspace")])
        # 2 that fit are offered to a desktop machine
        self.use(refuse=(409, {**one, "fit": 2, "workload": "desk"}))
        _, problem = self.run_llc(llc.set_settings, json=f, id="desk")
        self.assertIn('2 cores of one speed ("cpus": 2)', problem.next)
        # the kind unread: the most any machine needs
        self.use(refuse=(409, {**one, "workload": "fast"}), workspace_down=True)
        _, problem = self.run_llc(llc.set_settings, json=f, id="fast")
        self.assertEqual(problem.message, one["error"])
        self.assertNotIn('"cpus"', problem.next)
        # any other refusal of a set is left as it is, with no read
        fake = self.use(refuse=(409, FULL))
        _, problem = self.run_llc(llc.set_settings, json=f, id="desk")
        self.assertIn("tell the user, and stop", problem.next)
        self.assertEqual([c[:2] for c in fake.calls], [("PATCH", "/v1/workloads/desk")])

    def test_a_livellm_without_dedicated_cores_says_so(self):
        self.use(status=404, answer={"error": "404 page not found"})
        _, problem = self.run_llc(llc.cores)
        self.assertEqual(problem.message, "this LiveLLM has no dedicated cores yet")
        self.assertIn("leave dedicatedCores, coreSpeed and mixedSpeeds out", problem.next)


if __name__ == "__main__":
    unittest.main()
