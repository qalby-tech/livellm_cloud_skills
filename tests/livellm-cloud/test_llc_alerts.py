"""A resource's alert settings with scripts/llc.py, against a stand-in for
LiveLLM's API that records every call: `alerts` reads them as the API
answers, `alerts-set` sends only the kinds named, a refused value names the
part (and the flag to fix), an agent's refusal says only an owner changes
them (never a permission on the Agents page), and a LiveLLM without them
says so plainly.

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

SETTINGS = {"usual": {"cpu": 90, "memory": 90, "disk": 90}, "kinds": ["down", "cpu", "memory", "disk"],
            "settings": {"down": {"mode": "usual"}, "cpu": {"mode": "off"}, "memory": {"mode": "level", "level": 97},
                         "disk": {"mode": "usual"}}}


class FakeAPI:
    """Every call answers `answer` with `status` (a str answer is sent as
    plain text, as a router's 404 page is); records every call."""

    def __init__(self, status=200, answer=None):
        self.calls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fake.calls.append((self.command, self.path))
                self.reply()

            def do_PUT(self):
                n = int(self.headers.get("content-length") or 0)
                fake.calls.append((self.command, self.path, json.loads(self.rfile.read(n) or b"{}")))
                self.reply()

            def reply(self):
                body = answer if answer is not None else SETTINGS
                if isinstance(body, str):
                    raw, kind = body.encode(), "text/plain; charset=utf-8"
                else:
                    raw, kind = json.dumps(body).encode(), "application/json"
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class Stand(unittest.TestCase):

    def use(self, **kw):
        fake = FakeAPI(**kw)
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

    def set_args(self, **kw):
        args = {"id": "box", "down": None, "cpu": None, "memory": None, "disk": None, "gpu": None, "yes": True}
        args.update(kw)
        return args


class ReadSettings(Stand):

    def test_alerts_gives_the_settings_as_the_api_answers(self):
        fake = self.use()
        res, problem = self.run_llc(llc.alerts, id="box")
        self.assertIsNone(problem)
        self.assertEqual(res, SETTINGS)
        self.assertEqual(fake.calls, [("GET", "/v1/workloads/box/alert-settings")])

    def test_a_livellm_without_alert_settings_says_so(self):
        self.use(status=404, answer="404 page not found\n")
        res, problem = self.run_llc(llc.alerts, id="box")
        self.assertIsNone(res)
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 404))
        self.assertIn("aren't available", problem.message)

    def test_a_resource_that_isnt_there_is_still_a_wrong_id(self):
        self.use(status=404, answer={"error": "workload not found: nope"})
        _, problem = self.run_llc(llc.alerts, id="nope")
        self.assertEqual(problem.status, 404)
        self.assertIn("workload not found", problem.message)
        self.assertIn("llc.py ls", problem.next)


    def test_a_refused_read_keeps_the_usual_advice(self):
        self.use(status=403, answer={"error": "forbidden"})
        _, problem = self.run_llc(llc.alerts, id="box")
        self.assertEqual(problem.status, 403)
        self.assertNotIn("alert settings", problem.next)

class ChangeSettings(Stand):

    def test_only_the_kinds_named_are_sent(self):
        fake = self.use()
        res, problem = self.run_llc(llc.alerts_set, **self.set_args(cpu="off", memory="97", disk="usual"))
        self.assertIsNone(problem)
        self.assertEqual(res, SETTINGS)
        self.assertEqual(fake.calls, [("PUT", "/v1/workloads/box/alert-settings", {"settings": {
            "cpu": {"mode": "off"}, "memory": {"mode": "level", "level": 97}, "disk": {"mode": "usual"}}})])

    def test_down_and_gpu_take_usual_or_off_only(self):
        fake = self.use()
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(down="80"))
        self.assertIn("usual or off", problem.message)
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(gpu="off", down="usual"))
        self.assertIsNone(problem)
        self.assertEqual(fake.calls[-1][2], {"settings": {"down": {"mode": "usual"}, "gpu": {"mode": "off"}}})

    def test_nothing_named_sends_nothing(self):
        fake = self.use()
        _, problem = self.run_llc(llc.alerts_set, **self.set_args())
        self.assertIn("at least one", problem.message)
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(cpu="high"))
        self.assertIn("--cpu", problem.message)
        self.assertEqual(fake.calls, [])

    def test_a_refused_value_names_the_field_and_the_flag(self):
        self.use(status=422, answer={"error": "the level is a whole percent from 50 to 99", "code": "alert_settings",
                                     "field": "settings.memory.level"})
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(memory="40"))
        self.assertEqual(problem.status, 422)
        self.assertIn("whole percent from 50 to 99", problem.message)
        self.assertIn("settings.memory.level", problem.message)
        self.assertIn("--memory", problem.next)
        self.assertIn("do not retry unchanged", problem.next)

    def test_a_kind_that_doesnt_apply_names_it(self):
        self.use(status=422, answer={"error": "disk alerts don't apply to web", "code": "alert_settings",
                                     "field": "settings.disk"})
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(id="web", disk="off"))
        self.assertIn("don't apply to web", problem.message)
        self.assertIn("--disk", problem.next)

    def test_an_agent_is_refused_and_told_only_an_owner_changes_them(self):
        self.use(status=403, answer={"error": "Agents can't do this. Do it in the LiveLLM console."})
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(cpu="off"))
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 403))
        self.assertEqual(problem.message, "Agents can't do this. Do it in the LiveLLM console.")
        self.assertIn("owners", problem.next)
        self.assertNotIn("Agents page", problem.next)

    def test_a_members_key_is_refused_as_the_owners(self):
        self.use(status=403, answer={"error": "Only the workspace's owners can do this.", "code": "owners_only"})
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(cpu="off"))
        self.assertEqual(problem.message, "Only the workspace's owners can do this.")
        self.assertIn("owner's API key", problem.next)

    def test_a_livellm_without_alert_settings_says_so_on_a_change_too(self):
        self.use(status=404, answer="404 page not found\n")
        _, problem = self.run_llc(llc.alerts_set, **self.set_args(cpu="off"))
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertIn("aren't available", problem.message)


if __name__ == "__main__":
    unittest.main()
