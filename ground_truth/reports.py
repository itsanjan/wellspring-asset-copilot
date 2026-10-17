"""Parse structured facts out of free-text maintenance reports.

The compliance table at the end of each report is regular, so plain regexes are enough;
an LLM is only needed for prose that has no table row. Always follow `report_file` from
maintenance.jsonl (file names vary).
"""
import os
import re

ROW = re.compile(r"^\|\s*(?P<proto>[A-Z]{2}-\d\d)\s*\|\s*(?P<result>[^|]+?)\s*\|\s*(?P<detail>[^|]*?)\s*\|\s*$", re.M)
CONTAINED = re.compile(r"Contained about ([\d.]+) hours? after discovery \(limit ([\d.]+) hours?\)")
TRIGGER = re.compile(r"\*\*Triggered by:\*\*\s*inspection (INS-\d+-\d+)")


def parse_report(path):
    text = open(path).read()
    rows = {m["proto"]: (m["result"].replace("*", "").strip(), m["detail"]) for m in ROW.finditer(text)}
    facts = {"checks": {p: r for p, (r, _) in rows.items()}, "path": path}
    t = TRIGGER.search(text)
    facts["inspection_id"] = t.group(1) if t else None
    sr02 = rows.get("SR-02")
    if sr02:
        m = CONTAINED.search(sr02[1])
        if m:
            facts["contained_hours"] = float(m.group(1))
            facts["containment_limit_hours"] = float(m.group(2))
    return facts


def reports_for_well(well_dir, maintenance):
    """Map maintenance_id -> parsed facts, following report_file."""
    out = {}
    for m in maintenance:
        rf = m.get("report_file")
        if not rf:
            continue
        p = rf if os.path.isabs(rf) else os.path.join(well_dir, rf)
        if not os.path.exists(p):
            p = os.path.join(well_dir, "maintenance-reports", os.path.basename(rf))
        if os.path.exists(p):
            out[m["maintenance_id"]] = parse_report(p)
    return out
