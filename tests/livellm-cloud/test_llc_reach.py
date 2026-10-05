"""Who may reach a resource inside the workspace, with scripts/llc.py, against a
stand-in for LiveLLM's API that records every call; and the refusals of the
Network permission.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import contextlib
import importlib.util
import io
import json
import os
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

# A workspace as the API answers it. `old` was made before the setting existed.
WORKLOADS = [
    {"id": "web", "type": "pod", "reachableFrom": [], "pod": {"databases": [{"id": "db", "env": {"DATABASE_URL": "url"}}]}},
    {"id": "worker", "type": "pod", "reachableFrom": ["web"], "pod": {"dependsOn": ["db"]}},
    {"id": "db", "type": "storage", "reachableFrom": ["web"], "storage": {"engine": "postgres"}},
    {"id": "old", "type": "vm-ubuntu"},
    {"id": "shop-web", "type": "pod", "reachableFrom": ["*"], "pod": {"stack": "shop"}},
    {"id": "shop-api", "type": "pod", "reachableFrom": ["*"], "pod": {"stack": "shop"}},
    {"id": "b1", "type": "browser", "reachableFrom": []},
    {"id": "pool", "type": "controller", "reachableFrom": ["old"], "controller": {"browsers": ["b1"]}},
]

INSIDE = {"reachableFrom": ["web"], "alsoFrom": [{"id": "worker", "why": "waits for it"}],
          "addresses": [{"host": "acme-db-rw", "port": 5432}]}


class FakeAPI:
    """GET /v1/workspace answers WORKLOADS; `routes` ("METHOD /path" →
    (status, body)) answers the rest, anything else 202 {}. Records every call."""

    def __init__(self, routes=None, workloads=None):
        self.routes, self.calls = dict(routes or {}), []
        self.workloads = WORKLOADS if workloads is None else workloads
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n)) if n else None
                key = f"{self.command} {self.path}"
                fake.calls.append((self.command, self.path, body))
                if key in fake.routes:
                    status, payload = fake.routes[key]
                elif key == "GET /v1/workspace":
                    status, payload = 200, {"spec": {"workloads": fake.workloads}}
                elif key == "GET /v1/status":
                    status, payload = 200, {"workloads": []}
                else:
                    status, payload = 202, {}
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def writes(self):
        return [c for c in self.calls if c[0] != "GET" and not c[1].endswith("/connect")]


def reach_args(**kw):
    base = {"id": "db", "source": None, "none": False, "add": None, "remove": None, "yes": False}
    base.update(kw)
    return base


class ReachTest(unittest.TestCase):
    def use(self, routes=None, workloads=None):
        fake = FakeAPI(routes, workloads)
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
        return (json.loads(text) if text.strip() else None), problem, text

    def file(self, value):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(value, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    # --- reading ---

    def test_show_prints_the_connect_answers_inside_block_and_never_its_token(self):
        self.use({"POST /v1/workloads/db/connect": (200, {"type": "storage", "token": "llt_secret-42", "inside": INSIDE})})
        out, problem, text = self.run_llc(llc.reach, **reach_args())
        self.assertIsNone(problem)
        self.assertEqual(out, {"id": "db", "type": "storage", **INSIDE})
        self.assertNotIn("llt_secret-42", text)

    def test_show_against_an_api_without_inside_reads_the_settings(self):
        # tenant-api 0.48: connect has no inside block, and no resource has the setting
        bare = [{k: v for k, v in w.items() if k != "reachableFrom"} for w in WORKLOADS]
        self.use({"POST /v1/workloads/db/connect": (200, {"type": "storage", "token": "llt_x"})}, workloads=bare)
        out, problem, text = self.run_llc(llc.reach, **reach_args())
        self.assertIsNone(problem)
        self.assertEqual(out["reachableFrom"], ["*"])
        self.assertEqual(out["alsoFrom"], [{"id": "web", "why": "links it"}, {"id": "worker", "why": "waits for it"}])
        self.assertIn("llc.py connect db", out["note"])
        self.assertNotIn("llt_x", text)

    def test_show_names_same_app_and_the_browser_api_with_what_reaches_it(self):
        self.use()
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="b1"))
        self.assertEqual(out["reachableFrom"], [])
        self.assertEqual(out["alsoFrom"], [{"id": "pool", "why": "drives it", "through": ["old"]}])
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="shop-web"))
        self.assertEqual(out["alsoFrom"], [{"id": "shop-api", "why": "same app"}])

    def test_show_falls_back_when_connect_is_refused(self):
        self.use({"POST /v1/workloads/db/connect": (403, {"error": "This agent can't connect to db."})})
        out, problem, _ = self.run_llc(llc.reach, **reach_args())
        self.assertIsNone(problem)
        self.assertEqual(out["reachableFrom"], ["web"])

    def test_an_unknown_id_is_refused_before_anything_is_sent(self):
        fake = self.use()
        _, problem, _ = self.run_llc(llc.reach, **reach_args(id="nope", none=True, yes=True))
        self.assertIn("no resource nope", problem.message)
        self.assertEqual(fake.writes(), [])

    # --- changing ---

    def test_every_change_needs_yes_and_says_ask_the_user(self):
        fake = self.use()
        for kw in ({"source": "web"}, {"none": True}, {"add": "worker"}, {"remove": "web"}, {"source": "*"}):
            _, problem, _ = self.run_llc(llc.reach, **reach_args(**kw))
            self.assertIsNotNone(problem, kw)
            self.assertIn("ask the user", problem.next, kw)
            self.assertIn("--yes", problem.next, kw)
        self.assertEqual(fake.writes(), [])

    def test_from_none_add_remove_patch_only_the_setting(self):
        fake = self.use()
        cases = [
            ({"source": "web,worker"}, ["web", "worker"]),
            ({"source": "*"}, ["*"]),
            ({"none": True}, []),
            ({"add": "worker"}, ["web", "worker"]),
            ({"remove": "web"}, []),
            ({"add": "worker,old", "remove": "web"}, ["worker", "old"]),
        ]
        for kw, want in cases:
            fake.calls.clear()
            out, problem, _ = self.run_llc(llc.reach, **reach_args(yes=True, **kw))
            self.assertIsNone(problem, kw)
            self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/db", {"reachableFrom": want})], kw)
            self.assertEqual((out["reachableFrom"], out["before"]), (want, ["web"]), kw)

    def test_none_sends_an_empty_list_never_null(self):
        fake = self.use()
        self.run_llc(llc.reach, **reach_args(none=True, yes=True))
        self.assertEqual(fake.writes()[0][2], {"reachableFrom": []})

    def test_the_whole_workspace_goes_alone(self):
        fake = self.use()
        for kw in ({"source": "*,web"}, {"add": "*"}):
            _, problem, _ = self.run_llc(llc.reach, **reach_args(yes=True, **kw))
            self.assertIsNotNone(problem, kw)
            self.assertIn('"*" (the whole workspace) goes alone', problem.message, kw)
        _, problem, _ = self.run_llc(llc.reach, **reach_args(source="web,web", yes=True))
        self.assertIn("twice", problem.message)
        self.assertEqual(fake.writes(), [])

    def test_ways_of_saying_it_are_one_or_the_other(self):
        fake = self.use()
        for kw in ({"source": "web", "none": True}, {"source": "web", "add": "x"}, {"none": True, "remove": "web"}, {"source": ""}):
            _, problem, _ = self.run_llc(llc.reach, **reach_args(yes=True, **kw))
            self.assertIsNotNone(problem, kw)
        self.assertEqual(fake.writes(), [])

    def test_a_resource_open_to_everyone_takes_no_add_and_no_remove(self):
        fake = self.use()
        out, problem, _ = self.run_llc(llc.reach, **reach_args(id="old", add="web", yes=True))
        self.assertIsNone(problem)
        self.assertEqual(out["already"], "the whole workspace reaches it")
        _, problem, _ = self.run_llc(llc.reach, **reach_args(id="shop-web", remove="web", yes=True))
        self.assertIn("--from", problem.next)
        self.assertEqual(fake.writes(), [])
        # an unset one (made before the setting) counts as the whole workspace
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="old", none=True, yes=True))
        self.assertEqual(out["before"], ["*"])
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/old", {"reachableFrom": []})])

    def test_nothing_changes_sends_nothing(self):
        fake = self.use()
        out, _, _ = self.run_llc(llc.reach, **reach_args(source="web", yes=True))
        self.assertEqual(out["already"], "nothing changes")
        out, _, _ = self.run_llc(llc.reach, **reach_args(add="web", yes=True))
        self.assertEqual(out["already"], "nothing changes")
        self.assertEqual(fake.writes(), [])

    def test_a_composable_app_says_every_service_takes_it(self):
        self.use()
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="shop-api", source="web", yes=True))
        self.assertIn("every service of the Composable App shop", out["note"])

    def test_a_refused_change_asks_the_user_for_network(self):
        refusal = {"error": "This agent can't let web reach db inside the workspace. A person can turn on Network for it on the Agents page.",
                   "code": "network_permission"}
        self.use({"PATCH /v1/workloads/db": (403, refusal)})
        _, problem, _ = self.run_llc(llc.reach, **reach_args(add="worker", yes=True))
        self.assertEqual((problem.status, problem.code), (403, llc.EXIT_USER))
        self.assertIn("ask the user", problem.next)
        self.assertIn("Network", problem.next)

    # --- create and set keep it ---

    def test_create_keeps_reachable_from_as_written(self):
        fake = self.use()
        body = {"id": "cache", "engine": "redis", "reachableFrom": ["web"]}
        _, problem, _ = self.run_llc(llc.create, type="storage", json=self.file(body), yes=True, join=None)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/storage", body)])
        fake.calls.clear()
        body = {"id": "web2", "image": "nginx", "reachableFrom": []}
        self.run_llc(llc.create, type="pod", json=self.file(body), yes=True, join=None)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/pod", body)])

    def test_create_apps_keeps_it_on_every_app_and_database(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["a"], "databases": ["a-db"]})})
        body = {"apps": [{"id": "a", "image": "x", "reachableFrom": ["*"], "databases": [{"id": "a-db", "env": {"U": "url"}}]}],
                "databases": [{"id": "a-db", "engine": "postgres", "reachableFrom": ["worker"]}]}
        _, problem, _ = self.run_llc(llc.create, type="apps", json=self.file(body), yes=True, join=None)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", body)])
        fake.calls.clear()
        apps = [{"id": "b", "reachableFrom": ["a"]}, {"id": "c"}]
        self.run_llc(llc.create, type="apps", json=self.file(apps), yes=True, join=None)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", {"apps": apps})])

    def test_create_and_set_refuse_a_malformed_setting_before_sending(self):
        fake = self.use()
        for value in (["*", "web"], "web", [""], [1], ["web", "web"]):
            _, problem, _ = self.run_llc(llc.create, type="pod", json=self.file({"id": "x", "reachableFrom": value}),
                                         yes=True, join=None)
            self.assertIsNotNone(problem, value)
            _, problem, _ = self.run_llc(llc.create, type="apps", json=self.file({"apps": [{"id": "x"}], "databases": [
                {"id": "d", "reachableFrom": value}]}), yes=True, join=None)
            self.assertIsNotNone(problem, value)
            _, problem, _ = self.run_llc(llc.set_settings, id="x", json=self.file({"reachableFrom": value}), yes=True)
            self.assertIsNotNone(problem, value)
        self.assertEqual(fake.writes(), [])
        # null in a set closes it, like []
        _, problem, _ = self.run_llc(llc.set_settings, id="x", json=self.file({"reachableFrom": None}), yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/x", {"reachableFrom": None})])

    def test_ls_shows_the_setting_where_there_is_one(self):
        self.use()
        out, _, _ = self.run_llc(llc.ls, type=None)
        by = {r["id"]: r for r in out["resources"]}
        self.assertEqual(by["db"]["reachableFrom"], ["web"])
        self.assertEqual(by["b1"]["reachableFrom"], [])
        self.assertNotIn("reachableFrom", by["old"])

    # --- the Network refusal, in both wordings ---

    def test_network_refusals_say_ask_the_user_and_where_a_person_turns_it_on(self):
        cases = [
            {"error": "This API key can't let web reach nextcloud-db inside the workspace. "
                      "A person can turn on Network for it on the Keys page.", "code": "network_permission"},
            {"error": "This agent can't let web reach the whole workspace inside the workspace. "
                      "A person can turn on Network for it on the Agents page.", "code": "network_permission"},
            # no code: known by its words
            {"error": "This agent can't let web reach db, worker, cache and 2 more inside the workspace. "
                      "A person can turn on Network for it on the Agents page."},
            {"error": "x", "code": "network_permission"},
        ]
        for payload in cases:
            p = llc.status_problem(403, payload)
            self.assertEqual(p.code, llc.EXIT_USER, payload)
            self.assertIn("ask the user", p.next, payload)
            self.assertIn("Keys page", p.next, payload)
            self.assertIn("Agents page", p.next, payload)
            self.assertIn("Never work around it", p.next, payload)

    def test_other_refusals_are_not_taken_for_network(self):
        for status, payload in ((403, {"error": "This agent can add services only to an app it created. "
                                                 "A person can allow more on the Agents page."}),
                                (403, {"error": "This agent can't change db."}),
                                (422, {"error": 'reachableFrom: there is no resource "x" in this workspace'})):
            p = llc.status_problem(status, payload)
            self.assertNotIn("Network permission", p.next, payload)


if __name__ == "__main__":
    unittest.main()
