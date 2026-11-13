"""Renting GPUs with scripts/llc.py, against a stand-in for LiveLLM's API that
records every call: the reads send what they are asked, gpu-fit and gpu-rent
build the same body (one GPU, several on one server, clusters from a file; now
or booked), every command that spends money needs --yes, a cancel the API says
is charged in full is sent again with its confirm only with --confirm-charge,
an agent sign-in and a key without billing are told who may rent, a LiveLLM
without renting says so, and a GPU is given to a machine or an app through
the gpu field of an ordinary set or create.

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
from unittest import mock

sys.dont_write_bytecode = True  # keep the skill folder clean
SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud" / "scripts" / "llc.py"
spec = importlib.util.spec_from_file_location("llc", SCRIPT)
llc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llc)

RENTAL = {"id": "g-3f9a12bc", "model": "rtx5090", "name": "RTX 5090", "use": "apps", "state": "active",
          "phase": "ready", "usedBy": [], "cards": [{"id": "g-3f9a12bc", "model": "rtx5090", "answering": True}]}
MODELS = {"models": [{"model": "rtx5090", "name": "RTX 5090", "centsHour": 60, "total": 2,
                      "freeNow": {"machine": 1, "apps": 1}, "freeUntil": {"machine": None, "apps": None}}]}


class FakeAPI:
    """Answers each call with the next (status, answer) of `script`, the last
    one again once they run out (a str answer is sent as plain text, as a
    router's 404 page is); records every call with its body (None when it had
    none)."""

    def __init__(self, script):
        self.calls = []
        self.script = list(script)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_any(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n) if n else b""
                fake.calls.append((self.command, self.path, json.loads(raw) if raw else None))
                status, body = fake.script.pop(0) if len(fake.script) > 1 else fake.script[0]
                if isinstance(body, str):
                    data, kind = body.encode(), "text/plain; charset=utf-8"
                else:
                    data, kind = json.dumps(body).encode(), "application/json"
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class Stand(unittest.TestCase):

    def use(self, *script, key="llc_test"):
        fake = FakeAPI(script or [(200, {"rental": RENTAL})])
        self.addCleanup(fake.server.server_close)
        self.addCleanup(fake.server.shutdown)
        saved = {k: getattr(llc, k) for k in ("API", "API_KEY", "token")}
        self.addCleanup(lambda: [setattr(llc, k, v) for k, v in saved.items()])
        llc.API, llc.API_KEY = fake.url, key
        if not key:
            llc.token = lambda: "an-agent-sign-in"  # an agent's own sign-in, not a key
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

    def choice(self, **kw):
        args = {"model": None, "cards": None, "json": None, "use": None, "host": None, "region": None,
                "start": None, "end": None, "machine": None, "apps": None, "yes": True}
        args.update(kw)
        return args

    def parse(self, argv):
        """The command line as llc.py's own parser reads it: (exit code, stderr)."""
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["llc.py", *argv]), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                llc.main()
            except SystemExit as e:
                return e.code, err.getvalue()
        return 0, err.getvalue()


class Reads(Stand):

    def test_models_with_hours_and_use(self):
        fake = self.use((200, MODELS))
        res, problem = self.run_llc(llc.gpu_models, from_="2026-11-20T10:00:00Z", to="2026-11-20T14:00:00Z",
                                    use="machine")
        self.assertIsNone(problem)
        self.assertEqual(res, MODELS)
        method, path, _ = fake.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/v1/gpus/models?"))
        self.assertIn("from=2026-11-20T10%3A00%3A00Z", path)
        self.assertIn("use=machine", path)

    def test_from_without_to_sends_nothing(self):
        fake = self.use((200, MODELS))
        _, problem = self.run_llc(llc.gpu_models, from_="2026-11-20T10:00:00Z", to=None, use=None)
        self.assertIn("go together", problem.message)
        self.assertEqual(fake.calls, [])

    def test_list(self):
        fake = self.use((200, {"rentals": [RENTAL]}))
        res, _ = self.run_llc(llc.gpus_list)
        self.assertEqual(res["rentals"][0]["id"], "g-3f9a12bc")
        self.assertEqual(fake.calls, [("GET", "/v1/gpus", None)])

    def test_a_livellm_without_gpus_says_so(self):
        self.use((404, "404 page not found\n"))
        _, problem = self.run_llc(llc.gpus_list)
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 404))
        self.assertIn("GPUs aren't offered on this LiveLLM", problem.message)

    def test_an_agent_reads_like_a_key(self):
        fake = self.use((200, MODELS), key="")
        res, problem = self.run_llc(llc.gpu_models, from_=None, to=None, use=None)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("GET", "/v1/gpus/models", None)])


