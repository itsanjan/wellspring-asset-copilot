"""Integration: the product's SQL views agree with the LLM-free ground truth and with the
organizers' expected answers (03-evaluation-queries.md), on the real sample data."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date, timedelta

from wellspring import loader

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLE = os.path.abspath(os.path.join(ROOT, "data", "sample-data"))
# Point WELLSPRING_DATA at another dataset (e.g. the organizers' test data) to run the parity
# checks on it. The pinned organizer answers only apply to the sample data.
DATA = os.path.abspath(os.environ.get("WELLSPRING_DATA") or SAMPLE)
TODAY = date(2026, 10, 16)

con = gt = None


def _import_ground_truth():
    # ground_truth.py reads sys.argv[1] as the data root at import time.
    argv, sys.argv = sys.argv, ["ground_truth", DATA]
    try:
        spec = importlib.util.spec_from_file_location("ground_truth_ref", os.path.join(ROOT, "ground_truth", "ground_truth.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv = argv


def setUpModule():
    global con, gt
    if not os.path.isdir(DATA):
        raise unittest.SkipTest("run scripts/fetch_data.sh first")
    tmp = tempfile.mkdtemp()
    stats, problems = loader.load([DATA], os.path.join(tmp, "t.db"))
    assert not problems, problems
    con = sqlite3.connect(os.path.join(tmp, "t.db"))
    gt = _import_ground_truth()
    gt.areas, gt.wells = gt.load()
    unknown = {w["well_type"] for w in gt.wells.values()} - set(gt.THRESHOLD)
    if unknown:  # the independent reference only knows the sample's rules; say so instead of crashing
        raise unittest.SkipTest(f"ground_truth has no rule for well types {sorted(unknown)}; add them to THRESHOLD by hand to cross-check")


def tearDownModule():
    if con:
        con.close()


def q(sql, *args):
    return con.execute(sql, args).fetchall()


class LoadTest(unittest.TestCase):
    def test_counts_match_dataset_guide(self):
        self.assertEqual(q("SELECT COUNT(*) FROM wells")[0][0], len(gt.wells))
        if DATA == SAMPLE:
            self.assertEqual(len(gt.wells), 200)
        self.assertEqual(q("SELECT COUNT(*) FROM inspections")[0][0], sum(len(w["inspections"]) for w in gt.wells.values()))

    def test_every_well_type_has_a_threshold(self):
        self.assertEqual(q("SELECT COUNT(*) FROM v_inspection WHERE producing IS NULL")[0][0], 0)

    def test_load_is_idempotent(self):
        tmp = tempfile.mkdtemp()
        a, _ = loader.load([DATA], os.path.join(tmp, "a.db"))
        b, _ = loader.load([DATA], os.path.join(tmp, "a.db"))
        self.assertEqual(a, b)


class ParityWithGroundTruthTest(unittest.TestCase):
    """Two independent implementations must agree on every well, not just the sample wells."""

    def test_producing_flag_for_every_inspection(self):
        sql = {r[0] for r in q("SELECT inspection_id FROM v_inspection WHERE producing = 0")}
        ref = {i["inspection_id"] for w in gt.wells.values() for i in w["inspections"] if not gt.producing(w, i)}
        self.assertEqual(sql, ref)

    def test_real_spills_for_every_well(self):
        sql = {r[0] for r in q("SELECT inspection_id FROM v_real_spills")}
        ref = {i["inspection_id"] for w in gt.wells.values() for i in gt.real_spills(w, date(1900, 1, 1), date(2100, 1, 1))}
        self.assertEqual(sql, ref)

    def test_downtime_per_well_in_2025_and_last_4_months(self):
        for start, end in ((date(2025, 1, 1), date(2025, 12, 31)), (date(2026, 6, 16), TODAY)):
            sql = dict(q("SELECT asset_id, SUM(downtime_days) FROM v_downtime WHERE day BETWEEN ? AND ? GROUP BY asset_id",
                         str(start), str(end)))
            ref = {wid: gt.downtime_days(w, start, end) for wid, w in gt.wells.items()}
            ref = {k: v for k, v in ref.items() if v}
            self.assertEqual(sql, ref, f"{start}..{end}")

    def test_latest_inspection_per_well(self):
        sql = dict(q("SELECT asset_id, inspection_id FROM v_latest_inspection"))
        ref = {wid: w["inspections"][-1]["inspection_id"] for wid, w in gt.wells.items()}
        self.assertEqual(sql, ref)


@unittest.skipUnless(DATA == SAMPLE, "pinned answers apply to the sample dataset only")
class OrganizerExpectedAnswersTest(unittest.TestCase):
    """Pinned to the answers printed in 03-evaluation-queries.md (Q1-Q8, Q10)."""

    def test_q1_well_0001_not_producing(self):
        iid, method, inspector, delta, producing = q(
            "SELECT inspection_id, method, u.name, delta_c, producing FROM v_latest_inspection v "
            "JOIN users u ON u.user_id = v.inspector_user_id WHERE asset_id = 'WELL-0001'")[0]
        self.assertEqual((iid, method, inspector, delta, producing), ("INS-0001-0203", "drone", "Ryan Brooks", 3.2, 0))
        self.assertEqual(q("SELECT recorded_status FROM wells WHERE asset_id='WELL-0001'")[0][0], "active")

    def test_q2_northeast_block_8_not_producing(self):
        rows = q("SELECT asset_id, delta_c FROM v_latest_inspection WHERE area_id='NE-08' AND producing=0 ORDER BY asset_id")
        self.assertEqual(rows, [("WELL-0164", 4.1), ("WELL-0170", 2.9), ("WELL-0172", 5.7), ("WELL-0175", 0.4)])

    def test_q3_thirteen_readings_two_not_producing(self):
        rows = q("SELECT day, producing FROM v_inspection WHERE asset_id='WELL-0001' ORDER BY inspected_at DESC LIMIT 13")
        self.assertEqual((len(rows), rows[-1][0], rows[0][0]), (13, "2026-07-23", "2026-10-15"))
        self.assertEqual(sorted(d for d, p in rows if p == 0), ["2026-10-08", "2026-10-15"])

    def test_q4_six_spills_15_1_barrels_corrected_one_excluded(self):
        n, total = q("SELECT COUNT(*), ROUND(SUM(spill_est_volume_bbl),1) FROM v_real_spills "
                     "WHERE asset_id='WELL-0001' AND day >= '2022-10-20'")[0]
        self.assertEqual((n, total), (6, 15.1))
        self.assertEqual(q("SELECT COUNT(*) FROM v_inspection WHERE asset_id='WELL-0001' AND spill_detected=1")[0][0], 7)
        self.assertEqual(q("SELECT real_spill FROM v_inspection WHERE inspection_id='INS-0001-0070'")[0][0], 0)

    def test_q5_twenty_one_days_in_2025(self):
        self.assertEqual(q("SELECT SUM(downtime_days) FROM v_downtime WHERE asset_id='WELL-0001' AND day LIKE '2025-%'")[0][0], 21)

    def test_q6_top_three_downtime_northeast_block_7(self):
        rows = q("SELECT asset_id, SUM(downtime_days) d FROM v_downtime WHERE area_id='NE-07' AND day BETWEEN '2026-06-16' AND '2026-10-16' "
                 "GROUP BY asset_id ORDER BY d DESC, asset_id LIMIT 4")
        self.assertEqual(rows[0], ("WELL-0121", 49))
        self.assertEqual({r[0] for r in rows[1:3]}, {"WELL-0138", "WELL-0107"})
        self.assertEqual([r[1] for r in rows[:4]], [49, 42, 42, 28])

    def test_q7_four_pins_northeast_block_8(self):
        start = str(TODAY - timedelta(days=90))
        rows = q("SELECT asset_id, day, spill_est_volume_bbl, lat, lon FROM v_real_spills WHERE area_id='NE-08' AND day >= ? ORDER BY day DESC", start)
        self.assertEqual([r[0] for r in rows], ["WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"])
        self.assertEqual(rows[0][2:], (4.5, 32.12246, -102.18445))

    def test_q8_heatmap_totals_2026(self):
        rows = q("SELECT area_id, COUNT(*) FROM v_real_spills WHERE day BETWEEN '2026-01-01' AND '2026-10-16' GROUP BY area_id")
        totals = dict(rows)
        self.assertEqual({k: totals[k] for k in ("SW-23", "SW-24", "NE-07", "NE-08")}, {"SW-23": 35, "SW-24": 43, "NE-07": 37, "NE-08": 26})
        by_month = defaultdict(int)
        for (d,) in q("SELECT day FROM v_real_spills WHERE area_id='SW-24' AND day BETWEEN '2026-01-01' AND '2026-10-16'"):
            by_month[int(d[5:7])] += 1
        self.assertEqual([by_month[m] for m in range(1, 11)], [7, 4, 5, 6, 5, 5, 3, 1, 5, 2])

    def test_q10_two_uncontained_spills_southwest_block_24(self):
        start = str(TODAY - timedelta(days=90))
        rows = q("SELECT s.asset_id, rf.maintenance_id, ROUND(rf.contained_hours,1) FROM v_real_spills s "
                 "JOIN report_facts rf ON rf.inspection_id = s.inspection_id AND rf.protocol='SR-02' "
                 "WHERE s.area_id='SW-24' AND s.day >= ? AND rf.contained_hours > rf.limit_hours ORDER BY s.asset_id", start)
        self.assertEqual(rows, [("WELL-0082", "MNT-0082-028", 7.1), ("WELL-0100", "MNT-0100-034", 8.1)])

    def test_q11_repair_costs_this_year(self):
        rows = dict(q("SELECT asset_id, ROUND(SUM(cost_total)) FROM maintenance WHERE started_at >= '2026-01-01' "
                      "AND asset_id IN ('WELL-0121','WELL-0138','WELL-0107') GROUP BY asset_id"))
        self.assertEqual(rows, {"WELL-0121": 25794, "WELL-0138": 46942, "WELL-0107": 5176})


if __name__ == "__main__":
    unittest.main()
