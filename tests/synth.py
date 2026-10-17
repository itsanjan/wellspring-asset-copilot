"""Build perturbed and synthetic datasets in the challenge format, to test generalization
before the organizers' test data arrives."""
import json
import os
import re
import shutil
import tempfile

SAMPLE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "sample-data"))


def copy_sample():
    dst = os.path.join(tempfile.mkdtemp(), "perturbed")
    shutil.copytree(SAMPLE, dst)
    return dst


def set_threshold(root, well_type, degrees):
    """Change the 'at least **N °C above ambient**' rule in a well-type manual."""
    path = os.path.join(root, "fleet", "well-types", f"{well_type}.md")
    text = open(path).read()
    new, n = re.subn(r"(at least\s+\*\*)\d+(?:\.\d+)?(\s*°C\s+above)", rf"\g<1>{degrees}\g<2>", text, count=1)
    assert n == 1, f"no producing rule found in {path}"
    open(path, "w").write(new)


def add_well_type(root, name, degrees):
    path = os.path.join(root, "fleet", "well-types", f"{name}.md")
    open(path, "w").write(
        f"# Operations Manual {name}\n\nDocument ID: OM-{name}\n\n## 3. Production temperature rule\n\n"
        f"A {name} well is **producing (active)** only when its wellhead temperature is at least **{degrees} °C above\n"
        "ambient** air temperature at the time of the reading.\n\n## 6. Root cause analysis\n\n"
        "Same as T200 section 10 (RCA after three corrective events on the same equipment kind within 12 months).\n")


def inspection(well, n, when, ambient, wellhead, **kw):
    r = {"inspection_id": f"INS-{well[5:]}-{n:04d}", "asset_id": well, "inspected_at": when, "inspector_user_id": "U-104",
         "method": "drone", "ambient_temp_c": ambient, "wellhead_temp_c": wellhead, "spill_detected": False,
         "spill_area_pct": None, "spill_est_volume_bbl": None, "evidence_images": [], "notes": ""}
    r.update(kw)
    return r


def add_well(root, well, well_type, area_id, inspections, files=("inspections.jsonl",)):
    d = os.path.join(root, "assets", well)
    os.makedirs(d, exist_ok=True)
    json.dump({"asset_id": well, "name": f"{area_id} Well {well[-3:]}", "asset_category": "oil_well", "well_type": well_type,
               "area_id": area_id, "team_id": "TEAM-NE", "in_charge_user_id": "U-101",
               "location": {"lat": 32.0, "lon": -102.0}, "installed_on": "2024-01-01",
               "equipment": [{"equipment_id": f"{well}-WH", "kind": "wellhead", "model": "WH-3000"}],
               "last_inspection": {"inspection_id": inspections[-1]["inspection_id"], "inspected_at": inspections[-1]["inspected_at"],
                                   "inspector_user_id": "U-104"},
               "recorded_status": "active"}, open(os.path.join(d, "metadata.json"), "w"))
    with open(os.path.join(d, "inspections.jsonl"), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in inspections)
    p = os.path.join(root, "fleet", "areas.json")
    areas = json.load(open(p))
    for a in areas["areas"]:
        if a["area_id"] == area_id:
            a["asset_ids"].append(well)
    json.dump(areas, open(p, "w"))


def extra_only_dataset(well="WELL-0201", well_type="T500", area_id="NE-08", degrees=25):
    """A tiny second dataset (fleet + one new well of a new type) to load next to the sample."""
    root = os.path.join(tempfile.mkdtemp(), "extra")
    shutil.copytree(os.path.join(SAMPLE, "fleet"), os.path.join(root, "fleet"))
    add_well_type(root, well_type, degrees)
    add_well(root, well, well_type, area_id, [
        inspection(well, 1, "2026-10-01T08:00:00Z", 20.0, 46.0),   # +26 -> producing
        inspection(well, 2, "2026-10-08T08:00:00Z", 20.0, 44.0),   # +24 -> not producing under a 25 rule
        inspection(well, 3, "2026-10-15T08:00:00Z", 20.0, 45.0),   # +25 -> producing (boundary is inclusive)
    ])
    return root
