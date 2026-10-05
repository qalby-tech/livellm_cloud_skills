"""The validator's consent check: SKILL.md must say, word for word, that the
agent asks the person before changing proxies, moving a profile or letting
resources reach each other.

Run from the repository root:  python3 -m unittest discover -s tests/livellm-cloud
Needs PyYAML (the validator does).
"""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("validate", ROOT / "tools" / "validate.py")
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)
SKILL = ROOT / "skills" / "livellm-cloud" / "SKILL.md"


class ConsentSentencesTest(unittest.TestCase):
    def test_the_skill_says_all_three(self):
        self.assertEqual(validate.missing_consent_sentences(SKILL.read_text(encoding="utf-8")), [])

    def test_wrapping_and_indents_do_not_count(self):
        body = "\n".join("    " + w if i % 7 == 0 else w
                         for i, w in enumerate(" ".join(validate.CONSENT_SENTENCES.values()).split()))
        self.assertEqual(validate.missing_consent_sentences(body), [])

    def test_each_one_missing_or_reworded_is_named(self):
        text = SKILL.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        for label, sentence in validate.CONSENT_SENTENCES.items():
            gone = flat.replace(" ".join(sentence.split()), "")
            self.assertEqual(validate.missing_consent_sentences(gone), [label])
        reworded = flat.replace("ask the user and wait for their agreement: it changes",
                                "you may tell the user afterwards: it changes")
        self.assertEqual(validate.missing_consent_sentences(reworded), ["S1 (proxies)"])

    def test_the_older_inside_sentence_no_longer_passes(self):
        # 1.12.0's first wording: no database rule, no service added to a Composable App
        flat = " ".join(SKILL.read_text(encoding="utf-8").split())
        s3 = " ".join(validate.CONSENT_SENTENCES["S3 (inside access)"].split())
        old = ("Resources in a workspace can't reach each other unless the user allows it (a Composable App counts as one "
               "resource). Before you let a resource reach another (reachableFrom, a database link, dependsOn, or a "
               "browser put in a Browser API), ask the user and wait for their agreement, unless you created both or the "
               "one reached already lets the whole workspace in. Letting the whole workspace in always needs their "
               "agreement. An API key or an agent also needs the Network permission for this, which only a person turns on.")
        self.assertEqual(validate.missing_consent_sentences(flat.replace(s3, old)), ["S3 (inside access)"])
        for part in ("a database is reached only by what links it", "a service added to a Composable App"):
            self.assertIn(part, s3)

    def test_no_permission_of_their_own_is_named_for_proxies_or_profiles(self):
        flat = " ".join(SKILL.read_text(encoding="utf-8").split())
        self.assertNotIn("(Proxies, Profiles)", flat)
        self.assertNotIn("Proxies permission", flat)
        self.assertNotIn("Profiles permission", flat)


if __name__ == "__main__":
    unittest.main()
