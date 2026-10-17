"""The whole path (adapter -> Copilot -> tools -> database -> scorer) with a scripted model, no network.
A scripted model that says the right thing must pass the real Q1 case; one that says the wrong thing must fail it."""
import unittest

from .. import dbfix
from ..fakes import FakeClient, present, reply, tool
from .cases import A1
from .harness import score

try:
    from . import adapter
except ImportError as err:
    adapter = None
    IMPORT_ERROR = err

Q1 = A1[0]


def setUpModule():
    if adapter is None:
        raise unittest.SkipTest(f"agent dependencies missing: {IMPORT_ERROR}")
    if not dbfix.have_data():
        raise unittest.SkipTest("run scripts/fetch_data.sh first")


def model_saying(answer):
    return FakeClient(reply(tool("t1", "well_status", asset_ids=["WELL-0001"])),
                      reply(present("p1", answer, "card", "R1")))


def ask_q1(client):
    adapter.reset(client, source=dbfix.base_db())
    turn = Q1.turns[0]
    return turn, adapter.ask(turn.user, turn.message, "s1")


class PipelineTest(unittest.TestCase):
    GOOD = ("No, WELL-0001 is not producing. Latest inspection INS-0001-0203 on 15 Oct 2026 by Ryan Brooks (drone): "
            "the wellhead is only 3.2 °C above the air. A planned shut-in is noted in CTX-0001-003.")

    def test_a_correct_model_passes_the_real_q1_case(self):
        turn, reply_ = ask_q1(model_saying(self.GOOD))
        self.assertEqual(score(turn, reply_), [])

    def test_the_reply_carries_visual_evidence_and_flags_from_the_tools(self):
        _, reply_ = ask_q1(model_saying(self.GOOD))
        self.assertEqual(reply_["visual"]["type"], "card")
        self.assertIn("INS-0001-0203", reply_["citations"])
        self.assertIn("[stale_status]", reply_["answer"])

    def test_a_model_that_says_the_well_is_fine_fails(self):
        turn, reply_ = ask_q1(model_saying("Yes, WELL-0001 is producing normally."))
        fails = score(turn, reply_)
        self.assertTrue(any("missing fact" in f for f in fails), fails)

    def test_wrong_visual_is_caught(self):
        client = FakeClient(reply(tool("t1", "well_status", asset_ids=["WELL-0001"])),
                            reply(present("p1", self.GOOD, "table", "R1")))
        turn, reply_ = ask_q1(client)
        self.assertTrue(any("visual" in f for f in score(turn, reply_)))


if __name__ == "__main__":
    unittest.main()
