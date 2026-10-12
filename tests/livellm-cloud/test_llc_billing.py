"""What scripts/llc.py tells an agent about a billing refusal, by its code:
pay as you go off (Free includes no resources) or past its monthly limit,
a card missing, a payment overdue (402), and pay as you go not open to the
account yet (403). Each says to ask or tell the user, names the console's
Billing page, and stops; a plain 402 still says the plan is full.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Only the standard library.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder clean
SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "livellm-cloud" / "scripts" / "llc.py"
spec = importlib.util.spec_from_file_location("llc", SCRIPT)
llc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llc)

# The API's refusals, as the plan words them.
PAYG_OFF = {"error": "Free includes no resources. Turn on pay as you go, or choose Pro, on the Billing page.",
            "code": "payg_off"}
PAYG_LIMIT = {"error": "This would bring pay as you go to $140 a month, past your $100 limit. Raise it on the Billing page.",
              "code": "payg_limit"}
CARD_REQUIRED = {"error": "Add a card first: Billing → Add a card.", "code": "card_required", "checkout": "card"}
PAYMENT_OVERDUE = {"error": "A payment failed on 3 November. Pay the open invoice to add resources.",
                   "code": "payment_overdue", "payUrl": "https://invoice.example/i/secret"}
NOT_OFFERED = {"error": "Pay as you go isn't open to new accounts yet.", "code": "payg_not_offered"}
REFUSALS = ((402, PAYG_OFF), (402, PAYG_LIMIT), (402, CARD_REQUIRED), (402, PAYMENT_OVERDUE), (403, NOT_OFFERED))


class BillingRefusals(unittest.TestCase):

    def test_each_code_gets_its_own_next_step_on_the_billing_page(self):
        for status, payload in REFUSALS:
            p = llc.http_problem(status, payload)
            self.assertEqual(p.next, llc.BILLING[payload["code"]], payload)
            self.assertEqual((p.code, p.status, p.message, p.refusal),
                             (llc.EXIT_USER, status, payload["error"], payload["code"]), payload)
            self.assertIn("the user", p.next, payload)
            self.assertIn("stop" if payload is not CARD_REQUIRED else "wait until they say it is done", p.next, payload)
            if payload is not NOT_OFFERED:
                self.assertIn("on the console's Billing page", p.next, payload)
            self.assertNotIn("retry", p.next, payload)
            self.assertNotIn("invoice.example", p.next, payload)

    def test_pay_as_you_go_off_asks_the_user_to_turn_it_on_and_never_deletes(self):
        p = llc.status_problem(402, PAYG_OFF)
        self.assertIn("ask them to turn on pay as you go, or choose a bigger plan, on the console's Billing page", p.next)
        self.assertIn("Free includes no resources", p.next)
        self.assertIn("show the user their usage and stop", p.next)
        self.assertIn("it costs them money, so they decide", p.next)
        self.assertIn("never delete to make room", p.next)

    def test_the_limit_is_raised_by_the_user_only(self):
        p = llc.status_problem(402, PAYG_LIMIT)
        self.assertIn("past the account's monthly limit", p.next)
        self.assertIn("only they can raise the limit, on the console's Billing page", p.next)
        self.assertIn("never delete to make room", p.next)

    def test_a_card_is_the_users_to_add(self):
        p = llc.status_problem(402, CARD_REQUIRED)
        self.assertIn("ask the user to add one on the console's Billing page (Add a card)", p.next)
        self.assertIn("never ask for card details", p.next)

    def test_an_overdue_payment_is_the_users_to_pay(self):
        p = llc.status_problem(402, PAYMENT_OVERDUE)
        self.assertIn("tell the user to pay the open invoice on the console's Billing page, and stop", p.next)
        self.assertIn("what runs keeps running", p.next)
        self.assertIn("never pay it or open a payment link yourself", p.next)

    def test_pay_as_you_go_not_offered_is_no_missing_permission(self):
        p = llc.status_problem(403, NOT_OFFERED)
        self.assertIn("aren't open to this account yet: tell the user, and stop", p.next)
        self.assertIn("no permission changes it", p.next)
        self.assertNotIn("Agents page", p.next)
        self.assertNotIn("Keys page", p.next)

    def test_a_plain_full_plan_names_pay_as_you_go(self):
        # an older LiveLLM sends no code, and a code this script doesn't know
        # gets the same answer
        for payload in ({"error": "subscription \"starter\" pool exceeded: CPU (3/2 cores) — upgrade or enable metered overage"},
                        {"error": "Something new.", "code": "something_new"}):
            p = llc.status_problem(402, payload)
            self.assertIn("the plan is full", p.next, payload)
            self.assertIn("turns on pay as you go or chooses a bigger plan on the console's Billing page", p.next, payload)
            self.assertIn("never delete to make room", p.next, payload)
            self.assertEqual(p.code, llc.EXIT_USER, payload)
            self.assertNotIn("metered", p.next, payload)

    def test_an_organizations_share_names_no_billing_page(self):
        # on an organization's workspace pay as you go and plans are refused
        # for anyone (409 organization_billing): an owner gives it more
        p = llc.status_problem(402, {"error": "This workspace is using its share of Acme.", "code": "organization_share"})
        self.assertEqual(p.next, llc.ORGANIZATION_SHARE_NEXT)
        self.assertIn("an owner of the organization can give the workspace more under Organization → Billing", p.next)
        self.assertNotIn("pay as you go", p.next)
        self.assertNotIn("Billing page", p.next)
        self.assertEqual(p.code, llc.EXIT_USER)

    def test_the_codes_decide_only_on_their_own_status(self):
        # the same words with no code keep the generic answers
        p = llc.status_problem(403, {"error": NOT_OFFERED["error"]})
        self.assertIn("which permission this needs", p.next)
        # a billing code on another status is not taken for these
        p = llc.status_problem(403, {"error": "x", "code": "payg_off"})
        self.assertIn("which permission this needs", p.next)
        p = llc.status_problem(402, {"error": "x", "code": "payg_not_offered"})
        self.assertIn("the plan is full", p.next)
        p = llc.status_problem(409, {"error": "x", "code": "payment_overdue"})
        self.assertIn("retry once", p.next)
        # words a billing refusal might carry never pick another hint
        p = llc.http_problem(402, {"error": "Turn on Network. Restart this browser. Object storage has no backups.",
                                   "code": "payg_off"})
        self.assertEqual(p.next, llc.BILLING["payg_off"])

    def test_pay_as_you_go_not_offered_keeps_its_answer_through_a_profile_import_or_copy(self):
        with self.assertRaises(llc.Problem) as cm:
            with llc.may_place_profile():
                raise llc.http_problem(403, NOT_OFFERED)
        self.assertEqual(cm.exception.next, llc.BILLING["payg_not_offered"])
        self.assertNotIn("Manage", cm.exception.next)


if __name__ == "__main__":
    unittest.main()
