"""A machine's devices, time zone and port groups with scripts/llc.py, and a
Browser API's dedicated cores at its create, against a stand-in for
LiveLLM's API that records every call: llc.py devices gives the platform's
list, connect says where a machine's port groups are published, a set never
sends a machine's time zone, and the refusals of a device or a port group
keep the API's words and have the user decide (a device or a published port
is shared with every workspace: the agent never frees one).

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

DEVICES = {"devices": [{"id": "rtx5090", "label": "NVIDIA RTX 5090", "kind": "gpu", "total": 1, "free": 0, "here": ["box"]},
                       {"id": "usb-1", "label": "USB controller", "kind": "usb", "total": 1, "free": 1,
                        "here": []}]}

WORKSPACE = {"spec": {"workloads": [
    {"id": "box", "type": "vm-windows", "vm": {"cpus": 4, "devices": ["rtx5090"], "portGroups": [
        {"name": "stream", "offset": -10000, "ports": [{"port": 47984, "tcp": True}, {"port": 47998, "udp": True},
                                                      {"port": 48010, "tcp": True, "udp": True}]},
        {"name": "ftp", "mirrored": True, "count": 2, "allowCIDRs": ["198.51.100.0/24"],
         "ports": [{"port": 40000, "tcp": True}, {"port": 40001, "tcp": True}]},
        {"name": "new", "mirrored": True, "count": 4}]}},
    {"id": "plain", "type": "vm-ubuntu", "vm": {"cpus": 1}},
]}}
STATUS = {"workloads": [{"id": "box", "phase": "Running", "ready": True, "ssh": "203.0.113.10:30022"},
                        {"id": "plain", "phase": "Running", "ready": True, "ssh": "203.0.113.10:30023"}]}


class FakeAPI:
    """GET /v1/machine-devices answers `answer` with `status`, GET
    /v1/workspace WORKSPACE (500 with `workspace_down`) and GET /v1/status
    STATUS; POST …/connect answers a machine's connect; any other POST,
    PATCH or PUT answers 201 {}; records every call."""

    def __init__(self, status=200, answer=None, workspace_down=False):
        self.calls = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fake.calls.append((self.command, self.path))
                if self.path == "/v1/workspace":
                    self.reply(500, {"error": "down"}) if workspace_down else self.reply(200, WORKSPACE)
                elif self.path == "/v1/status":
                    self.reply(200, STATUS)
                else:
                    self.reply(status, answer if answer is not None else {})

            def write(self):
                n = int(self.headers.get("content-length") or 0)
                fake.calls.append((self.command, self.path, json.loads(self.rfile.read(n) or b"{}")))
                if self.path.endswith("/connect"):
                    self.reply(200, {"type": "vm-windows", "tool": "ssh"})
                else:
                    self.reply(201, {})

            do_POST = do_PATCH = do_PUT = write

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

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


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

    def file(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "changes.json"
        path.write_text(json.dumps(body))
        return str(path)


class DevicesCommand(Stand):

    def test_devices_gives_the_list_as_the_platform_answers(self):
        fake = self.use(answer=DEVICES)
        res, problem = self.run_llc(llc.devices)
        self.assertIsNone(problem)
        self.assertEqual(res, DEVICES)
        self.assertEqual(fake.calls, [("GET", "/v1/machine-devices")])

    def test_a_livellm_without_devices_says_so(self):
        self.use(status=404, answer={"error": "not found"})
        res, problem = self.run_llc(llc.devices)
        self.assertIsNone(res)
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 404))
        self.assertIn("no devices yet", problem.message)
        self.assertIn("leave devices out", problem.next)


class PortGroupsOnConnect(Stand):

    def test_connect_says_where_each_port_is_published(self):
        self.use()
        res, problem = self.run_llc(llc.connect, id="box", tool=None, screen_width=None, format=None, env=False)
        self.assertIsNone(problem)
        self.assertEqual(res["ssh"], {"address": "203.0.113.10:30022", "host": "203.0.113.10", "port": "30022"})
        self.assertEqual(res["portGroups"], [
            {"name": "stream", "offset": -10000, "ports": [
                {"port": 47984, "published": 37984, "address": "203.0.113.10:37984", "tcp": True, "udp": False},
                {"port": 47998, "published": 37998, "address": "203.0.113.10:37998", "tcp": False, "udp": True},
                {"port": 48010, "published": 38010, "address": "203.0.113.10:38010", "tcp": True, "udp": True}]},
            {"name": "ftp", "offset": 0, "mirrored": True, "allowCIDRs": ["198.51.100.0/24"], "ports": [
                {"port": 40000, "published": 40000, "address": "203.0.113.10:40000", "tcp": True, "udp": False},
                {"port": 40001, "published": 40001, "address": "203.0.113.10:40001", "tcp": True, "udp": False}]},
            {"name": "new", "offset": 0, "mirrored": True, "ports": []},
        ])

    def test_a_machine_without_groups_has_none_and_an_unread_workspace_costs_nothing(self):
        self.use()
        res, problem = self.run_llc(llc.connect, id="plain", tool=None, screen_width=None, format=None, env=False)
        self.assertIsNone(problem)
        self.assertNotIn("portGroups", res)
        self.use(workspace_down=True)
        res, problem = self.run_llc(llc.connect, id="box", tool=None, screen_width=None, format=None, env=False)
        self.assertIsNone(problem)
        self.assertIn("ssh", res)
        self.assertNotIn("portGroups", res)

    def test_no_address_yet_gives_the_published_numbers_alone(self):
        groups = llc.group_addresses({"portGroups": [{"name": "g", "offset": 5, "ports": [{"port": 10}]}]}, "")
        self.assertEqual(groups, [{"name": "g", "offset": 5, "ports": [
            {"port": 10, "published": 15, "tcp": True, "udp": False}]}])
        self.assertEqual(llc.group_addresses({}, "203.0.113.10"), [])
        self.assertEqual(llc.group_addresses(None, "203.0.113.10"), [])


class MachineTimeZone(Stand):

    def test_a_set_never_sends_a_machines_time_zone(self):
        fake = self.use()
        res, problem = self.run_llc(llc.set_settings, id="box", json=self.file({"vm": {"timeZone": "UTC", "cpus": 2}}),
                                    yes=True)
        self.assertIsNone(res)
        self.assertIn("set when it is made", problem.message)
        self.assertIn("leave timeZone out", problem.next)
        self.assertIn("timedatectl", problem.next)
        self.assertEqual(fake.writes(), [])

    def test_a_browsers_time_zone_is_its_own(self):
        fake = self.use()
        res, problem = self.run_llc(llc.set_settings, id="shop", json=self.file({"browser": {"timezone": "UTC"}}),
                                    yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.writes(), [("PATCH", "/v1/workloads/shop", {"browser": {"timezone": "UTC"}})])


class Refusals(unittest.TestCase):

    def test_a_device_not_offered_tells_the_user_and_stops(self):
        p = llc.http_problem(403, {"error": "NVIDIA RTX 5090 isn't offered to this workspace."})
        self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 403, "NVIDIA RTX 5090 isn't offered to this workspace."))
        self.assertIn("no permission changes that", p.next)
        self.assertIn("tell the user, and stop", p.next)
        self.assertNotIn("Agents page", p.next)

    def test_a_device_taken_is_never_freed(self):
        for msg in ("NVIDIA RTX 5090 is in use by another machine (1 in the cluster).",
                    "NVIDIA RTX 5090: no node of this cluster has one."):
            p = llc.http_problem(409, {"error": msg})
            self.assertEqual((p.code, p.message), (llc.EXIT_USER, msg))
            self.assertIn("llc.py devices", p.next)
            self.assertIn("never take it off another machine", p.next)
            self.assertNotIn("retry", p.next)

    def test_a_group_that_cant_be_published_has_the_user_decide(self):
        for status, msg in (
                (409, "box: no free place on this cluster for group stream's 7 ports"),
                (409, "box: no 4 free ports in a row are left on this cluster for group ftp"),
                (409, "box: port 47984 of group stream would be published at 37984, which is in use; the group's offset "
                      "stays -10000 (remove the group and add it again for a new one)"),
                (409, "box: group ftp can't grow: port 40002, the next one, is taken; remove the group and add it again "
                      "for a new range"),
                (409, "box: group ftp's port 40000 is held by something else now"),
                (422, "box: group stream spans 40000 numbers; the cluster publishes ports in 30000-48100")):
            p = llc.http_problem(status, {"error": msg})
            self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, status, msg))
            self.assertIn("tell the user", p.next)
            self.assertIn("only with their agreement", p.next)
            self.assertNotIn("retry", p.next)

    def test_a_port_taken_just_now_is_sent_again(self):
        p = llc.http_problem(409, {"error": "port 37984 was taken just now by another save: save again"})
        self.assertIn("retry once", p.next)

    def test_past_the_ports_a_machine_publishes(self):
        msg = "box would publish 70 ports; a machine publishes at most 64 (SSH and its groups included)"
        p = llc.http_problem(422, {"error": msg})
        self.assertEqual(p.message, msg)
        self.assertIn("ask the user which to leave out", p.next)

    def test_a_livellm_without_port_groups(self):
        p = llc.http_problem(503, {"error": "port groups aren't available on this install"})
        self.assertIn("leave portGroups out", p.next)
        self.assertNotIn("retry", p.next)


class BrowserAPICores(Stand):

    def test_create_takes_dedicated_cores(self):
        fake = self.use()
        res, problem = self.run_llc(llc.browser_api, action="create", name="pool", browser=None, browsers="b1,b2", all=False,
                                    remote=None, host=None, region=None, yes=True, dedicated_cores=True)
        self.assertIsNone(problem)
        self.assertEqual(res["created"], "pool")
        self.assertEqual(fake.writes(), [("POST", "/v1/workloads/controller",
                                          {"id": "pool", "autodiscover": False, "browsers": ["b1", "b2"], "dedicatedCores": True})])

    def test_shared_unless_asked_and_only_at_create(self):
        fake = self.use()
        self.run_llc(llc.browser_api, action="create", name="pool", browser=None, browsers="b1", all=False, remote=None,
                     host=None, region=None, yes=True, dedicated_cores=False)
        self.assertNotIn("dedicatedCores", fake.writes()[0][2])
        fake = self.use()
        res, problem = self.run_llc(llc.browser_api, action="add", name="pool", browser="b3", browsers=None, all=False,
                                    remote=None, host=None, region=None, yes=True, dedicated_cores=True)
        self.assertIn("--dedicated-cores is for create", problem.message)
        self.assertIn('"controller": {"dedicatedCores": true}', problem.next)
        self.assertEqual(fake.writes(), [])


if __name__ == "__main__":
    unittest.main()
