"""Q10: spills in an area, last 90 days, not contained within the SR-02 limit (from repair reports)."""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as gt
import reports

areas, wells = gt.load()
start = gt.TODAY - timedelta(days=90)
area = sys.argv[1] if len(sys.argv) > 1 else "SW-24"

parsed = missing = 0
for wid, w in wells.items():
    rep = reports.reports_for_well(f"{gt.ROOT}/assets/{wid}", w["maintenance"])
    for f in rep.values():
        if "SR-02" in f["checks"]:
            parsed += 1
            missing += "contained_hours" not in f
print(f"SR-02 rows={parsed}, without parsable hours={missing}")

print(f"\nQ10 {area}: spills since {start} not contained within limit")
for wid in areas[area]["asset_ids"]:
    w = wells[wid]
    spills = {s["inspection_id"]: s for s in gt.real_spills(w, start, gt.TODAY)}
    for mid, f in reports.reports_for_well(f"{gt.ROOT}/assets/{wid}", w["maintenance"]).items():
        if f["inspection_id"] in spills and f.get("contained_hours", 0) > f.get("containment_limit_hours", 4):
            print(wid, f["inspection_id"], mid, f["contained_hours"], "h vs", f["containment_limit_hours"])
