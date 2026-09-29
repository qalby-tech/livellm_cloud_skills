"""Running a command with scripts/llc.py exec, against a stand-in for LiveLLM's exec calls.

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


class FakeExec:
    """Answers POST .../exec with the first of `answers` and each GET of its run
    with the next one, recording every call."""

    def __init__(self, answers):
        self.answers, self.calls, self.bodies = list(answers), [], []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self):
                with fake.lock:
                    fake.calls.append(f"{self.command} {self.path}")
                    status, body = fake.answers.pop(0) if fake.answers else (404, {"error": "no such run"})
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                fake.bodies.append(json.loads(self.rfile.read(int(self.headers.get("content-length", 0)))))
                self.answer()

            def do_GET(self):
                self.answer()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class ExecTest(unittest.TestCase):
    def use(self, answers):
        fake = FakeExec(answers)
        self.addCleanup(fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY", "POLL_UNIT")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY, llc.POLL_UNIT = fake.url, "llc_test", 0.01
        return fake

    def run_exec(self, command="make", timeout=60, session=None):
        buf = io.StringIO()
        problem = None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                llc.run_command(types.SimpleNamespace(id="box", command=command, timeout=timeout,
                                                      session=session))
            except llc.Problem as p:
                problem = p
        text = buf.getvalue()
        return (json.loads(text) if text.strip() else None), problem

    def test_waits_for_a_command_still_going(self):
        run = "0123456789abcdef0123456789abcdef"
        fake = self.use([
            (202, {"done": False, "runId": run, "output": "a\n"}),
            (202, {"done": False, "runId": run, "output": "a\nb\n"}),
            (200, {"done": True, "runId": run, "exitCode": 3, "output": "a\nb\nc\n"}),
        ])
        printed, problem = self.run_exec(timeout=300, session="job")
        self.assertIsNone(problem)
        self.assertEqual(printed["exitCode"], 3)
        self.assertEqual(printed["output"], "a\nb\nc\n")
        self.assertEqual(fake.calls, [
            "POST /v1/workloads/box/exec",
            f"GET /v1/workloads/box/exec/{run}?wait=55",
            f"GET /v1/workloads/box/exec/{run}?wait=55",
        ])
        self.assertEqual(fake.bodies[0], {"command": "make", "timeout": 300, "wait": 55, "session": "job"})

    def test_a_command_that_ends_at_once_is_one_call(self):
        fake = self.use([(200, {"done": True, "runId": "r", "exitCode": 0, "output": "hi\n"})])
        printed, problem = self.run_exec(command="echo hi")
        self.assertIsNone(problem)
        self.assertEqual(printed["output"], "hi\n")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.bodies[0], {"command": "echo hi", "timeout": 60, "wait": 55})

    def test_an_answer_from_before_runs_is_the_end(self):
        fake = self.use([(200, {"exitCode": 0, "stdout": "hi\n", "stderr": ""})])
        printed, problem = self.run_exec()
        self.assertIsNone(problem)
        self.assertEqual(printed["stdout"], "hi\n")
        self.assertEqual(len(fake.calls), 1)

    def test_a_forgotten_run_is_a_problem(self):
        self.use([(202, {"done": False, "runId": "r1", "output": ""})])
        printed, problem = self.run_exec()
        self.assertIsNone(printed)
        self.assertIsNotNone(problem)
        self.assertEqual(problem.status, 404)

    def test_past_its_time_limit_it_stops_looking(self):
        saved = llc.EXEC_SLACK
        self.addCleanup(lambda: setattr(llc, "EXEC_SLACK", saved))
        llc.EXEC_SLACK = -1  # the time limit has passed as soon as it starts
        fake = self.use([(202, {"done": False, "runId": "r1", "output": "still\n"})])
        printed, problem = self.run_exec(timeout=0)
        self.assertEqual(printed["runId"], "r1")
        self.assertEqual(problem.code, llc.EXIT_BUSY)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
