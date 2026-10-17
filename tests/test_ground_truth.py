"""Smoke test: the LLM-free ground truth still reproduces the known sample answers."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(ROOT, "ground_truth", "ground_truth.py")
DATA = os.path.join(ROOT, "data", "sample-data")


@unittest.skipUnless(os.path.isdir(DATA), "run scripts/fetch_data.sh first")
class GroundTruthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([sys.executable, "-I", SCRIPT], capture_output=True, text=True, timeout=120)
        cls.returncode, cls.out = run.returncode, run.stdout

    def test_exits_cleanly(self):
        self.assertEqual(self.returncode, 0)

    def test_covers_q1_to_q8(self):
        for q in range(1, 9):
            self.assertIn(f"Q{q} ", self.out)

    def test_dataset_size(self):
        self.assertIn("wells=200", self.out)

    def test_q1_latest_inspection_not_producing(self):
        self.assertIn("INS-0001-0203", self.out)
        self.assertIn("delta 3.2 producing False", self.out)


if __name__ == "__main__":
    unittest.main()
