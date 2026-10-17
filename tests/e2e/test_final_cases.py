"""The final-question scenarios match the organizers' file word for word (skipped if the file is absent)."""
import os
import re
import unittest

from .final_cases import FINAL

PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "test-data", "final-questions.md")


def squash(s):
    return re.sub(r"\s+", " ", s.replace("*", "").replace("“", '"').replace("”", '"')).strip()


class FinalCasesTest(unittest.TestCase):
    def test_eleven_scenarios_with_unique_ids(self):
        self.assertEqual([s.id for s in FINAL], [f"F{i}" for i in range(1, 12)])

    @unittest.skipUnless(os.path.exists(PATH), "organizers' final-questions.md not present")
    def test_wording_matches_organizers_file(self):
        with open(PATH) as f:
            text = squash(f.read())
        for s in FINAL:
            for t in s.turns:
                self.assertIn(squash(t.message), text, f"{s.id}: wording differs from final-questions.md")


if __name__ == "__main__":
    unittest.main()