class Bodies(Stand):

    def test_fit_sends_one_gpu_with_its_location(self):
        fake = self.use((200, {"clusters": [{"fits": True, "max": {"rtx5090": 1}}]}))
        _, problem = self.run_llc(llc.gpu_fit, **self.choice(model="rtx5090", use="machine", region="eu"))
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("POST", "/v1/gpus/fit", {
            "model": "rtx5090", "use": "machine", "location": {"mode": "region", "region": "eu"}})])

    def test_fit_and_rent_send_the_same_body(self):
        fake = self.use((200, {}), (201, {"clusters": [RENTAL]}))
        kw = self.choice(cards="rtx5090=2,h100", use="apps", host="s1",
                         start="2026-11-20T10:00:00Z", end="2026-11-20T14:00:00Z")
        self.run_llc(llc.gpu_fit, **kw)
        self.run_llc(llc.gpu_rent, **kw)
        self.assertEqual(fake.calls[0][2], fake.calls[1][2])
        self.assertEqual(fake.calls[1], ("POST", "/v1/gpus", {
            "clusters": [{"use": "apps", "cards": {"rtx5090": 2, "h100": 1}, "location": {"mode": "host", "host": "s1"}}],
            "startsAt": "2026-11-20T10:00:00Z", "endsAt": "2026-11-20T14:00:00Z"}))

    def test_clusters_from_a_file(self):
        clusters = [{"use": "machine", "cards": {"rtx5090": 1}}, {"use": "apps", "cards": {"rtx5090": 1},
                                                                  "location": {"mode": "auto"}}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"clusters": clusters}, f)
        self.addCleanup(Path(f.name).unlink)
        fake = self.use((201, {"clusters": [RENTAL, RENTAL]}))
        res, problem = self.run_llc(llc.gpu_rent, **self.choice(json=f.name))
        self.assertIsNone(problem)
        self.assertEqual(fake.calls[0][2], {"clusters": clusters})
        self.assertIn("by the minute", res["next"])

    def test_a_file_with_hours_beside_its_clusters_sends_nothing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"clusters": [{"use": "apps", "cards": {"rtx5090": 1}}],
                       "startsAt": "2026-11-20T10:00:00Z", "endsAt": "2026-11-20T14:00:00Z"}, f)
        self.addCleanup(Path(f.name).unlink)
        fake = self.use()
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(json=f.name))
        self.assertIn("endsAt, startsAt", problem.message)
        self.assertIn("--start and --end", problem.next)
        self.assertEqual(fake.calls, [])

    def test_a_file_with_flags_beside_it_is_refused(self):
        fake = self.use()
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(json="x.json", use="apps"))
        self.assertIn("each cluster says its own", problem.message)
        self.assertEqual(fake.calls, [])

    def test_one_choice_only_and_a_use(self):
        fake = self.use()
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", cards="rtx5090=2", use="apps"))
        self.assertIn("don't go together", problem.message)
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090"))
        self.assertIn("--use", problem.next)
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(cards="rtx5090=0", use="apps"))
        self.assertIn("--cards", problem.message)
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps", host="a", region="b"))
        self.assertIn("not both", problem.message)
        self.assertEqual(fake.calls, [])

    def test_a_booking_needs_its_end(self):
        fake = self.use()
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps", start="2026-11-20T10:00:00Z"))
        self.assertIn("--end", problem.next)
        self.assertEqual(fake.calls, [])

    def test_a_booking_names_what_gets_it(self):
        fake = self.use((201, {"rental": dict(RENTAL, state="booked")}))
        res, problem = self.run_llc(llc.gpu_rent, **self.choice(
            model="rtx5090", use="machine", machine="trainer", start="2026-11-20T10:00:00Z", end="2026-11-20T14:00:00Z"))
        self.assertIsNone(problem)
        self.assertEqual(fake.calls[0][2]["target"], {"machine": "trainer"})
        self.assertIn("every booked hour is charged", res["next"])
        self.assertIn("more than 24 hours", res["next"])

    def test_a_rental_now_names_no_target(self):
        fake = self.use()
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps", apps="web"))
        self.assertIn("llc.py set", problem.next)
        self.assertEqual(fake.calls, [])

    def test_a_rental_now_runs_until_released_and_says_how_to_attach(self):
        fake = self.use((201, {"rental": RENTAL}))
        res, _ = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertEqual(fake.calls[0][2], {"model": "rtx5090", "use": "apps"})
        self.assertIn('{"pod": {"gpu": "g-3f9a12bc"}}', res["next"])
        self.assertIn("gpu-release g-3f9a12bc", res["next"])


