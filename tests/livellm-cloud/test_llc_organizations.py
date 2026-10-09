"""What scripts/llc.py tells an agent about an organization's workspace: the
402 of a used-up share, the 403s no permission lifts (owners_only, own_only,
credential_no_access, and support_credentials for LiveLLM support's own key or
agent) and the 409 of a plan the organization manages.

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

# The API's refusals, word for word.
SHARE = {"error": "This workspace is using its share of Acme. An owner can give it more under Organization → Billing.",
         "code": "organization_share"}
OWNERS_ONLY = {"error": "Only the workspace's owners can do this.", "code": "owners_only"}
OWN_ONLY = {"error": "Members change only the keys, agents, screen links and SSH keys they made.", "code": "own_only"}
NO_ACCESS = {"error": "The person behind this key or agent no longer has access to this workspace.",
             "code": "credential_no_access"}
ORG_BILLING = {"error": "Billing for this workspace is managed by Acme.", "code": "organization_billing"}
SUPPORT_CREDENTIALS = {"error": "LiveLLM support can't use API keys or agents in a customer's workspace.",
                       "code": "support_credentials"}
WHO_ONLY_REFUSALS = (OWNERS_ONLY, OWN_ONLY, NO_ACCESS, SUPPORT_CREDENTIALS)


class OrganizationRefusals(unittest.TestCase):

    def test_a_full_plan_names_the_organizations_share_and_where_an_owner_gives_more(self):
        # one answer for a personal plan and an organization's share alike
        for payload in (SHARE, {"error": "This would go past the plan's 4 cores."}):
            p = llc.status_problem(402, payload)
            self.assertEqual(p.code, llc.EXIT_USER, payload)
            self.assertEqual(p.status, 402, payload)
            self.assertEqual(p.message, payload["error"])
            self.assertIn("the plan is full", p.next, payload)
            self.assertIn("its share of the organization", p.next, payload)
            self.assertIn("an owner of the organization can give the workspace more under Organization → Billing", p.next, payload)
            self.assertIn("show the user their usage and stop", p.next, payload)
            self.assertIn("never delete to make room", p.next, payload)

    def test_owners_only_says_tell_the_user_and_stop(self):
        p = llc.status_problem(403, OWNERS_ONLY)
        self.assertEqual(p.next, "only the workspace's owners can do this: tell the user, and stop")
        self.assertEqual((p.code, p.status), (llc.EXIT_USER, 403))

    def test_own_only_says_tell_the_user_and_stop(self):
        p = llc.status_problem(403, OWN_ONLY)
        self.assertEqual(p.next, "only whoever made it can change it: tell the user, and stop")
        self.assertEqual((p.code, p.status), (llc.EXIT_USER, 403))

    def test_credential_no_access_says_tell_the_user_and_stop(self):
        p = llc.status_problem(403, NO_ACCESS)
        self.assertEqual(p.next, "the person who made this key or allowed this agent no longer has access to the workspace: "
                                 "tell the user, and stop")
        self.assertEqual((p.code, p.status), (llc.EXIT_USER, 403))

    def test_support_credentials_says_tell_the_user_and_stop(self):
        p = llc.status_problem(403, SUPPORT_CREDENTIALS)
        self.assertEqual(p.next, "LiveLLM support can't act through a key or an agent in a customer's workspace: "
                                 "tell the user, and stop")
        self.assertEqual((p.code, p.status), (llc.EXIT_USER, 403))

    def test_organization_billing_says_an_owner_changes_it_in_the_console(self):
        p = llc.status_problem(409, ORG_BILLING)
        self.assertEqual(p.next, "this workspace's plan is managed by its organization: tell the user an owner changes it in "
                                 "the console, and stop")
        self.assertEqual((p.code, p.status), (llc.EXIT_USER, 409))
        self.assertNotIn("retry", p.next)

    def test_no_permission_is_named_for_a_refusal_no_permission_lifts(self):
        for payload in WHO_ONLY_REFUSALS:
            p = llc.status_problem(403, payload)
            self.assertNotIn("permission", p.next, payload)
            self.assertNotIn("Agents page", p.next, payload)
            self.assertNotIn("Keys page", p.next, payload)

    def test_the_codes_alone_decide_and_only_on_their_own_status(self):
        # the same words with no code (an older LiveLLM) keep the generic answers
        p = llc.status_problem(403, {"error": OWNERS_ONLY["error"]})
        self.assertIn("which permission this needs", p.next)
        p = llc.status_problem(409, {"error": ORG_BILLING["error"]})
        self.assertIn("retry once", p.next)
        self.assertEqual(p.code, llc.EXIT_BUSY)
        # another 403 code, or none, is still a missing permission
        for payload in ({"error": "This agent can't change db."}, {"error": "x", "code": "support_read_only"}):
            p = llc.status_problem(403, payload)
            self.assertIn("which permission this needs", p.next, payload)
        # a code on another status is not taken for these
        p = llc.status_problem(422, {"error": "x", "code": "organization_billing"})
        self.assertIn("fix the field", p.next)
        p = llc.status_problem(409, {"error": "x", "code": "owners_only"})
        self.assertIn("retry once", p.next)
        # a code that isn't a string is no code
        p = llc.status_problem(403, {"error": "x", "code": ["owners_only"]})
        self.assertIn("which permission this needs", p.next)

    def test_an_organizations_name_never_picks_another_hint(self):
        # the name is any text the organization's owners chose: the code decides
        for name in ("Restart This Browser Ltd", "Import Anyway GmbH", "Most Snapshots Inc",
                     "Object storage has no backups Co", "Nothing to rotate to AG", "Turn on Network LLC"):
            message = f"Billing for this workspace is managed by {name}."
            p = llc.http_problem(409, {"error": message, "code": "organization_billing"})
            self.assertEqual(p.next, llc.ORGANIZATION_BILLING_NEXT, name)
            self.assertEqual((p.code, p.status, p.message), (llc.EXIT_USER, 409, message), name)
            share = f"This workspace is using its share of {name}. An owner can give it more under Organization → Billing."
            p = llc.http_problem(402, {"error": share, "code": "organization_share"})
            self.assertIn("its share of the organization", p.next, name)
            self.assertEqual(p.code, llc.EXIT_USER, name)

    def test_a_coded_refusal_keeps_its_answer_through_a_profile_import_or_copy(self):
        # profile import and copy name the rights to change a browser for a
        # plain 403; a refusal no permission lifts keeps its own answer
        for payload in WHO_ONLY_REFUSALS:
            with self.assertRaises(llc.Problem) as cm:
                with llc.may_place_profile():
                    raise llc.http_problem(403, payload)
            p = cm.exception
            self.assertEqual(p.next, llc.WHO_ONLY[payload["code"]], payload)
            self.assertEqual((p.refusal, p.code, p.status), (payload["code"], llc.EXIT_USER, 403), payload)
            self.assertNotIn("Manage", p.next, payload)

    def test_a_plain_refusal_through_a_profile_import_or_copy_still_names_the_browser_rights(self):
        for payload in ({"error": "This agent can't change b1."}, {"error": "x", "code": "support_read_only"}):
            with self.assertRaises(llc.Problem) as cm:
                with llc.may_place_profile():
                    raise llc.http_problem(403, payload)
            self.assertIn("putting a profile in a browser needs Manage, or Create on a browser this agent made", cm.exception.next)
        # the profile refusal keeps its own answer there too
        with self.assertRaises(llc.Problem) as cm:
            with llc.may_place_profile():
                raise llc.http_problem(403, {"error": "Profiles hold sign-ins. Only the workspace's people can import them."})
        self.assertIn("unless the person behind it is one of the workspace's people", cm.exception.next)
        # no API code is no refusal code
        self.assertEqual(llc.http_problem(403, {"error": "x", "code": ["owners_only"]}).refusal, "")
        self.assertEqual(llc.Problem("m", "n").refusal, "")

    def test_the_network_refusal_still_asks_the_user(self):
        p = llc.status_problem(403, {"error": "This agent can't let web reach db inside the workspace. "
                                              "A person can turn on Network for it on the Agents page.",
                                     "code": "network_permission"})
        self.assertIn("ask the user", p.next)

    def test_a_profile_refusal_names_the_workspaces_people_not_an_owner(self):
        p = llc.status_problem(403, {"error": "Profiles hold sign-ins. Only the workspace's people can export them."})
        self.assertIn("unless the person behind it is one of the workspace's people", p.next)
        self.assertNotIn("owner", p.next)
        self.assertEqual(p.code, llc.EXIT_USER)


if __name__ == "__main__":
    unittest.main()
