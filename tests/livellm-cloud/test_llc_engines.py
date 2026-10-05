"""Browser engines (Chrome, Camoufox) with scripts/llc.py and the connect
examples in assets/, against stand-ins that record every call.

The refusals are pinned to the wording the platform's engine contract gives
them (422 engine_unavailable, engine_fixed, extensions_unsupported,
engine_mismatch, profile_engine; 409 profile_newer for Camoufox); the Chrome
answers must come out exactly as they did before engines existed. Only a
browser has an engine: a Browser API holds browsers of both engines, and its
create sends no engine.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library (node, when it is there, for the .mjs examples).
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder clean
SKILL = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


llc = load("llc_engines", SKILL / "scripts" / "llc.py")

CHROME_CONNECT = {"tool": "cdp", "engine": "chrome", "token": "llt_chrome", "expiresAt": "2026-10-05T12:15:00Z",
                  "cdp": {"url": "wss://shop-cdp-ab.apps.example/devtools/browser/default",
                          "headers": {"Authorization": "Bearer llt_chrome"}},
                  "api": {"url": "https://shop-cdp-ab.apps.example", "headers": {"Authorization": "Bearer llt_chrome"}}}
CAMOUFOX_CONNECT = {"tool": "cdp", "engine": "camoufox", "token": "llt_fox", "expiresAt": "2026-10-05T12:15:00Z",
                    "playwright": {"url": "wss://fox-cdp-ab.apps.example/playwright/default",
                                   "headers": {"Authorization": "Bearer llt_fox"}, "browser": "firefox", "version": "1.62"},
                    "api": {"url": "https://fox-cdp-ab.apps.example", "headers": {"Authorization": "Bearer llt_fox"}},
                    "how": "This browser runs Camoufox (Firefox): drive it with Playwright 1.62 ..."}


class FakeAPI:
    """Answers `routes` ("METHOD path" -> (status, payload), or a function of
    the request headers giving one), 202 {} for any
    other write and 200 {} for any other read; records (method, path,
    headers, body) of every call."""

    def __init__(self):
        self.calls, self.routes = [], {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_any(self):
                n = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(n) if n else b""
                fake.calls.append((self.command, self.path, dict(self.headers), json.loads(raw) if raw else None))
                route = fake.routes.get(f"{self.command} {self.path}",
                                        (200 if self.command == "GET" else 202, {}))
                status, payload = route(self.headers) if callable(route) else route
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def workspace(self, *workloads):
        self.routes["GET /v1/workspace"] = (200, {"spec": {"workloads": list(workloads)}})


DEFAULTS = {"json": None, "type": None, "join": None, "yes": True, "engine": None, "id": None, "tool": None,
            "screen_width": None, "format": None, "env": False, "action": None, "name": None, "browser": None,
            "browsers": None, "all": False, "remote": None, "host": None, "region": None}


class EngineTest(unittest.TestCase):
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
        buf, problem = io.StringIO(), None
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                fn(types.SimpleNamespace(**{**DEFAULTS, **kw}))
            except llc.Problem as p:
                problem = p
        return buf.getvalue(), problem

    def run_json(self, fn, **kw):
        text, problem = self.run_llc(fn, **kw)
        return (json.loads(text) if text.strip() else None), problem

    def file(self, name, value):
        p = self.tmp / name
        p.write_text(json.dumps(value))
        return str(p)

    def posts(self):
        return [(c[1], c[3]) for c in self.fake.calls if c[0] == "POST"]

    # --- refusals ---

    def test_the_engine_refusals_say_what_to_do(self):
        # Word for word as the engine contract gives them; the code alone, and
        # the message alone, are each enough.
        cases = [
            (422, "engine_unavailable", "This platform doesn't offer Camoufox browsers.", "llc.py engines", llc.EXIT_USER),
            (422, "engine_fixed", "A browser's engine can't change after creation — make a new browser "
                                  "(its cookies can be imported into it).", "leave engine out", llc.EXIT_OTHER),
            (422, "extensions_unsupported", "Camoufox browsers take no extensions yet.", "leave extensions out", llc.EXIT_OTHER),
            # the profile copy, as the platform words it
            (422, "engine_mismatch", "shop runs Chrome and fox runs Camoufox: profiles move only between browsers of one "
                                     "engine — import its cookies instead.", "llc.py cookies", llc.EXIT_OTHER),
            (422, "profile_engine", "This profile is from a Chrome browser; this browser runs Camoufox. Profiles move only "
                                    "between browsers of one engine — import its cookies instead.", "llc.py cookies", llc.EXIT_OTHER),
            (422, "not_livellm_profile", "Only profiles exported from LiveLLM Camoufox browsers can be imported into this "
                                         "browser. Import cookies instead.", "LiveLLM Camoufox browser", llc.EXIT_OTHER),
            (409, "profile_newer", "This profile is from Camoufox 157.0.1-beta.40; this browser runs 156.0.1-beta.34. "
                                   "Import anyway?", "newer Camoufox", llc.EXIT_USER),
        ]
        for status, code, message, words, exit_code in cases:
            for payload in ({"error": message, "code": code}, {"error": message}):
                problem = llc.status_problem(status, payload)
                self.assertIn(words, problem.next, payload)
                self.assertEqual(problem.code, exit_code, payload)
            if code not in ("not_livellm_profile", "profile_newer"):
                problem = llc.status_problem(status, {"error": "refused", "code": code})
                self.assertIn(words, problem.next, code)
        # engine_mismatch is the profile copy only: never advice about a Browser API's engine
        problem = llc.status_problem(422, {"error": "refused", "code": "engine_mismatch"})
        self.assertNotIn("Browser API", problem.next)
        # the pool wordings of before mean nothing now: the plain answer
        for message in ("Browser API scrapers drives Camoufox browsers; agent-1 runs Chrome",
                        "Remote browsers go only in a Chrome Browser API"):
            self.assertEqual(llc.status_problem(422, {"error": message}).next,
                             "fix the field the message names; do not retry unchanged", message)

    def test_a_database_engine_refusal_is_not_a_browser_one(self):
        # tenant-api's own refusal for a database (no code): the plain 422 answer, as before engines
        problem = llc.status_problem(422, {"error": "workloads[0] (pg): a database's engine can't change after creation"})
        self.assertEqual(problem.next, "fix the field the message names; do not retry unchanged")
        self.assertEqual(problem.code, llc.EXIT_OTHER)

    def test_chrome_refusals_answer_as_before(self):
        newer = llc.status_problem(409, {"error": "This profile is from Chrome 155; this browser runs 154. Import anyway?",
                                         "code": "profile_newer"})
        self.assertEqual(newer.next, "the profile comes from a newer Chrome: ask the user, and only if they agree run the "
                                     "same import with --force")
        self.assertEqual(newer.code, llc.EXIT_USER)
        # a LiveLLM from before engines: the plain answer, as before
        other = llc.status_problem(422, {"error": "Only profiles exported from LiveLLM browsers can be imported. "
                                                  "Import cookies instead.", "code": "not_livellm_profile"})
        self.assertEqual(other.next, "fix the field the message names; do not retry unchanged")
        # worded by the browser's engine: a Camoufox file into a Chrome browser is refused so too, and the
        # answer is to move the sign-ins as cookies
        chrome = "Only profiles exported from LiveLLM Chrome browsers can be imported into this browser. Import cookies instead."
        for payload in ({"error": chrome, "code": "not_livellm_profile"}, {"error": chrome}):
            other = llc.status_problem(422, payload)
            self.assertIn("LiveLLM Chrome browser", other.next, payload)
            self.assertIn("llc.py cookies", other.next, payload)
            self.assertEqual(other.code, llc.EXIT_OTHER)
        # a Browser API refusal of today keeps its own answer
        taken = llc.status_problem(422, {"error": "agent-1 is already in Browser API scrapers"})
        self.assertEqual(taken.next, "fix the field the message names; do not retry unchanged")

    # --- create ---

    def test_create_a_chrome_browser_sends_the_file_as_it_is(self):
        body = {"id": "shop", "cpu": "2", "memory": "4Gi", "storage": "4Gi"}
        for engine in (None, "chrome"):
            self.fake.calls.clear()
            res, problem = self.run_json(llc.create, type="browser", json=self.file("b.json", body), engine=engine)
            self.assertIsNone(problem)
            self.assertEqual(res, {"created": "shop", "type": "browser", "next": "llc.py wait shop then llc.py connect shop"})
            self.assertEqual([(c[0], c[1], c[3]) for c in self.fake.calls], [("POST", "/v1/workloads/browser", body)])

    def test_create_a_camoufox_browser_checks_it_was_made_so(self):
        body = {"id": "fox", "cpu": "2", "memory": "4Gi"}
        self.fake.workspace({"id": "fox", "type": "browser", "browser": {"engine": "camoufox"}})
        res, problem = self.run_json(llc.create, type="browser", json=self.file("b.json", body), engine="camoufox")
        self.assertIsNone(problem)
        self.assertEqual(res["engine"], "camoufox")
        self.assertEqual(self.posts(), [("/v1/workloads/browser", {**body, "engine": "camoufox"})])
        # the engine in the file is enough
        self.fake.calls.clear()
        res, problem = self.run_json(llc.create, type="browser", json=self.file("c.json", {**body, "engine": "camoufox"}))
        self.assertIsNone(problem)
        self.assertEqual(self.posts(), [("/v1/workloads/browser", {**body, "engine": "camoufox"})])

    def test_a_platform_without_engines_is_not_taken_for_camoufox(self):
        self.fake.workspace({"id": "fox", "type": "browser", "browser": {}})
        _, problem = self.run_json(llc.create, type="browser", json=self.file("b.json", {"id": "fox"}), engine="camoufox")
        self.assertIsNotNone(problem)
        self.assertIn("not Camoufox", problem.message)
        self.assertIn("only if they want it gone", problem.next)
        self.assertEqual(problem.code, llc.EXIT_USER)

    def test_engine_is_refused_where_it_does_not_belong(self):
        _, problem = self.run_json(llc.create, type="pod", json=self.file("p.json", {"id": "web"}), engine="camoufox")
        self.assertIn("--engine is for a browser", problem.message)
        # a Browser API has no engine
        _, problem = self.run_json(llc.create, type="controller", json=self.file("c.json", {"id": "scrapers"}), engine="camoufox")
        self.assertEqual(problem.message, "--engine is for a browser, not controller")
        _, problem = self.run_json(llc.create, type="browser", json=self.file("b.json", {"id": "b", "engine": "chrome"}),
                                   engine="camoufox")
        self.assertIn("say it once", problem.next)
        self.assertEqual(self.fake.calls, [])

    # --- Browser API ---

    def test_browser_api_create(self):
        res, problem = self.run_json(llc.browser_api, action="create", name="scrapers", browsers="a,b")
        self.assertIsNone(problem)
        self.assertEqual(self.posts(), [("/v1/workloads/controller", {"id": "scrapers", "autodiscover": False, "browsers": ["a", "b"]})])
        self.assertEqual(res, {"created": "scrapers", "type": "controller",
                               "next": "llc.py wait scrapers then llc.py connect scrapers --tool api"})
        # every browser in the workspace, whatever its engine, and remote browsers beside them
        self.fake.calls.clear()
        res, problem = self.run_json(llc.browser_api, action="create", name="all", all=True,
                                     remote=["office=wss://office.example/cdp"])
        self.assertIsNone(problem)
        self.assertEqual(self.posts(), [("/v1/workloads/controller", {
            "id": "all", "autodiscover": True, "externalBrowsers": [{"id": "office", "wsUrl": "wss://office.example/cdp"}]})])
        self.assertNotIn("engine", res)

    def test_browser_api_create_takes_no_engine(self):
        # refused by the command line itself, before anything is sent
        for argv in (["browser-api", "create", "foxes", "--all", "--engine", "camoufox", "--yes"],
                     ["browser-api", "create", "foxes", "--all", "--engine", "chrome", "--yes"]):
            err = io.StringIO()
            with mock.patch.object(sys, "argv", ["llc.py", *argv]), contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as done:
                llc.main()
            self.assertEqual(done.exception.code, 2, argv)
            self.assertIn("--engine", err.getvalue())
        self.assertEqual(self.fake.calls, [])

    def test_browser_api_show_holds_both_engines(self):
        # "old-foxes" still carries the engine a Browser API once had: it is
        # ignored, so the Browser API drives every browser, of any engine.
        self.fake.workspace({"id": "shop", "type": "browser", "browser": {}},
                            {"id": "fox", "type": "browser", "browser": {"engine": "camoufox"}},
                            {"id": "scrapers", "type": "controller", "controller": {"autodiscover": True}},
                            {"id": "old-foxes", "type": "controller",
                             "controller": {"autodiscover": True, "engine": "camoufox"}},
                            {"id": "pair", "type": "controller", "controller": {"browsers": ["shop", "fox"]}})
        for name in ("scrapers", "old-foxes"):
            res, _ = self.run_json(llc.browser_api, action="show", name=name)
            self.assertEqual(sorted(res), ["answering", "browsers", "drives", "id", "ready", "remoteBrowsers", "state"],
                             name)
            self.assertEqual(res["drives"], "every browser in the workspace", name)
            self.assertNotIn("Camoufox", json.dumps(res), name)
        res, _ = self.run_json(llc.browser_api, action="show", name="pair")
        self.assertEqual(res["drives"], "only these")
        self.assertEqual(res["browsers"], ["shop", "fox"])
        self.assertNotIn("engine", res)

    # --- ls, engines, cookies ---

    def test_ls_names_only_a_camoufox_engine(self):
        self.fake.workspace({"id": "shop", "type": "browser", "browser": {}},
                            {"id": "fox", "type": "browser", "browser": {"engine": "camoufox"}},
                            {"id": "scrapers", "type": "controller", "controller": {"autodiscover": True}},
                            {"id": "old-foxes", "type": "controller",
                             "controller": {"autodiscover": True, "engine": "camoufox"}})
        res, _ = self.run_json(llc.ls, type=None)
        by_id = {r["id"]: r for r in res["resources"]}
        self.assertNotIn("engine", by_id["shop"])
        self.assertEqual(by_id["fox"]["engine"], "camoufox")
        # a Browser API has no engine, even one that still carries the old field
        self.assertNotIn("engine", by_id["scrapers"])
        self.assertNotIn("engine", by_id["old-foxes"])

    def test_engines_is_one_read_with_the_sign_in_when_there_is_one(self):
        # The key (or sign-in) goes with the read, so the list is the one
        # this account gets; without one, or with one it won't take, the
        # public list.
        answer = {"engines": [{"id": "chrome", "name": "Chrome", "protocol": "cdp", "default": True},
                              {"id": "camoufox", "name": "Camoufox", "protocol": "playwright", "playwright": "1.62"}]}
        self.fake.routes["GET /v1/browsers/engines"] = (200, answer)
        res, problem = self.run_json(llc.engines)
        self.assertIsNone(problem)
        self.assertEqual(res, answer)
        self.assertEqual([c[:2] for c in self.fake.calls], [("GET", "/v1/browsers/engines")])
        self.assertEqual(self.fake.calls[0][2].get("Authorization"), "Bearer llc_test")
        # not signed in: one read without a token
        saved = llc.CREDENTIALS
        self.addCleanup(setattr, llc, "CREDENTIALS", saved)
        llc.API_KEY, llc.CREDENTIALS = "", self.tmp / "no-credentials.json"
        self.fake.calls.clear()
        res, problem = self.run_json(llc.engines)
        self.assertIsNone(problem)
        self.assertEqual(res, answer)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertNotIn("Authorization", self.fake.calls[0][2])
        # a key the list won't take: the public list, once more without it
        llc.API_KEY = "llc_revoked"
        public = {"engines": answer["engines"][:1]}
        self.fake.routes["GET /v1/browsers/engines"] = (
            lambda h: (401, {"error": "invalid API key"}) if h.get("Authorization") else (200, public))
        self.fake.calls.clear()
        res, problem = self.run_json(llc.engines)
        self.assertIsNone(problem)
        self.assertEqual(res, public)
        self.assertEqual([c[2].get("Authorization") for c in self.fake.calls], ["Bearer llc_revoked", None])
        # a LiveLLM from before engines: Chrome only, not "the id is wrong"
        self.fake.routes["GET /v1/browsers/engines"] = (404, {"error": "404 page not found"})
        res, problem = self.run_json(llc.engines)
        self.assertIsNone(problem)
        self.assertEqual([e["id"] for e in res["engines"]], ["chrome"])

    def test_cookies_a_camoufox_browser_left_out(self):
        items = [{"name": "sid", "value": "cookie-secret-77", "domain": ".example.com", "path": "/"},
                 {"name": "x", "value": "cookie-secret-78", "domain": ".example.com", "path": "/", "sameSite": "None"}]
        self.fake.routes["POST /v1/workloads/fox/cookies"] = (200, {"browser": "fox", "added": 1, "dropped": 1})
        res, _ = self.run_json(llc.cookies, id="fox", json=self.file("c.json", items))
        self.assertEqual((res["added"], res["dropped"]), (1, 1))
        self.assertIn("1 of the cookies were not taken", res["note"])
        self.assertNotIn("cookie-secret", json.dumps(res))
        # a Chrome browser's answer is printed as before
        self.fake.routes["POST /v1/workloads/shop/cookies"] = (200, {"browser": "shop", "added": 2})
        res, _ = self.run_json(llc.cookies, id="shop", json=self.file("c.json", items))
        self.assertEqual(res, {"added": 2, "browser": "shop", "to": "shop"})

    # --- connect ---

    def test_connect_env_for_chrome_is_as_before(self):
        self.fake.routes["POST /v1/workloads/shop/connect"] = (200, CHROME_CONNECT)
        text, problem = self.run_llc(llc.connect, id="shop", tool="cdp", env=True)
        self.assertIsNone(problem)
        self.assertEqual(text, 'export LIVELLM_CDP_URL="wss://shop-cdp-ab.apps.example/devtools/browser/default"\n'
                               'export LIVELLM_CONNECT_TOKEN="llt_chrome"\n')

    def test_connect_env_for_camoufox_names_the_playwright_address(self):
        self.fake.routes["POST /v1/workloads/fox/connect"] = (200, CAMOUFOX_CONNECT)
        text, problem = self.run_llc(llc.connect, id="fox", tool="cdp", env=True)
        self.assertIsNone(problem)
        self.assertEqual(text, 'export LIVELLM_PLAYWRIGHT_URL="wss://fox-cdp-ab.apps.example/playwright/default"\n'
                               'export LIVELLM_PLAYWRIGHT_VERSION="1.62"\n'
                               'export LIVELLM_CONNECT_TOKEN="llt_fox"\n')
        res, _ = self.run_json(llc.connect, id="fox", tool="cdp")
        self.assertEqual(res, CAMOUFOX_CONNECT)


# --- the connect examples -----------------------------------------------------


class FakePage:
    def __init__(self, log):
        self.log, self.url = log, ""

    def goto(self, url, **kw):
        self.log.append(("goto", url, kw))
        self.url = url

    def title(self):
        return "Example"

    def close(self):
        self.log.append(("page.close",))


class FakeContext:
    def __init__(self, log):
        self.log = log

    def new_page(self):
        self.log.append(("new_page",))
        return FakePage(self.log)


class FakeBrowser:
    def __init__(self, log, contexts):
        self.log, self.contexts = log, contexts

    def new_page(self):
        raise AssertionError("browser.new_page() must not be used")

    def new_context(self, **kw):
        self.log.append(("new_context", kw))
        return FakeContext(self.log)


def fake_playwright(log, version="1.62.0", contexts=True):
    """A stand-in `playwright` package: its sync_playwright() records the
    calls made through it."""
    def browser():
        return FakeBrowser(log, [FakeContext(log)] if contexts else [])

    class BrowserType:
        def __init__(self, name):
            self.name = name

        def connect(self, url, **kw):
            log.append((self.name + ".connect", url, kw))
            return browser()

        def connect_over_cdp(self, url, **kw):
            log.append((self.name + ".connect_over_cdp", url, kw))
            return browser()

    @contextlib.contextmanager
    def sync_playwright():
        yield types.SimpleNamespace(firefox=BrowserType("firefox"), chromium=BrowserType("chromium"))

    pkg = types.ModuleType("playwright")
    pkg.__version__ = version
    sync = types.ModuleType("playwright.sync_api")
    sync.sync_playwright = sync_playwright
    pkg.sync_api = sync
    return {"playwright": pkg, "playwright.sync_api": sync}


class ExampleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = []
        saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.addCleanup(restore)

    def stub(self, **kw):
        sys.modules.update(fake_playwright(self.log, **kw))

    def answer(self, value):
        p = self.tmp / "connect.json"
        p.write_text(json.dumps(value))
        return str(p)

    def run_main(self, script, answer, installed=None):
        mod = load("example_" + script.replace(".", "_"), SKILL / "assets" / script)
        if installed is not None:
            mod.installed_playwright = lambda: installed
        out, err = io.StringIO(), io.StringIO()
        argv = sys.argv
        sys.argv = [script, self.answer(answer), "https://example.com"]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mod.main()
        finally:
            sys.argv = argv
        return code, out.getvalue(), err.getvalue()

    def test_cdp_connect_drives_chrome_as_before(self):
        self.stub()
        code, out, _ = self.run_main("cdp_connect.py", CHROME_CONNECT)
        self.assertEqual(code, 0)
        self.assertEqual(self.log, [
            ("chromium.connect_over_cdp", CHROME_CONNECT["cdp"]["url"], {"headers": CHROME_CONNECT["cdp"]["headers"]}),
            ("new_page",), ("goto", "https://example.com", {"wait_until": "domcontentloaded"}), ("page.close",)])
        self.assertEqual(json.loads(out), {"title": "Example", "url": "https://example.com"})

    def test_cdp_connect_points_a_camoufox_answer_elsewhere(self):
        self.stub()
        code, out, err = self.run_main("cdp_connect.py", CAMOUFOX_CONNECT)
        self.assertEqual(code, 2)
        self.assertIn("This browser runs Camoufox: use assets/playwright_connect.py", err)
        self.assertEqual((self.log, out), ([], ""))

    def test_playwright_connect_drives_camoufox_in_its_own_context(self):
        self.stub()
        code, out, _ = self.run_main("playwright_connect.py", CAMOUFOX_CONNECT, installed="1.62.1")
        self.assertEqual(code, 0)
        pw = CAMOUFOX_CONNECT["playwright"]
        self.assertEqual(self.log, [
            ("firefox.connect", pw["url"], {"headers": pw["headers"]}),
            ("new_page",), ("goto", "https://example.com", {"wait_until": "domcontentloaded"}), ("page.close",)])
        self.assertEqual(json.loads(out), {"title": "Example", "url": "https://example.com"})

    def test_playwright_connect_without_a_context_makes_one_with_no_viewport(self):
        self.stub(contexts=False)
        code, _, _ = self.run_main("playwright_connect.py", CAMOUFOX_CONNECT, installed="1.62.0")
        self.assertEqual(code, 0)
        self.assertIn(("new_context", {"no_viewport": True}), self.log)

    def test_playwright_connect_names_the_pip_line_before_connecting(self):
        self.stub()
        for installed in ("1.63.0", "1.61.2"):
            self.log.clear()
            code, out, err = self.run_main("playwright_connect.py", CAMOUFOX_CONNECT, installed=installed)
            self.assertEqual(code, 2, installed)
            self.assertIn(f"This browser takes Playwright 1.62; {installed} is installed.", err)
            self.assertIn('Run: pip install "playwright==1.62.*"', err)
            self.assertEqual((self.log, out), ([], ""))

    def test_playwright_connect_with_no_playwright_names_the_pip_line(self):
        mod = load("example_none", SKILL / "assets" / "playwright_connect.py")
        mod.installed_playwright = lambda: None
        err = io.StringIO()
        argv = sys.argv
        sys.argv = ["playwright_connect.py", self.answer(CAMOUFOX_CONNECT), "https://example.com"]
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = mod.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 2)
        self.assertIn("none is installed.", err.getvalue())
        self.assertIn('pip install "playwright==1.62.*"', err.getvalue())

    def test_the_installed_version_is_read_from_the_package(self):
        mod = load("example_version", SKILL / "assets" / "playwright_connect.py")
        try:
            from importlib.metadata import version
            version("playwright")
            self.skipTest("a real playwright is installed here")
        except Exception:
            pass
        self.stub(version="1.62.3")
        self.assertEqual(mod.installed_playwright(), "1.62.3")
        self.assertTrue(mod.same_minor("1.62.3", "1.62"))
        self.assertFalse(mod.same_minor("1.63.0", "1.62"))

    def test_playwright_connect_points_a_chrome_answer_elsewhere(self):
        self.stub()
        code, _, err = self.run_main("playwright_connect.py", CHROME_CONNECT, installed="1.62.0")
        self.assertEqual(code, 2)
        self.assertIn("This browser runs Chrome: use assets/cdp_connect.py", err)
        self.assertEqual(self.log, [])


NODE_STUB = """
const log = (...a) => console.error("CALL " + JSON.stringify(a));
const page = { url: () => "https://example.com", title: async () => "Example",
  goto: async (u, o) => log("goto", u, o), close: async () => log("page.close") };