class Consent(Stand):

    def test_every_command_that_spends_or_changes_needs_yes(self):
        for argv in (["gpu-rent", "--model", "rtx5090", "--use", "apps"],
                     ["gpu-extend", "g-3f9a12bc", "--end", "2026-11-20T16:00:00Z"],
                     ["gpu-use", "g-3f9a12bc", "machine"],
                     ["gpu-release", "g-3f9a12bc"],
                     ["gpu-remove-card", "g-3f9a12bc", "g-7c01d2e4"]):
            fake = self.use()
            code, err = self.parse(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("--yes", err)
            self.assertEqual(fake.calls, [], argv)

    def test_the_reads_and_fit_need_no_yes(self):
        fake = self.use((200, {}))
        self.assertEqual(self.parse(["gpu-fit", "--model", "rtx5090", "--use", "apps"])[0], 0)
        self.assertEqual(fake.calls[0][:2], ("POST", "/v1/gpus/fit"))


class Cancels(Stand):

    CHARGED = (428, {"error": "This booking starts within a day, so it is charged in full. Send its id as confirm to "
                              "cancel it.", "code": "confirm_required"})

    def test_a_free_cancel_sends_no_confirm(self):
        fake = self.use((200, {"rental": dict(RENTAL, state="cancelled"), "chargedCents": 0}))
        res, problem = self.run_llc(llc.gpu_release, id="g-3f9a12bc", confirm_charge=True, yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("DELETE", "/v1/gpus/g-3f9a12bc", None)])
        self.assertEqual(res["chargedCents"], 0)

    def test_a_charged_cancel_stops_without_confirm_charge(self):
        fake = self.use(self.CHARGED)
        _, problem = self.run_llc(llc.gpu_release, id="g-3f9a12bc", confirm_charge=False, yes=True)
        self.assertEqual((problem.code, problem.status, problem.refusal), (llc.EXIT_USER, 428, "confirm_required"))
        self.assertIn("charged in full", problem.message)
        self.assertIn("--confirm-charge", problem.next)
        self.assertIn("ask", problem.next)
        self.assertEqual(fake.calls, [("DELETE", "/v1/gpus/g-3f9a12bc", None)])

    def test_confirm_charge_sends_the_rentals_id_after_the_428(self):
        fake = self.use(self.CHARGED, (200, {"rental": dict(RENTAL, state="cancelled"), "chargedCents": 240}))
        res, problem = self.run_llc(llc.gpu_release, id="g-3f9a12bc", confirm_charge=True, yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("DELETE", "/v1/gpus/g-3f9a12bc", None),
                                      ("DELETE", "/v1/gpus/g-3f9a12bc", {"confirm": "g-3f9a12bc"})])
        self.assertEqual(res["chargedCents"], 240)

    def test_a_booked_card_is_confirmed_by_the_cards_id(self):
        fake = self.use(self.CHARGED, (200, {"rental": RENTAL}))
        _, problem = self.run_llc(llc.gpu_remove_card, id="g-3f9a12bc", card="g-7c01d2e4", confirm_charge=True, yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("DELETE", "/v1/gpus/g-3f9a12bc/cards/g-7c01d2e4", None),
                                      ("DELETE", "/v1/gpus/g-3f9a12bc/cards/g-7c01d2e4", {"confirm": "g-7c01d2e4"})])

    def test_a_booked_card_without_confirm_charge_stops(self):
        fake = self.use(self.CHARGED)
        _, problem = self.run_llc(llc.gpu_remove_card, id="g-3f9a12bc", card="g-7c01d2e4", confirm_charge=False,
                                  yes=True)
        self.assertEqual(problem.status, 428)
        self.assertEqual(len(fake.calls), 1)

    def test_a_gpu_in_use_names_what_uses_it(self):
        self.use((409, {"error": "In use by trainer. Take the GPU off them first.", "code": "gpu_in_use",
                        "usedBy": ["trainer"]}))
        _, problem = self.run_llc(llc.gpu_release, id="g-3f9a12bc", confirm_charge=False, yes=True)
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 409))
        self.assertIn("trainer", problem.message)
        self.assertIn("ask the user", problem.next)


