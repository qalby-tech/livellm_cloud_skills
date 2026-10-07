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

            do_PATCH = do_POST

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

    # --- object storage (engine s3) ---

    def test_ls_names_every_databases_engine(self):
        self.use({
            "GET /v1/workspace": (200, {"spec": {"workloads": [
                {"id": "db", "type": "storage", "storage": {"credentials": {"username": "shop"}}},
                {"id": "pg", "type": "storage", "storage": {"engine": "postgres"}},
                {"id": "cache", "type": "storage", "storage": {"engine": "redis"}},
                {"id": "files", "type": "storage", "storage": {"engine": "s3",
                                                                "credentials": {"username": "filesapp", "password": "s3cr3t-k3y"}}},
                {"id": "web", "type": "pod", "pod": {"databases": [{"id": "files", "env": {"S3_BUCKET": "bucket"}}]}},
                {"id": "b", "type": "browser", "browser": {"engine": "camoufox"}},
                {"id": "c", "type": "browser"}]}}),
            "GET /v1/status": (200, {"workloads": []}),
        })
        out, _ = self.run_llc(llc.ls, type=None)
        by = {r["id"]: r for r in out["resources"]}
        # a database whose settings name no engine is PostgreSQL
        self.assertEqual({i: by[i].get("engine") for i in by},
                         {"db": "postgres", "pg": "postgres", "cache": "redis", "files": "s3", "web": None,
                          "b": "camoufox", "c": None})
        # an object storage's access key shows as its username; the secret key never does
        self.assertEqual(by["files"]["username"], "filesapp")
        self.assertNotIn("s3cr3t-k3y", json.dumps(out))
        self.assertNotIn("password", json.dumps(out))

    def test_object_storage_refusals_say_what_to_do(self):
        cases = {
            "workloads[0] (files): object storage has no backups yet — it keeps one copy of your files": "has no backups",
            "workloads[0] (files): object storage runs as one server — a second copy isn't offered yet": "leave instances out",
            "workloads[0] (files): object storage runs version 1": "leave version out",
            "workloads[0] (files): turning on the admin console needs the secret key in the same save — it signs in with it":
                "access key llc.py ls shows",
            "workloads[0] (db): turning on the admin console needs the password in the same save — it signs in with it":
                "leave username out when it shows none",
            "workloads[1] (web).pod.databases[0].env.FILES_URL: an object storage has no url": "AWS_ACCESS_KEY_ID: accessKey",
            "workloads[1] (web).pod.databases[0].env.BUCKET: a PostgreSQL database has no bucket": "are an object storage's",
            "workloads[1] (web).pod.databases[0].env.R: a Redis database has no region": "are an object storage's",
        }
        for message, want in cases.items():
            p = llc.status_problem(422, {"error": message})
            self.assertIn(want, p.next, message)
            self.assertEqual(p.message, message)
        # the object storage's console hint warns of the restart; a database's never names a secret key
        s3 = llc.status_problem(422, {"error": "workloads[0] (files): turning on the admin console needs the secret key "
                                               "in the same save — it signs in with it"}).next
        self.assertIn("even with the current key", s3)
        pg = llc.status_problem(422, {"error": "workloads[0] (db): turning on the admin console needs the password "
                                               "in the same save — it signs in with it"}).next
        self.assertNotIn("secret key", pg)
        self.assertNotIn("object storage", pg)
        # one made with its apps and a console but no secret key
        p = llc.status_problem(422, {"error": "databases[0] (files): an admin console signs in with the object storage's keys "
                                              "— send credentials.password (the secret key) to have one"})
        self.assertIn("leave adminConsole out", p.next)
        # a new secret key that wasn't stored after the save: send it again, not "platform trouble"
        p = llc.status_problem(502, {"error": "files: the other settings in this save were applied, but the new secret key "
                                              "wasn't stored — send it again"})
        self.assertIn("still uses its old secret key", p.next)
        self.assertNotIn("platform trouble", p.next)
        # the backups routes answer 400 for an object storage
        p = llc.status_problem(400, {"error": "object storage has no backups yet: it keeps one copy of your files"})
        self.assertIn("one copy", p.next)
        self.assertIn("don't back it up", p.next)
        # a database's own field refusal and other 422s keep the plain answer
        for message in ("workloads[1] (web).pod.databases[0].env.D: a Redis database has no database",
                        "workloads[0] (db): a database's disk can grow but never shrink (it is 10Gi)"):
            self.assertIn("fix the field", llc.status_problem(422, {"error": message}).next, message)

    def test_a_livellm_without_object_storage_says_tell_the_user(self):
        # a LiveLLM from before object storage: not "fix the field" (which reads as "change the engine")
        p = llc.status_problem(422, {"error": 'workloads[0].storage.engine "s3" must be postgres or redis'})
        self.assertIn("no object storage", p.next)
        self.assertIn("tell the user", p.next)
        self.assertNotIn("fix the field", p.next)
        self.assertEqual(p.code, llc.EXIT_USER)
        # a mistyped engine on either LiveLLM keeps the plain answer
        for message in ('workloads[0].storage.engine "postgresql" must be postgres or redis',
                        'workloads[0].storage.engine "s4" must be postgres, redis or s3'):
            self.assertIn("fix the field", llc.status_problem(422, {"error": message}).next, message)

    def test_a_file_holding_a_secret_key_is_to_be_deleted(self):
        self.use({"POST /v1/workloads/storage": (202, {})})
        f = self.file({"id": "files", "engine": "s3", "credentials": {"username": "filesapp", "password": "k" * 20}})
        out, problem = self.run_llc(llc.create, type="storage", json=f, yes=True)
        self.assertIsNone(problem)
        self.assertIn(f"delete {f}", out["delete"])
        self.assertNotIn("k" * 20, json.dumps(out))
        f = self.file({"storage": {"adminConsole": True, "credentials": {"username": "filesapp", "password": "k" * 20}}})
        out, problem = self.run_llc(llc.set_settings, id="files", json=f, yes=True)
        self.assertIsNone(problem)
        self.assertIn(f"delete {f}", out["delete"])
        # no secret in the file, no reminder
        for body in ({"storage": {"adminConsole": False}}, {"storage": {"credentials": {"username": "filesapp"}}}):
            out, _ = self.run_llc(llc.set_settings, id="files", json=self.file(body), yes=True)
            self.assertNotIn("delete", out)
        out, _ = self.run_llc(llc.create, type="storage", json=self.file({"id": "files", "engine": "s3"}), yes=True)
        self.assertNotIn("delete", out)

    def test_an_object_storage_has_no_restore_and_nothing_is_sent(self):
        fake = self.use({"GET /v1/workspace": (200, {"spec": {"workloads": [
            {"id": "files", "type": "storage", "storage": {"engine": "s3"}}]}})})
        os.environ["LLC_TEST_NEW_KEY"] = "a-new-secret-key"
        self.addCleanup(os.environ.pop, "LLC_TEST_NEW_KEY", None)
        _, problem = self.run_llc(llc.restore, id="files", backup="b1", as_id="files-2", at=None,
                                  password_env="LLC_TEST_NEW_KEY", host=None, region=None, yes=True)
        self.assertIn("no backups", problem.message)
        self.assertIn("one copy", problem.next)
        self.assertEqual(fake.writes(), [])

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
