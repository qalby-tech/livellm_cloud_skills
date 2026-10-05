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

# A workspace as the API answers it. `old` was made before the setting existed;
# a database never has one: what links it reaches it.
WORKLOADS = [
    {"id": "web", "type": "pod", "reachableFrom": [], "pod": {"databases": [{"id": "db", "env": {"DATABASE_URL": "url"}}]}},
    {"id": "worker", "type": "pod", "reachableFrom": ["web"], "pod": {"dependsOn": ["db"]}},
    {"id": "db", "type": "storage", "storage": {"engine": "postgres"}},
    {"id": "api", "type": "pod", "reachableFrom": ["web"], "pod": {"ports": [{"port": 8080}]}},
    {"id": "old", "type": "vm-ubuntu"},
    {"id": "shop-web", "type": "pod", "reachableFrom": ["*"], "pod": {"stack": "shop"}},
    {"id": "shop-api", "type": "pod", "reachableFrom": ["*"], "pod": {"stack": "shop"}},
    {"id": "b1", "type": "browser", "reachableFrom": []},
    {"id": "pool", "type": "controller", "reachableFrom": ["old"], "controller": {"browsers": ["b1"]}},
]


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
                    status, payload = 200, {"name": "acme", "spec": {"workloads": fake.workloads}}
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
        return [c for c in self.calls if c[0] != "GET"]