class Changes(Stand):

    def test_extend_sends_the_new_end(self):
        fake = self.use((200, {"rental": RENTAL}))
        _, problem = self.run_llc(llc.gpu_extend, id="g-3f9a12bc", end="2026-11-20T18:00:00Z", yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("PATCH", "/v1/gpus/g-3f9a12bc", {"endsAt": "2026-11-20T18:00:00Z"})])

    def test_use_while_the_gpu_gets_ready(self):
        fake = self.use((202, {"rental": dict(RENTAL, use="machine", phase="switching-to-machine")}))
        res, _ = self.run_llc(llc.gpu_use, id="g-3f9a12bc", use="machine", yes=True)
        self.assertEqual(fake.calls, [("POST", "/v1/gpus/g-3f9a12bc/use", {"use": "machine"})])
        self.assertIn("getting ready", res["next"])

    def test_use_ready_at_once_says_nothing_more(self):
        self.use((200, {"rental": dict(RENTAL, use="machine")}))
        res, _ = self.run_llc(llc.gpu_use, id="g-3f9a12bc", use="machine", yes=True)
        self.assertNotIn("next", res)

    def test_too_soon_waits(self):
        self.use((409, {"error": "You can change use again at 15:00 UTC.", "code": "gpu_change_too_soon",
                        "at": "2026-11-20T15:00:00Z"}))
        _, problem = self.run_llc(llc.gpu_use, id="g-3f9a12bc", use="apps", yes=True)
        self.assertEqual(problem.code, llc.EXIT_BUSY)
        self.assertIn("once an hour", problem.next)


