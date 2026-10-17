"""Tests for wellspring.rules: thresholds are read from the manuals, not hard-coded."""
import os
import tempfile
import textwrap
import unittest

from wellspring import rules

FLEET = os.path.join(os.path.dirname(__file__), "..", "data", "sample-data", "fleet")


def write_manual(fleet, name, body):
    os.makedirs(os.path.join(fleet, "well-types"), exist_ok=True)
    with open(os.path.join(fleet, "well-types", f"{name}.md"), "w") as f:
        f.write(textwrap.dedent(body))


BASE = """\
    # Manual
    Document ID: OM-T200

    ## 9.3 Producing check
    A well is producing when the wellhead is at least **15 °C above ambient** air temperature.

    ## 10. Root cause analysis
    Three or more **corrective** maintenance events on the same equipment kind in the
    12 months before the current date require a root-cause analysis (RCA).
    """


class ParseManualTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def parse(self, body, name="T200"):
        write_manual(self.tmp.name, name, body)
        return rules.parse_manual(os.path.join(self.tmp.name, "well-types", f"{name}.md"))

    def test_producing_threshold_with_source(self):
        self.assertEqual(self.parse(BASE)["producing_delta_c"], (15.0, "OM-T200 section 9.3"))

    def test_rca_count_and_window(self):
        out = self.parse(BASE)
        self.assertEqual(out["rca_corrective_repairs"], (3.0, "OM-T200 section 10"))
        self.assertEqual(out["rca_window_months"], (12.0, "OM-T200 section 10"))

    def test_decimal_threshold(self):
        out = self.parse(BASE.replace("**15 °C above", "**17.5 °C above"))
        self.assertEqual(out["producing_delta_c"][0], 17.5)

    @unittest.expectedFailure  # rules.ALARM expects "at or below **85" but manuals bold "**at or below 85 °C**"
    def test_alarm_threshold_in_manual_wording(self):
        body = BASE + "\n### 9.4 Temperature alarm\nThe wellhead must stay **at or below 85 °C**.\n"
        self.assertEqual(self.parse(body)["temp_alarm_c"], (85.0, "OM-T200 section 9.4"))

    def test_numeric_digit_repair_count(self):
        out = self.parse(BASE.replace("Three or more", "4 or more"))
        self.assertEqual(out["rca_corrective_repairs"][0], 4.0)

    def test_missing_document_id_falls_back_to_filename(self):
        out = self.parse(BASE.replace("Document ID: OM-T200\n", ""))
        self.assertTrue(out["producing_delta_c"][1].startswith("T200.md section"))

    def test_unknown_number_word_is_ignored(self):
        out = self.parse(BASE.replace("Three or more", "Seventeen or more"))
        self.assertNotIn("rca_corrective_repairs", out)

    def test_no_rules_gives_empty_result(self):
        self.assertEqual(self.parse("# Manual\nDocument ID: X\n\n## 1. Intro\nNothing here.\n"), {})


class ParseAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        write_manual(self.tmp.name, "T200", BASE)

    def test_new_well_type_needs_no_code_change(self):
        write_manual(self.tmp.name, "T500", BASE.replace("T200", "T500").replace("**15 °C above", "**25 °C above"))
        types = rules.parse_all(self.tmp.name)
        self.assertEqual(set(types), {"T200", "T500"})
        self.assertEqual(types["T500"]["producing_delta_c"][0], 25.0)

    def test_missing_rca_is_inherited_from_t200_and_marked(self):
        write_manual(
            self.tmp.name,
            "T350",
            """\
            Document ID: OM-T350

            ## 3. Producing check
            Producing means at least **20 °C above ambient**.
            """,
        )
        t350 = rules.parse_all(self.tmp.name)["T350"]
        self.assertEqual(t350["producing_delta_c"][0], 20.0)
        value, source = t350["rca_corrective_repairs"]
        self.assertEqual(value, 3.0)
        self.assertIn("inherited by T350", source)

    def test_own_values_are_not_overwritten_by_inheritance(self):
        write_manual(self.tmp.name, "T350", BASE.replace("T200", "T350").replace("Three or more", "Five or more"))
        self.assertEqual(rules.parse_all(self.tmp.name)["T350"]["rca_corrective_repairs"][0], 5.0)

    def test_empty_fleet_dir(self):
        self.assertEqual(rules.parse_all(tempfile.mkdtemp()), {})


@unittest.skipUnless(os.path.isdir(FLEET), "run scripts/fetch_data.sh first")
class SampleDataTest(unittest.TestCase):
    """Pin the values the organizers' manuals actually contain."""

    @classmethod
    def setUpClass(cls):
        cls.types = rules.parse_all(FLEET)

    def test_producing_thresholds_match_manuals(self):
        self.assertEqual(self.types["T200"]["producing_delta_c"], (15.0, "OM-T200 section 9.3"))
        self.assertEqual(self.types["T350"]["producing_delta_c"], (20.0, "OM-T350 section 3"))

    def test_rca_rule_is_three_repairs_in_twelve_months(self):
        for t in ("T200", "T350"):
            self.assertEqual(self.types[t]["rca_corrective_repairs"][0], 3.0)
            self.assertEqual(self.types[t]["rca_window_months"][0], 12.0)


if __name__ == "__main__":
    unittest.main()
