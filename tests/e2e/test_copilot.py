"""Run the organizers' 18 questions through the real copilot. Skipped until COPILOT_URL or
COPILOT_ADAPTER is set (see tests/e2e/__init__.py)."""
import unittest

from .cases import A1, STATEFUL
from .harness import connect, score

CONN = connect()


@unittest.skipUnless(CONN, "set COPILOT_URL or COPILOT_ADAPTER to run end-to-end tests against the app")
class CopilotE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ask, reset = CONN
        cls.ask = staticmethod(ask)
        reset()

    def run_scenario(self, scenario):
        sessions = {}  # one conversation per user within a scenario, unless a turn starts a new chat
        for n, turn in enumerate(scenario.turns, 1):
            if turn.new_chat or turn.user not in sessions:
                sessions[turn.user] = f"{scenario.id}-{turn.user.split()[0].lower()}-{n}"
            reply = self.ask(turn.user, turn.message, sessions[turn.user])
            with self.subTest(scenario=scenario.id, turn=n):
                fails = score(turn, reply)
                self.assertEqual(fails, [], f"{scenario.id} turn {n} ({turn.user}): {turn.message[:70]}")

    def test_a1_single_user_questions(self):
        for s in A1:
            self.run_scenario(s)

    def test_a2_and_b_memory_feedback_and_multi_user(self):
        """Ordered: Q15 depends on Q12/Q13, Q16 on Q11."""
        for s in STATEFUL:
            self.run_scenario(s)


if __name__ == "__main__":
    unittest.main()
