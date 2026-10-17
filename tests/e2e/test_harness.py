"""The scorer itself must not be vacuous: it passes a good answer and fails wrong ones."""
import unittest

from .cases import A1, STATEFUL
from .harness import Turn, norm, score

GOOD = {"answer": "No, it is not producing. INS-0001-0203 by Ryan Brooks, 3.2 °C. CTX-0001-003. Cost USD 25,794.",
        "visual": {"type": "status_card"}, "citations": ["INS-0001-0203"]}


class ScoreTest(unittest.TestCase):
    def test_good_answer_passes(self):
        t = Turn("u", "q", must=(("not producing", "no,"), "INS-0001-0203", "3.2", "25794"), visual="card", min_citations=1)
        self.assertEqual(score(t, GOOD), [])

    def test_missing_fact_wrong_visual_and_no_evidence_all_fail(self):
        t = Turn("u", "q", must=("WELL-9999",), visual="map", min_citations=2)
        self.assertEqual(len(score(t, GOOD)), 3)

    def test_must_not_catches_leaked_memory(self):
        self.assertTrue(score(Turn("u", "q", must_not=("25794",)), GOOD))

    def test_normalisation(self):
        self.assertEqual(norm("USD 5,000 − 41.0 °C"), "5000 - 41.0 c")

    def test_citations_count_as_evidence_text(self):
        self.assertEqual(score(Turn("u", "q", must=("MNT-1",)), {"answer": "x", "citations": ["MNT-1"]}), [])


class CaseTableTest(unittest.TestCase):
    def test_all_eighteen_questions_present_in_order(self):
        self.assertEqual([s.id for s in A1 + STATEFUL], [f"Q{i}" for i in range(1, 19)])

    def test_every_a1_case_has_checks(self):
        for s in A1:
            self.assertTrue(s.turns[0].must, s.id)


if __name__ == "__main__":
    unittest.main()
