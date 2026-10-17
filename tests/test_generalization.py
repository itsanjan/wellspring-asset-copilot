"""G-01, G-02, G-05: the product follows the data and the manuals, not the sample.
Uses perturbed and synthetic datasets, so it also works before the organizers' test data arrives."""
import os
import sqlite3
import tempfile
import unittest

from wellspring import loader

from . import synth
from .test_parity import _import_ground_truth  # ground_truth.py reads sys.argv at import, so reuse the patched loader


def build(dirs):
    path = os.path.join(tempfile.mkdtemp(), "g.db")
    stats, problems = loader.load(dirs, path)
    return sqlite3.connect(path), stats, problems


@unittest.skipUnless(os.path.isdir(synth.SAMPLE), "run scripts/fetch_data.sh first")
class RuleChangeFlowsThroughTest(unittest.TestCase):
    """G-05: edit a threshold in a manual, reload, and every derived answer moves with it."""

    @classmethod
    def setUpClass(cls):
        cls.base, _, _ = build([synth.SAMPLE])
        cls.gt = _import_ground_truth()
        cls.gt.areas, cls.gt.wells = cls.gt.load()
        cls.root = synth.copy_sample()
        synth.set_threshold(cls.root, "T200", 25)  # sample T200 temperatures are bimodal; 25 sits inside the producing band
        cls.con, _, cls.problems = build([cls.root])

    def test_threshold_is_read_from_the_edited_manual(self):
        self.assertEqual(self.con.execute("SELECT value FROM rule_params WHERE well_type='T200' AND name='producing_delta_c'").fetchone()[0], 25.0)
        self.assertEqual(self.problems, [])

    def test_downtime_rises_exactly_by_readings_between_15_and_25(self):
        q = "SELECT COUNT(*) FROM v_inspection WHERE producing = 0 AND well_type = 'T200'"
        before, after = self.base.execute(q).fetchone()[0], self.con.execute(q).fetchone()[0]
        between = self.base.execute("SELECT COUNT(*) FROM v_inspection WHERE well_type='T200' AND delta_c >= 15 AND delta_c < 25").fetchone()[0]
        self.assertGreater(between, 0)
        self.assertEqual(after - before, between)

    def test_t350_is_untouched(self):
        q = "SELECT COUNT(*) FROM v_inspection WHERE producing = 0 AND well_type = 'T350'"
        self.assertEqual(self.base.execute(q).fetchone(), self.con.execute(q).fetchone())

    def test_matches_ground_truth_told_the_new_rule(self):
        gt = self.gt
        old = dict(gt.THRESHOLD)
        gt.THRESHOLD["T200"] = 25.0  # tell the independent reference the new rule by hand
        try:
            ref = {i["inspection_id"] for w in gt.wells.values() if w["well_type"] == "T200"
                   for i in w["inspections"] if not gt.producing(w, i)}
        finally:
            gt.THRESHOLD.update(old)
        sql = {r[0] for r in self.con.execute("SELECT inspection_id FROM v_inspection WHERE producing = 0 AND well_type = 'T200'")}
        self.assertEqual(sql, ref)


@unittest.skipUnless(os.path.isdir(synth.SAMPLE), "run scripts/fetch_data.sh first")
class NewWellTypeAndSecondDatasetTest(unittest.TestCase):
    """G-01 / G-02: a new well type and a new well, loaded next to the sample data."""

    @classmethod
    def setUpClass(cls):
        cls.con, cls.stats, cls.problems = build([synth.SAMPLE, synth.extra_only_dataset()])

    def test_both_datasets_are_queryable(self):
        self.assertEqual(self.stats["wells"], 201)
        self.assertEqual(self.problems, [])

    def test_new_type_threshold_comes_from_its_manual(self):
        self.assertEqual(self.con.execute("SELECT value, source FROM rule_params WHERE well_type='T500' AND name='producing_delta_c'").fetchone(),
                         (25.0, "OM-T500 section 3"))

    def test_new_well_status_is_derived_with_inclusive_boundary(self):
        rows = self.con.execute("SELECT delta_c, producing FROM v_inspection WHERE asset_id='WELL-0201' ORDER BY inspected_at").fetchall()
        self.assertEqual(rows, [(26.0, 1), (24.0, 0), (25.0, 1)])

    def test_new_well_counts_in_area_downtime_and_latest(self):
        self.assertEqual(self.con.execute("SELECT SUM(downtime_days) FROM v_downtime WHERE asset_id='WELL-0201'").fetchone()[0], 7)
        self.assertEqual(self.con.execute("SELECT inspection_id FROM v_latest_inspection WHERE asset_id='WELL-0201'").fetchone()[0], "INS-0201-0003")

    def test_sample_answers_do_not_change_when_data_is_added(self):
        self.assertEqual(self.con.execute("SELECT COUNT(*), ROUND(SUM(spill_est_volume_bbl),1) FROM v_real_spills "
                                          "WHERE asset_id='WELL-0001' AND day >= '2022-10-20'").fetchone(), (6, 15.1))

    def test_same_id_in_later_dataset_replaces_earlier_record(self):
        extra = synth.extra_only_dataset()
        # Re-state INS-0201-0002 in a third dataset with a hot reading: later folders win.
        override = synth.extra_only_dataset()
        path = os.path.join(override, "assets", "WELL-0201", "inspections.jsonl")
        lines = open(path).read().splitlines()
        lines[1] = lines[1].replace('"wellhead_temp_c": 44.0', '"wellhead_temp_c": 50.0')
        open(path, "w").write("\n".join(lines) + "\n")
        con, _, _ = build([extra, override])
        self.assertEqual(con.execute("SELECT producing FROM v_inspection WHERE inspection_id='INS-0201-0002'").fetchone()[0], 1)


@unittest.skipUnless(os.path.isdir(synth.SAMPLE), "run scripts/fetch_data.sh first")
class MessyInputTest(unittest.TestCase):
    """Real feeds have gaps. A missing reading must be unknown, never counted as downtime."""

    @classmethod
    def setUpClass(cls):
        cls.root = synth.extra_only_dataset()
        path = os.path.join(cls.root, "assets", "WELL-0201", "inspections.jsonl")
        lines = open(path).read().splitlines()
        lines[1] = lines[1].replace('"wellhead_temp_c": 44.0', '"wellhead_temp_c": null')
        open(path, "w").write("\n".join(lines) + "\n")
        cls.con, _, cls.problems = build([cls.root])

    def test_well_with_only_inspections_file_loads(self):
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM inspections WHERE asset_id='WELL-0201'").fetchone()[0], 3)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM maintenance WHERE asset_id='WELL-0201'").fetchone()[0], 0)

    # A NULL temperature is unknown, not "not producing" (which would add 7 days of downtime).
    def test_null_reading_is_unknown_not_downtime(self):
        producing = self.con.execute("SELECT producing FROM v_inspection WHERE inspection_id='INS-0201-0002'").fetchone()[0]
        self.assertIsNone(producing)
        downtime = self.con.execute("SELECT COUNT(*) FROM v_downtime WHERE inspection_id='INS-0201-0002'").fetchone()[0]
        self.assertEqual(downtime, 0)


if __name__ == "__main__":
    unittest.main()
