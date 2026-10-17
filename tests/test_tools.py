"""The tool layer is where answers are decided. These tests call the tools directly (no model) and
check the organizers' expected facts for Q1-Q18 plus the overlay semantics (corrections, scoped rules,
claims, private memory)."""
import unittest

from wellspring import tools

from . import dbfix

TODAY = "2026-10-16"


def setUpModule():
    if not dbfix.have_data():
        raise unittest.SkipTest("run scripts/fetch_data.sh first")


def run(con, user, name, **args):
    return tools.run(tools.Context(con, user), name, args)


def ids(out, key="asset_id"):
    return [r[key] for r in out["rows"]]


def flags(out, kind):
    return [f for f in out["flags"] if f["kind"] == kind]


class ReadOnlyAnswersTest(unittest.TestCase):
    """Q1-Q11 and the first half of Q14, straight from the tools, on the untouched sample data."""

    @classmethod
    def setUpClass(cls):
        cls.con = dbfix.connect(dbfix.base_db())

    def call(self, name, **a):
        return run(self.con, "U-201", name, **a)

    def test_q1_status_card_well_0001(self):
        out = self.call("well_status", asset_ids=["WELL-0001"])
        row = out["rows"][0]
        self.assertEqual((row["status"], row["delta_c"], row["threshold_c"]), ("not producing", 3.2, 15.0))
        self.assertEqual(row["recorded_status"], "active")
        self.assertIn("card", out["visuals"])
        self.assertEqual(len(flags(out, "stale_status")), 1)
        refs = {e["ref"] for e in out["evidence"]}
        self.assertTrue({"INS-0001-0203", "CTX-0001-003"} <= refs, refs)

    def test_q2_northeast_block_8_four_wells_two_without_reason(self):
        out = self.call("well_status", area_id="NE-08", only_not_producing=True)
        self.assertEqual(sorted(ids(out)), ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"])
        no_reason = {r for f in flags(out, "no_recorded_reason") for r in f["refs"]}
        self.assertEqual(no_reason, {"WELL-0164", "WELL-0175"})
        refs = {e["ref"] for e in out["evidence"]}
        self.assertTrue({"MNT-0170-031", "MNT-0172-028"} <= refs, refs)
        self.assertIn("table", out["visuals"])

    def test_q3_thirteen_readings_line_chart(self):
        out = self.call("inspection_history", asset_id="WELL-0001", last_n=13)
        self.assertEqual(out["summary"]["readings"], 13)
        self.assertEqual(out["summary"]["not_producing_days"], ["2026-10-08", "2026-10-15"])
        self.assertIn("line", out["visuals"])

    def test_q4_six_spills_15_1_barrels_one_excluded_as_corrected(self):
        s = self.call("spills", asset_ids=["WELL-0001"], start="2022-10-20")["summary"]
        self.assertEqual((s["spills"], s["total_bbl"], s["detections"], s["excluded_as_corrected"]), (6, 15.1, 7, 1))
        self.assertEqual(s["excluded"][0]["inspection_id"], "INS-0001-0070")
        self.assertEqual(s["excluded"][0]["reason"]["note_id"], "CTX-0001-001")

    def test_q5_twenty_one_days_with_the_three_inspections(self):
        row = self.call("downtime", asset_ids=["WELL-0001"], start="2025-01-01", end="2025-12-31")["rows"][0]
        self.assertEqual(row["downtime_days"], 21)
        self.assertEqual(row["inspections"], ["INS-0001-0140", "INS-0001-0152", "INS-0001-0153"])

    def test_q6_top_three_downtime_bar_chart(self):
        out = self.call("downtime", area_id="NE-07", start="2026-06-16", end=TODAY, top_n=3)
        self.assertEqual([(r["asset_id"], r["downtime_days"]) for r in out["rows"]][0], ("WELL-0121", 49))
        self.assertEqual(sorted(r["downtime_days"] for r in out["rows"]), [42, 42, 49])
        self.assertEqual(out["summary"]["next_after_top"], {"asset_id": "WELL-0119", "downtime_days": 28})
        self.assertIn("bar", out["visuals"])

    def test_q7_four_map_pins_northeast_block_8(self):
        out = self.call("spills", area_id="NE-08", start="2026-07-18", end=TODAY)
        self.assertEqual(sorted(ids(out)), ["WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"])
        self.assertIn("map", out["visuals"])

    def test_q8_heatmap_totals_by_area(self):
        out = self.call("spills", start="2026-01-01", end=TODAY, group_by="area_month")
        self.assertIn("heatmap", out["visuals"])
        per_area = {}
        for r in out["rows"]:
            per_area[r["area_id"]] = per_area.get(r["area_id"], 0) + 1
        self.assertEqual({k: per_area[k] for k in ("SW-23", "SW-24", "NE-07", "NE-08")},
                         {"SW-23": 35, "SW-24": 43, "NE-07": 37, "NE-08": 26})

    def test_q9_well_0001_four_breaches_and_open_items(self):
        out = self.call("compliance_check", asset_ids=["WELL-0001"])
        protocols = {r["protocol"] for r in out["rows"]}
        self.assertEqual(protocols, {"SR-01", "SR-02", "SR-03", "RC-04"})
        self.assertEqual(out["summary"]["breaches"], 4)
        detail = " ".join(r["detail"] for r in out["rows"])
        self.assertIn("REG-0001-002", detail + " ".join(e["ref"] for e in out["evidence"]))
        open_refs = {o["ref"] for o in out["summary"]["open_items"]}
        self.assertIn("CTX-0001-003", open_refs)  # planned shut-in not in the maintenance system

    def test_q10_uncontained_spills_southwest_block_24(self):
        out = self.call("compliance_check", area_id="SW-24", start="2026-07-18", end=TODAY)
        sr02 = [r for r in out["rows"] if r["protocol"] == "SR-02"]
        self.assertEqual(sorted((r["asset_id"], r["ref"]) for r in sr02), [("WELL-0082", "INS-0082-0198"), ("WELL-0100", "INS-0100-0196")])
        self.assertIn("7.1 h", " ".join(r["detail"] for r in sr02))
        self.assertIn("8.1 h", " ".join(r["detail"] for r in sr02))

    def test_q11_repair_costs_this_year(self):
        per = {w["asset_id"]: w["cost_usd"] for w in self.call(
            "repairs", asset_ids=["WELL-0121", "WELL-0138", "WELL-0107"], start="2026-01-01", end=TODAY)["summary"]["per_well"]}
        self.assertEqual(per, {"WELL-0121": 25794.0, "WELL-0138": 46942.0, "WELL-0107": 5176.0})

    def test_q14_first_question_three_wells_need_rca(self):
        out = self.call("maintenance_escalation", area_id="NE-07", equipment_kind="pump_jack")
        self.assertEqual(sorted(ids(out)), ["WELL-0108", "WELL-0121", "WELL-0138"])
        near = {w["asset_id"] for w in out["summary"]["one_below_threshold"]}
        self.assertIn("WELL-0102", near)

    def test_unknown_inputs_are_rejected_not_invented(self):
        for name, args in (("nope", {}), ("well_status", {"bogus": 1}), ("inspection_history", {})):
            with self.assertRaises(tools.ToolError, msg=name):
                self.call(name, **args)


class FeedbackAndMemoryTest(unittest.TestCase):
    """Q12-Q17 at tool level. Each test gets its own database copy, because these tools write."""

    def setUp(self):
        self.con = dbfix.connect(dbfix.copy_db())
        self.addCleanup(self.con.close)

    def call(self, user, name, **a):
        return run(self.con, user, name, **a)

    # Q12 / Q15
    def test_q12_raw_correction_is_kept_flagged_and_changes_the_answer(self):
        rec = self.call("U-201", "record_correction", inspection_id="INS-0164-0205", field="wellhead_temp_c",
                        new_value="41.0", reason="handheld gun faulty; crew re-measured")
        self.assertEqual((rec["summary"]["recorded"]["original_value"], rec["summary"]["recorded"]["new_value"]), ("24.0", "41.0"))
        self.assertEqual(rec["summary"]["recorded"]["author_user_id"], "U-201")
        self.assertEqual(rec["rows"][0]["producing"], 1)
        self.assertEqual(rec["rows"][0]["delta_c"], 21.1)
        self.assertTrue(flags(rec, "conflict"), "must flag the 'Pump jack not moving.' remark")
        again = self.call("U-201", "well_status", area_id="NE-08", only_not_producing=True)
        self.assertEqual(sorted(ids(again)), ["WELL-0170", "WELL-0172", "WELL-0175"])

    def test_q15_correction_reaches_another_user_with_the_original_kept(self):
        self.call("U-201", "record_correction", inspection_id="INS-0164-0205", field="wellhead_temp_c", new_value="41.0", reason="r")
        out = self.call("U-202", "well_status", asset_ids=["WELL-0164"])
        self.assertEqual((out["rows"][0]["status"], out["rows"][0]["delta_c"]), ("producing", 21.1))
        kinds = {f["kind"] for f in out["flags"]}
        self.assertTrue({"correction_applied", "conflict"} <= kinds, kinds)
        self.assertIn("raw value 24.0", flags(out, "correction_applied")[0]["message"])

    # Q13 / Q15
    def test_q13_ai_estimate_correction_makes_the_spill_reportable_and_cascades(self):
        self.call("U-201", "record_correction", inspection_id="INS-0151-0201", field="spill_est_volume_bbl", new_value="5.4", reason="crew measured")
        spills = self.call("U-401", "spills", area_id="NE-08", start="2026-07-18", end=TODAY, min_class="reportable")
        self.assertEqual([(r["asset_id"], r["volume_bbl"]) for r in spills["rows"]], [("WELL-0151", 5.4)])
        self.assertIn("4.5", flags(spills, "correction_applied")[0]["message"])
        comp = self.call("U-201", "compliance_check", asset_ids=["WELL-0151"])
        self.assertIn("SR-01", {r["protocol"] for r in comp["rows"]})
        self.assertIn("23 Nov 2026", " ".join(o["detail"] for o in comp["summary"]["open_items"] if o["protocol"] == "SR-03"))
        self.assertTrue(flags(comp, "report_outdated"), "MNT-0151-025 was based on the old estimate")

    # Q14
    def test_q14_scoped_rule_adds_t350_well_but_not_t200(self):
        before = self.call("U-201", "maintenance_escalation", area_id="NE-07", equipment_kind="pump_jack")
        self.assertEqual(sorted(ids(before)), ["WELL-0108", "WELL-0121", "WELL-0138"])
        self.call("U-201", "record_type_rule", well_type="T350", rule_name="rca_corrective_repairs", value=2, reason="team policy")
        after = self.call("U-201", "maintenance_escalation", area_id="NE-07", equipment_kind="pump_jack")
        self.assertEqual(sorted(ids(after)), ["WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"])
        self.assertNotIn("WELL-0102", ids(after))  # T200 with 2 repairs stays below its own threshold
        added = next(r for r in after["rows"] if r["asset_id"] == "WELL-0120")
        self.assertTrue(added["user_rule"])
        self.assertEqual((added["threshold"], added["manual_threshold"]), (2.0, 3.0))

    def test_q14_rule_is_visible_to_other_users(self):
        self.call("U-201", "record_type_rule", well_type="T350", rule_name="rca_corrective_repairs", value=2, reason="r")
        out = self.call("U-202", "maintenance_escalation", area_id="NE-07", equipment_kind="pump_jack")
        self.assertIn("WELL-0120", ids(out))

    # Q16
    def test_q16_shared_statement_is_visible_to_everyone_with_its_author(self):
        self.call("U-102", "record_well_statement", asset_id="WELL-0007", text="Shut in from 17 Oct for a 10-day planned workover",
                  valid_from="2026-10-17", valid_to="2026-10-27")
        out = self.call("U-201", "well_context", asset_id="WELL-0007")
        mine = [e for e in out["rows"][0]["user_entries"] if e.get("kind") == "note"]
        self.assertEqual(len(mine), 1)
        self.assertIn("17 Oct", mine[0]["text"])
        self.assertIn("U-102", str(mine[0]))  # attributed to its author

    def test_q16_private_memory_is_empty_for_another_user(self):
        con = self.con
        con.execute("INSERT INTO conversations (conversation_id, user_id, started_at, title) VALUES ('conv-x', 'U-201', '2026-10-15T09:00:00Z', 'Top downtime')")
        con.execute("INSERT INTO turns (conversation_id, user_id, role, text, asset_ids, created_at) VALUES "
                    "('conv-x', 'U-201', 'user', 'Top 3 downtime in NE-07', '[\"WELL-0121\",\"WELL-0138\",\"WELL-0107\"]', '2026-10-15T09:00:00Z')")
        con.commit()
        mine = self.call("U-201", "recall_my_conversations")
        theirs = self.call("U-202", "recall_my_conversations")
        self.assertEqual(mine["summary"]["conversations"], 1)
        self.assertEqual(theirs["summary"]["conversations"], 0)
        self.assertNotIn("WELL-0121", str(theirs))

    # Q17
    def test_q17_claim_against_the_data_is_flagged_and_stays_unconfirmed(self):
        out = self.call("U-204", "record_well_statement", asset_id="WELL-0175", text="WELL-0175 is running fine", claimed_status="producing")
        self.assertEqual(out["summary"]["recorded"]["kind"], "claim")
        msg = flags(out, "conflict")[0]["message"]
        self.assertIn("INS-0175-0204", msg)
        self.assertIn("0.4", msg)
        status = self.call("U-204", "well_status", asset_ids=["WELL-0175"])["rows"][0]
        self.assertEqual(status["status"], "not producing", "an unconfirmed claim must never override inspection data")

    def test_q17_claim_that_matches_the_data_is_not_a_conflict(self):
        out = self.call("U-201", "record_well_statement", asset_id="WELL-0175", text="Shut in for planned work since 14 Oct",
                        claimed_status="not_producing")
        self.assertFalse(flags(out, "conflict"))

    # Q18
    def test_q18_competing_notes_are_both_available(self):
        ctx = self.call("U-201", "well_context", asset_id="WELL-0101")
        refs = {e["ref"] for e in ctx["evidence"]}
        self.assertTrue({"CTX-0101-002", "CTX-0101-003"} <= refs, refs)

    def test_correction_input_validation(self):
        for bad in (dict(inspection_id="INS-9999-0001", field="wellhead_temp_c", new_value="41.0", reason="r"),
                    dict(inspection_id="INS-0164-0205", field="not_a_field", new_value="1", reason="r"),
                    dict(inspection_id="INS-0164-0205", field="wellhead_temp_c", new_value="hot", reason="r")):
            with self.assertRaises(Exception, msg=str(bad)):
                self.call("U-201", "record_correction", **bad)

    def test_original_record_is_never_mutated(self):
        before = self.con.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id='INS-0164-0205'").fetchone()[0]
        self.call("U-201", "record_correction", inspection_id="INS-0164-0205", field="wellhead_temp_c", new_value="41.0", reason="r")
        after = self.con.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id='INS-0164-0205'").fetchone()[0]
        self.assertEqual((before, after), (24.0, 24.0))


if __name__ == "__main__":
    unittest.main()
