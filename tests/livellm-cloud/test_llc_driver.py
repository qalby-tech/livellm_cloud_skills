"""A machine's NVIDIA driver with scripts/llc.py, against a stand-in for
LiveLLM's API: gpu-driver reads it as the API answers with what to do next,
gpu-driver-install posts with no body, and a LiveLLM without the routes says
so plainly.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
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

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                fake.calls.append((self.command, self.path, self.rfile.read(n)))
                self.reply()

            def do_PUT(self):
                n = int(self.headers.get("content-length") or 0)
                fake.calls.append((self.command, self.path, json.loads(self.rfile.read(n) or b"{}")))
                self.reply()

            def reply(self):
                body = answer if answer is not None else {"state": "working", "detail": "620.70.06"}
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



class Driver(Stand):

    def test_reads_the_state(self):
        fake = self.use()
        res, problem = self.run_llc(llc.gpu_driver, id="box")
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("GET", "/v1/workloads/box/gpu-driver")])
        self.assertEqual(res["state"], "working")
        self.assertNotIn("next", res)

    def test_restart_needed_asks_the_user(self):
        self.use(answer={"state": "restart-needed", "detail": "installed"})
        res, _ = self.run_llc(llc.gpu_driver, id="box")
        self.assertIn("only if they agree", res["next"])
        self.assertIn("llc.py restart box", res["next"])

    def test_not_installed_names_the_install(self):
        self.use(answer={"state": "not-installed"})
        res, _ = self.run_llc(llc.gpu_driver, id="box")
        self.assertIn("gpu-driver-install box --yes", res["next"])

    def test_install_posts_nothing(self):
        fake = self.use(status=202, answer={"state": "installing", "detail": "started"})
        res, problem = self.run_llc(llc.gpu_driver_install, id="box", yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls[0][:2], ("POST", "/v1/workloads/box/gpu-driver"))
        self.assertEqual(res["state"], "installing")
        self.assertIn("gpu-driver box", res["next"])

    def test_route_missing(self):
        self.use(status=404, answer="404 page not found\n")
        _, problem = self.run_llc(llc.gpu_driver, id="box")
        self.assertIn("isn't available on this LiveLLM", problem.message)
        self.assertEqual(problem.status, 404)

    def test_refusal_keeps_the_api_words(self):
        self.use(status=422, answer={"error": "box's system has no automatic NVIDIA driver.", "code": "gpu_driver_unsupported"})
        _, problem = self.run_llc(llc.gpu_driver_install, id="box", yes=True)
        self.assertIn("no automatic NVIDIA driver", problem.message)


if __name__ == "__main__":
    unittest.main()
