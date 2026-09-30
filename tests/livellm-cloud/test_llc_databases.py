"""Apps with their databases, templates and deletes with scripts/llc.py, against a
stand-in for LiveLLM's API that records every call.

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

TEMPLATES = {"templates": [
    {"id": "tpl_box", "name": "small box", "kind": "vm-ubuntu", "config": {"vm": {"cpus": 2}}},
    {"id": "tpl_api", "name": "api", "kind": "pod",
     "config": {"pod": {"image": "api", "secretEnv": [{"name": "S", "required": True}]}}},
    {"id": "tpl_shop", "name": "shop", "kind": "stack", "config": {"stack": {"name": "shop", "services": [
        {"name": "web", "id": "shop-web", "pod": {"secretEnv": [{"name": "API_KEY", "required": True}]}},
        {"name": "worker", "id": "shop-worker", "pod": {"secretEnv": [{"name": "API_KEY", "required": True}],
                                                         "imageAuth": {"username": "u", "required": True}}}],
        "databases": [{"name": "db", "id": "shop-db", "storage": {"engine": "postgres"}}]}}},
    {"id": "tpl_solo", "name": "solo", "kind": "stack", "config": {"stack": {"name": "solo", "services": [
        {"name": "solo", "id": "solo", "pod": {"imageAuth": {"username": "u", "required": True}}}]}}},
]}


class FakeAPI:
    """Answers each call from `routes` ("METHOD /path" → (status, body)), GET
    /v1/templates with TEMPLATES, anything else 202 {}; records every call."""

    def __init__(self, routes=None):
        self.routes, self.calls = dict(routes or {}), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, body=None):
                key = f"{self.command} {self.path}"
                fake.calls.append((self.command, self.path, body))
                if key in fake.routes:
                    status, payload = fake.routes[key]
                elif key == "GET /v1/templates":
                    status, payload = 200, TEMPLATES
                else:
                    status, payload = 202, {}
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                self.answer(json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"null"))

            def do_GET(self):
                self.answer()

            def do_DELETE(self):
                self.answer()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class DatabasesAndTemplatesTest(unittest.TestCase):
    def use(self, routes=None):
        fake = FakeAPI(routes)
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

    def file(self, value):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(value, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def use_template(self, ref, new_id, secret=None, secret_env=None, json_file=None):
        return self.run_llc(llc.template, action="use", ref=ref, new_id=new_id, secret=secret,
                            secret_env=secret_env, json=json_file, yes=True, source=None, description=None)

    # --- apps with their databases ---

    def test_create_apps_with_databases(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["shop-web"], "databases": ["shop-db"]})})
        body = {"apps": [{"id": "shop-web", "image": "shop", "databases": [{"id": "shop-db", "env": {"DATABASE_URL": "url"}}]}],
                "databases": [{"id": "shop-db", "engine": "postgres"}]}
        out, problem = self.run_llc(llc.create, type="apps", json=self.file(body), yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", body)])
        self.assertEqual((out["created"], out["databases"]), (["shop-web"], ["shop-db"]))

    def test_create_apps_as_a_list(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["a", "b"]})})
        out, _ = self.run_llc(llc.create, type="apps", json=self.file([{"id": "a"}, {"id": "b"}]), yes=True)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", {"apps": [{"id": "a"}, {"id": "b"}]})])
        self.assertNotIn("databases", out)

    def test_create_apps_onto_an_existing_app(self):
        fake = self.use({"POST /v1/workloads": (202, {"created": ["nextcloud-cache"]})})
        app = {"id": "nextcloud-cache", "hostname": "cache", "image": "redis:7"}
        out, problem = self.run_llc(llc.create, type="apps", json=self.file([app]), join="nextcloud", yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", {"apps": [app], "join": "nextcloud"})])
        # or named in the file
        fake = self.use({"POST /v1/workloads": (202, {"created": ["nextcloud-cache"]})})
        self.run_llc(llc.create, type="apps", json=self.file({"apps": [app], "join": "nextcloud"}), yes=True)
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads", {"apps": [app], "join": "nextcloud"})])

    def test_ls_shows_links(self):
        self.use({
            "GET /v1/workspace": (200, {"spec": {"workloads": [
                {"id": "web", "type": "pod", "pod": {"databases": [{"id": "db", "env": {"DATABASE_URL": "url"}}]}},
                {"id": "db", "type": "storage", "storage": {"credentials": {"username": "shop", "hasUrl": True}}}]}}),
            "GET /v1/status": (200, {"workloads": [{"id": "web", "phase": "Running", "databases": ["db"]},
                                                   {"id": "db", "phase": "Running", "usedBy": ["web"]}]}),
        })
        out, _ = self.run_llc(llc.ls, type=None)
        by = {r["id"]: r for r in out["resources"]}
        self.assertEqual((by["web"]["databases"], by["db"]["usedBy"]), ([{"id": "db", "env": {"DATABASE_URL": "url"}}], ["web"]))
        self.assertNotIn("usedBy", by["web"])
        self.assertEqual(by["db"]["username"], "shop")

    def test_rm_when_the_answer_is_lost(self):
        before = {"spec": {"workloads": [{"id": "web", "type": "pod"},
                                         {"id": "db", "type": "storage", "storage": {"createdWith": ["web"]}},
                                         {"id": "cache", "type": "storage", "storage": {"createdWith": ["web"]}}]}}
        after = {"spec": {"workloads": [{"id": "cache", "type": "storage", "storage": {"createdWith": ["web"]}}]}}
        fake = self.use({"GET /v1/workspace": (200, before)})
        real, done = llc.request, {"it": True}

        def lost(method, path, body=None, token=None, form=False, timeout=llc.TIMEOUT):
            if method == "DELETE":
                self.assertGreaterEqual(timeout, 120)  # never hang up on a delete in progress
                if done["it"]:
                    fake.routes["GET /v1/workspace"] = (200, after)
                raise llc.Problem("cannot reach LiveLLM: timed out", "check the network")
            return real(method, path, body, token, form, timeout)
        llc.request = lost
        self.addCleanup(setattr, llc, "request", real)
        out, problem = self.run_llc(llc.rm, id="web", with_databases=True, force=False, yes=True)
        self.assertIsNone(problem)
        self.assertEqual((out["deleted"], out["databases"]), ("web", {"deleted": ["db"], "kept": ["cache"]}))
        fake.routes["GET /v1/workspace"], done["it"] = (200, before), False
        _, problem = self.run_llc(llc.rm, id="web", with_databases=False, force=False, yes=True)
        self.assertIsNotNone(problem, "still there after a lost answer: the error stands")

    def test_rm_with_databases(self):
        fake = self.use({"DELETE /v1/workloads/web?withDatabases=true": (202, {"databases": {"deleted": ["db"], "kept": []}})})
        out, _ = self.run_llc(llc.rm, id="web", with_databases=True, force=False, yes=True)
        self.assertEqual(fake.writes(), [("DELETE", "/v1/workloads/web?withDatabases=true", None)])
        self.assertEqual(out["databases"], {"deleted": ["db"], "kept": []})
        fake.calls.clear()
        out, _ = self.run_llc(llc.rm, id="web", with_databases=False, force=True, yes=True)
        self.assertEqual(fake.writes(), [("DELETE", "/v1/workloads/web?force=true", None)])
        self.assertEqual(out, {"deleted": "web"})

    # --- templates ---

    def test_save_has_livellm_read_the_resource(self):
        fake = self.use({"POST /v1/templates": (201, {"id": "tpl_new", "kind": "stack"})})
        out, _ = self.run_llc(llc.template, action="save", ref="shop", source="shop-web", description="with its db",
                              new_id=None, secret=None, secret_env=None, json=None, yes=False)
        self.assertEqual(fake.writes(), [("POST", "/v1/templates", {"name": "shop", "from": "shop-web", "description": "with its db"})])
        self.assertEqual(out["kind"], "stack")

    def test_use_a_machine_template(self):
        fake = self.use({"POST /v1/templates/tpl_box/create": (202, {"created": ["box2"]})})
        os.environ["LLC_TEST_PW"] = "p=w"
        self.addCleanup(os.environ.pop, "LLC_TEST_PW")
        out, problem = self.use_template("small box", "box2", secret=["credentials.username=me"],
                                         secret_env=["credentials.password=LLC_TEST_PW"])
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("POST", "/v1/templates/tpl_box/create",
                                          {"id": "box2", "credentials": {"username": "me", "password": "p=w"}})])
        self.assertEqual((out["created"], out["type"]), ("box2", "vm-ubuntu"))

    def test_use_an_app_template(self):
        fake = self.use()
        self.use_template("api", "api2", secret=["S=v", "imagePassword=x", "portPasswords.http.alice=pw"])
        self.assertEqual(fake.writes()[0][2], {"id": "api2", "secretEnv": {"S": "v"}, "imagePassword": "x",
                                               "portPasswords": {"http": {"alice": "pw"}}})

    def test_use_a_composable_app_template(self):
        fake = self.use({"POST /v1/templates/tpl_shop/create": (202, {"created": ["shop2-web", "shop2-worker"], "databases": ["shop2-db"]})})
        secrets = self.file({"services": {"worker": {"imagePassword": "pw"}}})
        out, problem = self.use_template("shop", "shop2", secret=["API_KEY=k"], json_file=secrets)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes()[0][2], {"name": "shop2", "services": {
            "web": {"secretEnv": {"API_KEY": "k"}}, "worker": {"secretEnv": {"API_KEY": "k"}, "imagePassword": "pw"}}})
        self.assertEqual((out["created"], out["databases"]), (["shop2-web", "shop2-worker"], ["shop2-db"]))

    def test_use_a_one_service_template(self):
        fake = self.use()
        self.use_template("solo", "solo2", secret=["imagePassword=pw"])
        self.assertEqual(fake.writes()[0][2], {"name": "solo2", "services": {"solo": {"imagePassword": "pw"}}})

    def test_missing_secrets_say_the_flags(self):
        self.use({"POST /v1/templates/tpl_shop/create": (422, {
            "error": "the template needs these secrets to create from it: services.web.secretEnv.API_KEY",
            "missing": ["services.web.secretEnv.API_KEY", "secretEnv.S"]})})
        _, problem = self.use_template("shop", "shop2")
        self.assertEqual(problem.status, 422)
        self.assertIn("--secret-env services.web.secretEnv.API_KEY=VAR --secret-env S=VAR", problem.next)

    def test_refused_before_anything_is_sent(self):
        fake = self.use()
        cases = {
            "settings in the file": dict(ref="api", json_file=self.file({"env": [{"name": "A", "value": "1"}]})),
            "a service's secrets on an app's template": dict(ref="api", secret=["services.web.secretEnv.S=1"]),
            "top-level secrets in a Composable App's file": dict(ref="shop", json_file=self.file({"secretEnv": {"API_KEY": "k"}})),
            "a secret no service has": dict(ref="shop", secret=["NOPE=1"]),
            "an image password, no service named": dict(ref="shop", secret=["imagePassword=1"]),
            "a secret without a value": dict(ref="api", secret=["S"]),
            "a whole block as one secret": dict(ref="api", secret=["credentials=1"]),
            "a secret from an unset variable": dict(ref="api", secret_env=["S=LLC_TEST_UNSET"]),
        }
        for name, kw in cases.items():
            _, problem = self.use_template(new_id="x", **kw)
            self.assertIsNotNone(problem, name)
        self.assertEqual(fake.writes(), [])


    def test_a_username_with_dots_stays_whole(self):
        pod, solo = TEMPLATES["templates"][1], TEMPLATES["templates"][3]
        for t, path, want in [
            (pod, "portPasswords.http.alice.smith", {"portPasswords": {"http": {"alice.smith": "pw"}}}),
            (solo, "services.solo.portPasswords.http.a@b.com", {"services": {"solo": {"portPasswords": {"http": {"a@b.com": "pw"}}}}}),
            (solo, "portPasswords.http.alice.smith", {"services": {"solo": {"portPasswords": {"http": {"alice.smith": "pw"}}}}}),
        ]:
            body = {}
            llc.put_secret(body, t, path, "pw")
            self.assertEqual(body, want, path)


if __name__ == "__main__":
    unittest.main()