def reach_args(**kw):
    base = {"id": "api", "source": None, "none": False, "add": None, "remove": None, "yes": False}
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

    def test_show_reads_the_workspace_only_and_never_connects(self):
        # connect holds a machine, makes a screen link or a token and writes an
        # activity line: the view sends nothing but GETs, for every type
        fake = self.use()
        for wid in ("db", "old", "b1", "pool", "web"):
            _, problem, _ = self.run_llc(llc.reach, **reach_args(id=wid))
            self.assertIsNone(problem, wid)
        self.assertEqual(fake.writes(), [])
        self.assertEqual({c[1] for c in fake.calls}, {"/v1/workspace"})

    def test_show_gives_the_setting_who_else_reaches_it_and_its_inside_addresses(self):
        self.use()
        out, _, _ = self.run_llc(llc.reach, **reach_args())
        self.assertEqual(out, {"id": "api", "type": "pod", "reachableFrom": ["web"], "alsoFrom": [],
                               "addresses": [{"host": "acme-api", "port": 8080}]})
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="pool"))
        self.assertEqual(out["addresses"], [{"host": "acme-pool", "port": 8000}])
        # nothing reaches it: no address to hand out
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="web"))
        self.assertEqual((out["reachableFrom"], out["alsoFrom"]), ([], []))
        self.assertNotIn("addresses", out)

    def test_show_a_resource_with_no_setting_says_so_and_claims_no_value(self):
        # before inside access (api 0.48) no resource has the setting; after the
        # platform's own pass an unset one means nothing, so the view guesses neither
        bare = [{k: v for k, v in w.items() if k != "reachableFrom"} for w in WORKLOADS]
        self.use(workloads=bare)
        out, problem, _ = self.run_llc(llc.reach, **reach_args())
        self.assertIsNone(problem)
        self.assertIsNone(out["reachableFrom"])
        self.assertIn("not set", out["note"])
        self.assertEqual(out["alsoFrom"], [])
        self.assertNotIn("addresses", out)

    def test_show_names_same_app_and_the_browser_api_with_what_reaches_it(self):
        self.use()
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="b1"))
        self.assertEqual(out["reachableFrom"], [])
        self.assertEqual(out["alsoFrom"], [{"id": "pool", "why": "drives it", "through": ["old"]}])
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="shop-web"))
        self.assertEqual(out["alsoFrom"], [{"id": "shop-api", "why": "same app"}])

    def test_a_link_reaches_the_targets_whole_app_from_the_linkers_whole_app(self):
        workloads = [
            {"id": "s-web", "type": "pod", "reachableFrom": [], "pod": {"stack": "s", "ports": [{"port": 80}]}},
            {"id": "s-db", "type": "pod", "reachableFrom": [], "pod": {"stack": "s", "hostname": "db",
                                                                      "ports": [{"port": 5432, "internal": True}]}},
            {"id": "f-a", "type": "pod", "reachableFrom": [], "pod": {"stack": "f", "dependsOn": ["s-db"]}},
            {"id": "f-b", "type": "pod", "reachableFrom": [], "pod": {"stack": "f"}},
            {"id": "lone", "type": "pod", "reachableFrom": [], "pod": {}},
        ]
        self.use(workloads=workloads)
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="s-web"))
        self.assertEqual(out["alsoFrom"], [
            {"id": "s-db", "why": "same app"},
            {"id": "f-a", "why": "waits for it", "app": "f"},
            {"id": "f-b", "why": "waits for it", "app": "f", "via": "f-a"},
        ])
        self.assertEqual(out["addresses"], [{"host": "acme-s-web", "port": 80, "inStack": "s-web:80"}])
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="s-db"))
        self.assertEqual(out["addresses"], [{"host": "acme-s-db", "port": 5432, "inStack": "db:5432"}])

    def test_a_composable_apps_name_shows_and_changes_the_whole_app(self):
        fake = self.use()
        out, problem, _ = self.run_llc(llc.reach, **reach_args(id="shop"))
        self.assertIsNone(problem)
        self.assertEqual((out["app"], out["reachableFrom"]), ("shop", ["*"]))
        out, problem, _ = self.run_llc(llc.reach, **reach_args(id="shop", source="web", yes=True))
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/shop-web", {"reachableFrom": ["web"]})])
        self.assertIn("every service of the Composable App shop", out["note"])

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
            self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/api", {"reachableFrom": want})], kw)
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
        out, problem, _ = self.run_llc(llc.reach, **reach_args(id="shop-web", add="web", yes=True))
        self.assertIsNone(problem)
        self.assertEqual(out["already"], "the whole workspace reaches it")
        _, problem, _ = self.run_llc(llc.reach, **reach_args(id="shop-web", remove="web", yes=True))
        self.assertIn("--from", problem.next)
        self.assertEqual(fake.writes(), [])

    def test_a_resource_with_no_setting_takes_only_a_whole_list(self):
        # unset may mean the whole workspace (before the platform's pass) or
        # nothing (after it): adding to or taking from it would be a guess
        fake = self.use()
        for kw in ({"add": "web"}, {"remove": "web"}):
            _, problem, _ = self.run_llc(llc.reach, **reach_args(id="old", yes=True, **kw))
            self.assertIsNotNone(problem, kw)
            self.assertIn("--from", problem.next, kw)
        self.assertEqual(fake.writes(), [])
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="old", none=True, yes=True))
        self.assertIsNone(out["before"])
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
        self.use({"PATCH /v1/workloads/api": (403, refusal)})
        _, problem, _ = self.run_llc(llc.reach, **reach_args(add="worker", yes=True))
        self.assertEqual((problem.status, problem.code), (403, llc.EXIT_USER))
        self.assertIn("ask the user", problem.next)
        self.assertIn("Network", problem.next)

    # --- a browser put in a Browser API is an opening ---

    def test_browser_api_add_needs_yes_and_says_ask_the_user(self):
        fake = self.use()
        args = dict(action="add", name="pool", browser="b2", browsers=None, all=False, remote=None,
                    host=None, region=None)
        _, problem, _ = self.run_llc(llc.browser_api, **args, yes=False)
        self.assertIn("--yes", problem.message)
        self.assertIn("ask the user and wait for their agreement", problem.next)
        self.assertEqual(fake.writes(), [])
        _, problem, _ = self.run_llc(llc.browser_api, **args, yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("PUT", "/v1/workloads/pool/browsers/b2", {})])

    # --- create and set keep it ---

    def test_create_keeps_reachable_from_as_written(self):
        fake = self.use()
        body = {"id": "b2", "reachableFrom": ["web"]}
        _, problem, _ = self.run_llc(llc.create, type="browser", json=self.file(body), yes=True, join=None, engine=None)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/browser", body)])
        fake.calls.clear()
        body = {"id": "web2", "image": "nginx", "reachableFrom": []}
        self.run_llc(llc.create, type="pod", json=self.file(body), yes=True, join=None)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/pod", body)])

    def test_create_apps_keeps_it_on_every_app(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["a"], "databases": ["a-db"]})})
        body = {"apps": [{"id": "a", "image": "x", "reachableFrom": ["*"], "databases": [{"id": "a-db", "env": {"U": "url"}}]}],
                "databases": [{"id": "a-db", "engine": "postgres"}]}
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
        self.assertEqual(by["api"]["reachableFrom"], ["web"])
        self.assertNotIn("reachableFrom", by["db"])
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

    # --- a database is reached only by what links it ---

    def test_a_database_shows_no_setting_only_what_links_it(self):
        workloads = WORKLOADS + [
            {"id": "runner", "type": "vm-ubuntu", "reachableFrom": [], "vm": {"databases": [{"id": "db"}]}},
            {"id": "desk", "type": "desktop", "reachableFrom": [], "desktop": {"databases": [{"id": "db"}]}},
            {"id": "lone-db", "type": "storage", "storage": {"engine": "redis"}},
        ]
        self.use(workloads=workloads)
        out, problem, _ = self.run_llc(llc.reach, **reach_args(id="db"))
        self.assertIsNone(problem)
        self.assertNotIn("reachableFrom", out)
        self.assertIn("reached only by what links it", out["note"])
        self.assertEqual(out["alsoFrom"], [{"id": "web", "why": "links it"}, {"id": "worker", "why": "waits for it"},
                                           {"id": "runner", "why": "links it"}, {"id": "desk", "why": "links it"}])
        self.assertEqual(out["addresses"], [{"host": "acme-db-rw", "port": 5432}])
        # nothing links it: no address to hand out
        out, _, _ = self.run_llc(llc.reach, **reach_args(id="lone-db"))
        self.assertEqual(out["alsoFrom"], [])
        self.assertNotIn("addresses", out)
        self.assertNotIn("reachableFrom", out)

    def test_a_databases_setting_is_never_changed_and_the_answer_points_at_link(self):
        fake = self.use()
        for kw in ({"source": "web"}, {"source": "*"}, {"none": True}, {"add": "api"}, {"remove": "web"}):
            for yes in (False, True):
                _, problem, _ = self.run_llc(llc.reach, **reach_args(id="db", yes=yes, **kw))
                self.assertIsNotNone(problem, kw)
                self.assertIn("a database is reached only by what links it: add db to the databases of the app, "
                              "machine or Desktop App that uses it", problem.message, kw)
                self.assertIn("llc.py link APP db --yes", problem.next, kw)
        self.assertEqual(fake.writes(), [])

    def test_create_refuses_reachable_from_on_a_database_and_lets_empty_pass(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["a"]})})
        for value in (["web"], ["*"]):
            _, problem, _ = self.run_llc(llc.create, type="storage", json=self.file(
                {"id": "cache", "engine": "redis", "reachableFrom": value}), yes=True, join=None)
            self.assertIn("reached only by what links it", problem.message, value)
            _, problem, _ = self.run_llc(llc.create, type="apps", json=self.file(
                {"apps": [{"id": "a"}], "databases": [{"id": "a-db", "reachableFrom": value}]}), yes=True, join=None)
            self.assertIn("add a-db to the databases", problem.message, value)
        self.assertEqual(fake.writes(), [])
        # [] and null: closed anyway, the API drops them
        for value in ([], None):
            body = {"id": "cache", "engine": "redis", "reachableFrom": value}
            _, problem, _ = self.run_llc(llc.create, type="storage", json=self.file(body), yes=True, join=None)
            self.assertIsNone(problem, value)
        self.assertEqual(len(fake.writes()), 2)

    def test_the_apis_database_refusal_points_at_link(self):
        p = llc.status_problem(422, {"error": "reachableFrom: a database is reached only by what links it: add db to the "
                                              "databases of the app, machine or Desktop App that uses it"})
        self.assertIn("llc.py link", p.next)
        self.assertIn("rule 11", p.next)

    # --- link: reach only, apps, machines and Desktop Apps ---

    LINKED = [
        {"id": "web", "type": "pod", "reachableFrom": [], "pod": {"stack": "shop",
                                                                  "databases": [{"id": "db", "env": {"DATABASE_URL": "url"}}]}},
        {"id": "db", "type": "storage", "storage": {"engine": "postgres"}},
        {"id": "cache", "type": "storage", "storage": {"engine": "redis"}},
        {"id": "runner", "type": "vm-ubuntu", "reachableFrom": []},
        {"id": "desk", "type": "desktop", "reachableFrom": [], "desktop": {"databases": [{"id": "cache"}]}},
        {"id": "b1", "type": "browser", "reachableFrom": []},
    ]

    def link_args(self, id, *dbs, remove=False, yes=True):
        return dict(id=id, databases=list(dbs), remove=remove, yes=yes)

    def test_link_adds_a_reach_only_link_and_keeps_the_others(self):
        fake = self.use(workloads=self.LINKED)
        out, problem, _ = self.run_llc(llc.link, **self.link_args("web", "cache"))
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/web", {"pod": {"databases": [
            {"id": "db", "env": {"DATABASE_URL": "url"}}, {"id": "cache"}]}})])
        self.assertIn("nothing restarts", out["note"])
        self.assertIn("whole Composable App shop", out["note"])

    def test_link_on_a_machine_and_a_desktop_app_goes_in_their_own_block(self):
        fake = self.use(workloads=self.LINKED)
        self.run_llc(llc.link, **self.link_args("runner", "db", "cache"))
        self.run_llc(llc.link, **self.link_args("desk", "db"))
        self.assertEqual(fake.writes(), [
            ("PATCH", "/v1/workloads/runner", {"vm": {"databases": [{"id": "db"}, {"id": "cache"}]}}),
            ("PATCH", "/v1/workloads/desk", {"desktop": {"databases": [{"id": "cache"}, {"id": "db"}]}}),
        ])

    def test_link_needs_yes_and_says_ask_the_user(self):
        fake = self.use(workloads=self.LINKED)
        for remove in (False, True):
            _, problem, _ = self.run_llc(llc.link, **self.link_args("web", "cache", remove=remove, yes=False))
            self.assertIn("--yes", problem.message)
            self.assertIn("ask the user and wait for their agreement", problem.next)
        self.assertEqual(fake.writes(), [])

    def test_link_remove_takes_only_the_named_links_out(self):
        fake = self.use(workloads=self.LINKED)
        out, _, _ = self.run_llc(llc.link, **self.link_args("desk", "cache", remove=True))
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/desk", {"desktop": {"databases": None}})])
        self.assertIn("nothing restarts", out["note"])
        fake.calls.clear()
        # a link with variables: taking it out changes the app's variables, so set does that
        _, problem, _ = self.run_llc(llc.link, **self.link_args("web", "db", remove=True))
        self.assertIn("takes variables from db", problem.message)
        self.assertIn("llc.py set web", problem.next)
        self.assertEqual(fake.writes(), [])

    def test_link_that_changes_nothing_sends_nothing(self):
        fake = self.use(workloads=self.LINKED)
        out, _, _ = self.run_llc(llc.link, **self.link_args("web", "db"))
        self.assertEqual(out["already"], "every one is linked already")
        out, _, _ = self.run_llc(llc.link, **self.link_args("runner", "db", remove=True))
        self.assertEqual(out["already"], "none of them is linked")
        self.assertEqual(fake.writes(), [])

    def test_link_refuses_what_is_no_link_before_sending(self):
        fake = self.use(workloads=self.LINKED)
        cases = [
            (("nope", "db"), "no resource nope"),
            (("shop", "db"), "is a Composable App"),
            (("b1", "db"), "only an app, a machine or a Desktop App links a database"),
            (("db", "cache"), "only an app, a machine or a Desktop App links a database"),
            (("web", "nope"), "no database nope"),
            (("web", "runner"), "not a database"),
        ]
        for ids, said in cases:
            _, problem, _ = self.run_llc(llc.link, **self.link_args(*ids))
            self.assertIsNotNone(problem, ids)
            self.assertIn(said, problem.message, ids)
        _, problem, _ = self.run_llc(llc.link, **self.link_args("web", "runner"))
        self.assertIn("llc.py reach runner --add web", problem.next)
        many = self.LINKED + [{"id": f"d{i}", "type": "storage"} for i in range(8)]
        fake2 = self.use(workloads=many)
        _, problem, _ = self.run_llc(llc.link, **self.link_args("web", *[f"d{i}" for i in range(8)]))
        self.assertIn("at most 8", problem.message)
        self.assertEqual(fake.writes() + fake2.writes(), [])

    def test_a_refused_link_asks_the_user_for_network(self):
        refusal = {"error": "This agent can't let runner reach db inside the workspace. A person can turn on Network for "
                            "it on the Agents page.", "code": "network_permission"}
        self.use({"PATCH /v1/workloads/runner": (403, refusal)}, workloads=self.LINKED)
        _, problem, _ = self.run_llc(llc.link, **self.link_args("runner", "db"))
        self.assertEqual((problem.status, problem.code), (403, llc.EXIT_USER))
        self.assertIn("ask the user", problem.next)

    def test_create_refuses_variables_on_a_machines_or_desktop_apps_link(self):
        fake = self.use()
        for typ, kind in (("vm-ubuntu", "a machine"), ("desktop", "a Desktop App")):
            body = {"id": "m", "databases": [{"id": "db", "env": {"U": "url"}}]}
            _, problem, _ = self.run_llc(llc.create, type=typ, json=self.file(body), yes=True, join=None)
            self.assertIn(f"{kind} gets no variables from a link: it only lets it reach the database", problem.message)
        self.assertEqual(fake.writes(), [])
        body = {"id": "m", "databases": [{"id": "db"}]}
        _, problem, _ = self.run_llc(llc.create, type="vm-ubuntu", json=self.file(body), yes=True, join=None)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/vm-ubuntu", body)])

    def test_ls_shows_the_links_of_machines_and_desktop_apps(self):
        self.use(workloads=self.LINKED + [{"id": "runner2", "type": "vm-windows", "vm": {"databases": [{"id": "db"}]}}])
        out, _, _ = self.run_llc(llc.ls, type=None)
        by = {r["id"]: r for r in out["resources"]}
        self.assertEqual(by["desk"]["databases"], [{"id": "cache"}])
        self.assertEqual(by["runner2"]["databases"], [{"id": "db"}])
        self.assertNotIn("databases", by["runner"])

    def test_a_join_refusal_is_a_network_refusal(self):
        p = llc.status_problem(403, {"error": "This API key can't add worker to Composable App shop inside the workspace. "
                                              "A person can turn on Network for it on the Keys page.",
                                     "code": "network_permission"})
        self.assertEqual(p.code, llc.EXIT_USER)
        self.assertIn("ask the user", p.next)


if __name__ == "__main__":
    unittest.main()