const context = { newPage: async () => { log("newPage"); return page; } };
const browser = { contexts: () => [context], newPage: () => { throw new Error("browser.newPage"); },
  newContext: async (o) => { log("newContext", o); return context; } };
export const firefox = { connect: async (u, o) => { log("firefox.connect", u, o); return browser; } };
export const chromium = { connectOverCDP: async (u, o) => { log("chromium.connectOverCDP", u, o); return browser; } };
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class NodeExampleTest(unittest.TestCase):
    """The .mjs examples against a stand-in playwright package in node_modules."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_node(self, script, answer, version="1.62.1"):
        for name in ("cdp_connect.mjs", "playwright_connect.mjs"):
            shutil.copy(SKILL / "assets" / name, self.tmp / name)
        pkg = self.tmp / "node_modules" / "playwright"
        shutil.rmtree(pkg, True)
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({
            "name": "playwright", "version": version, "type": "module",
            "exports": {".": "./index.js", "./package.json": "./package.json"}}))
        (pkg / "index.js").write_text(NODE_STUB)
        (self.tmp / "connect.json").write_text(json.dumps(answer))
        r = subprocess.run(["node", str(self.tmp / script), str(self.tmp / "connect.json"), "https://example.com"],
                           capture_output=True, text=True, timeout=60, cwd=self.tmp, env={**os.environ, "NODE_PATH": ""})
        calls = [json.loads(line[5:]) for line in r.stderr.splitlines() if line.startswith("CALL ")]
        return r.returncode, r.stdout, r.stderr, calls

    def test_cdp_connect_mjs_drives_chrome_as_before(self):
        code, out, _, calls = self.run_node("cdp_connect.mjs", CHROME_CONNECT)
        self.assertEqual(calls[0], ["chromium.connectOverCDP", CHROME_CONNECT["cdp"]["url"],
                                    {"headers": CHROME_CONNECT["cdp"]["headers"]}])
        self.assertEqual(json.loads(out), {"title": "Example", "url": "https://example.com"})
        self.assertEqual(code, 0)

    def test_cdp_connect_mjs_points_a_camoufox_answer_elsewhere(self):
        code, _, err, calls = self.run_node("cdp_connect.mjs", CAMOUFOX_CONNECT)
        self.assertEqual(code, 2)
        self.assertIn("This browser runs Camoufox: use assets/playwright_connect.mjs", err)
        self.assertEqual(calls, [])

    def test_playwright_connect_mjs(self):
        code, out, _, calls = self.run_node("playwright_connect.mjs", CAMOUFOX_CONNECT)
        pw = CAMOUFOX_CONNECT["playwright"]
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ["firefox.connect", pw["url"], {"headers": pw["headers"]}])
        self.assertEqual(calls[1], ["newPage"])
        self.assertEqual(json.loads(out), {"title": "Example", "url": "https://example.com"})

    def test_playwright_connect_mjs_names_the_npm_line_before_connecting(self):
        code, _, err, calls = self.run_node("playwright_connect.mjs", CAMOUFOX_CONNECT, version="1.63.0")
        self.assertEqual(code, 2)
        self.assertIn("This browser takes Playwright 1.62; 1.63.0 is installed.", err)
        self.assertIn("Run: npm i playwright@1.62", err)
        self.assertEqual(calls, [])

    def test_playwright_connect_mjs_without_playwright_says_where_it_looked(self):
        for name in ("playwright_connect.mjs",):
            shutil.copy(SKILL / "assets" / name, self.tmp / name)
        (self.tmp / "connect.json").write_text(json.dumps(CAMOUFOX_CONNECT))
        r = subprocess.run(["node", str(self.tmp / "playwright_connect.mjs"), str(self.tmp / "connect.json"), "https://example.com"],
                           capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("none is installed where this script can see it.", r.stderr)
        self.assertIn("Run: npm i playwright@1.62 in this script's folder", r.stderr)

    def test_playwright_connect_mjs_points_a_chrome_answer_elsewhere(self):
        code, _, err, calls = self.run_node("playwright_connect.mjs", CHROME_CONNECT)
        self.assertEqual(code, 2)
        self.assertIn("This browser runs Chrome: use assets/cdp_connect.mjs", err)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