class Who(Stand):

    def test_an_agent_sign_in_is_told_to_ask_the_person(self):
        self.use((403, {"error": "Agents can't rent GPUs. Do it in the LiveLLM console."}), key="")
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 403))
        self.assertIn("console", problem.next)
        self.assertIn("billing permission", problem.next)
        self.assertIn("LIVELLM_API_KEY", problem.next)
        self.assertNotIn("Agents page", problem.next)

    def test_a_key_without_billing_is_told_where_it_is_given(self):
        self.use((403, {"error": "This API key can't rent GPUs. A person can give it the billing permission on the "
                                 "Keys page."}))
        _, problem = self.run_llc(llc.gpu_extend, id="g-3f9a12bc", end="2026-11-20T18:00:00Z", yes=True)
        self.assertIn("Keys page", problem.next)
        self.assertNotIn("LIVELLM_API_KEY", problem.next)

    def test_a_members_key_is_the_owners_refusal(self):
        self.use((403, {"error": "Only the workspace's owners can do this.", "code": "owners_only"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertIn("owners", problem.next)

    def test_renting_off_says_it_isnt_offered(self):
        for answer in ("404 page not found\n", {"error": "GPUs aren't offered on this cloud."}):
            self.use((404, answer))
            _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
            self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, 404))
            self.assertEqual(problem.message, "GPU renting isn't offered on this LiveLLM")
            _, problem = self.run_llc(llc.gpu_release, id="g-3f9a12bc", confirm_charge=False, yes=True)
            self.assertEqual(problem.message, "GPU renting isn't offered on this LiveLLM")

    def test_pay_as_you_go_off_says_gpus_are_pay_as_you_go(self):
        self.use((402, {"error": "Turn on pay as you go first.", "code": "payg_off"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertIn("always pay as you go", problem.next)

    def test_a_card_is_the_users(self):
        self.use((402, {"error": "Add a card first.", "code": "card_required"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertIn("never ask for card details", problem.next)

    def test_none_free_and_clusters_off(self):
        self.use((409, {"error": "No free GPU for those hours.", "code": "gpu_none_free"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertIn("gpu-fit", problem.next)
        self.use((422, {"error": "One GPU per cluster on this cloud.", "code": "gpu_clusters_off"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(cards="rtx5090=2", use="apps"))
        self.assertEqual((problem.status, problem.refusal), (422, "gpu_clusters_off"))
        self.assertIn("one GPU per cluster", problem.next)

    def test_a_gpu_not_offered_stops_at_either_status(self):
        for status, words in ((403, "This GPU isn't offered yet."), (409, "GPUs can't be rented on this workspace.")):
            self.use((status, {"error": words, "code": "gpu_not_offered"}))
            _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
            self.assertEqual((problem.code, problem.status), (llc.EXIT_USER, status))
            self.assertIn("and stop", problem.next)

    def test_payments_unavailable_is_a_refusal_not_a_lost_answer(self):
        self.use((503, {"error": "Payments are unavailable right now.", "code": "billing_unavailable"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertEqual(problem.status, 503)
        self.assertNotIn("never rent", problem.next)

    def test_a_lost_answer_says_look_before_renting_again(self):
        self.use((502, {"error": "bad gateway"}))
        _, problem = self.run_llc(llc.gpu_rent, **self.choice(model="rtx5090", use="apps"))
        self.assertIn("llc.py gpus", problem.next)
        self.assertIn("never rent or extend twice", problem.next)


class Attach(Stand):
    """A GPU goes to a machine, an app or a Desktop App through the gpu field
    of an ordinary set or create: sent as written, and never added."""

    def write(self, body):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(body, f)
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_set_sends_the_gpu_as_written(self):
        fake = self.use((200, {}))
        _, problem = self.run_llc(llc.set_settings, id="trainer", json=self.write({"vm": {"gpu": "g-3f9a12bc"}}), yes=True)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls, [("PATCH", "/v1/workloads/trainer", {"vm": {"gpu": "g-3f9a12bc"}})])

    def test_set_takes_it_off_with_an_empty_id(self):
        fake = self.use((200, {}))
        self.run_llc(llc.set_settings, id="web", json=self.write({"pod": {"gpu": ""}}), yes=True)
        self.assertEqual(fake.calls[0][2], {"pod": {"gpu": ""}})

    def test_a_set_without_gpu_sends_none(self):
        fake = self.use((200, {}))
        self.run_llc(llc.set_settings, id="web", json=self.write({"pod": {"memory": "512Mi"}}), yes=True)
        self.assertNotIn("gpu", json.dumps(fake.calls[0][2]))

    def test_a_gpu_not_ready_on_start_says_wait(self):
        self.use((409, {"error": "Your GPU isn't ready yet.", "code": "gpu_not_ready"}))
        _, problem = self.run_llc(llc.set_settings, id="trainer", json=self.write({"vm": {"gpu": "g-3f9a12bc"}}),
                                  yes=True)
        self.assertEqual(problem.code, llc.EXIT_NOT_READY)
        self.assertIn("phase", problem.next)

    def test_a_gpu_that_isnt_working_asks_about_removing_it(self):
        self.use((409, {"error": "One GPU isn't working. Remove it to run on the rest.", "code": "gpu_not_ready"}))
        _, problem = self.run_llc(llc.set_settings, id="trainer", json=self.write({"stopped": False}), yes=True)
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertIn("gpu-remove-card", problem.next)
        self.assertIn("only if they agree", problem.next)

    def test_a_gpu_in_devices_without_a_rental(self):
        self.use((409, {"error": "trainer: rent a GPU for this machine first.", "code": "gpu_not_rented"}))
        _, problem = self.run_llc(llc.set_settings, id="trainer", json=self.write({"vm": {"devices": ["rtx5090"]}}),
                                  yes=True)
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertIn("gpu-rent", problem.next)
        self.assertIn("price", problem.next)

    def test_a_gpu_set_by_an_administrator_is_not_rented(self):
        self.use((409, {"error": "trainer: set this GPU to Machines first.", "code": "gpu_not_rented"}))
        _, problem = self.run_llc(llc.set_settings, id="trainer", json=self.write({"vm": {"devices": ["rtx5090"]}}),
                                  yes=True)
        self.assertEqual(problem.code, llc.EXIT_USER)
        self.assertIn("aren't rented", problem.next)
        self.assertNotIn("gpu-rent", problem.next)

    def test_a_machine_create_carries_the_gpu(self):
        fake = self.use((201, {}))
        body = {"id": "trainer", "gpu": "g-3f9a12bc", "privateNetwork": False,
                "credentials": {"username": "u", "password": "p"}}
        _, problem = self.run_llc(llc.create, type="vm-ubuntu", json=self.write(body), yes=True, engine=None,
                                  private_address=None, no_private_network=False)
        self.assertIsNone(problem)
        self.assertEqual(fake.calls[0][:2], ("POST", "/v1/workloads/vm-ubuntu"))
        self.assertEqual(fake.calls[0][2]["gpu"], "g-3f9a12bc")


if __name__ == "__main__":
    unittest.main()
