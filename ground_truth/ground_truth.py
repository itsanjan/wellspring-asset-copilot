"""Ground truth: recomputes the sample answers (Q1-Q8) straight from the raw files.

No LLM involved. Rules come from the manuals/SOPs:
  producing  <=> wellhead - ambient >= 15 (T200) / 20 (T350)      [T200 s9.3, T350 s3]
  downtime   = 7 days per non-producing inspection                 [dataset guide]
  corrected detections (context.jsonl note_type=correction) are not spills [SR SOP s7]

Usage: python3 -I ground_truth/ground_truth.py [path/to/sample-data]
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "sample-data")
TODAY = date(2026, 10, 16)
THRESHOLD = {"T200": 15.0, "T350": 20.0, "T500": 25.0}  # T500: OM-T500 section 3


def jl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load():
    areas = {a["area_id"]: a for a in json.load(open(f"{ROOT}/fleet/areas.json"))["areas"]}
    wells = {}
    for d in sorted(glob.glob(f"{ROOT}/assets/WELL-*")):
        meta = json.load(open(f"{d}/metadata.json"))
        w = dict(meta)
        w["inspections"] = sorted(jl(f"{d}/inspections.jsonl"), key=lambda r: r["inspected_at"])
        w["maintenance"] = jl(f"{d}/maintenance.jsonl")
        w["regulatory"] = jl(f"{d}/regulatory-events.jsonl")
        w["context"] = jl(f"{d}/context.jsonl")
        w["corrected"] = {n["inspection_id"] for n in w["context"] if n["note_type"] == "correction"}
        wells[meta["asset_id"]] = w
    return areas, wells


def day(ins):
    return datetime.fromisoformat(ins["inspected_at"].replace("Z", "+00:00")).date()


def delta(w, ins):
    return round(ins["wellhead_temp_c"] - ins["ambient_temp_c"], 1)


def producing(w, ins):
    return delta(w, ins) >= THRESHOLD[w["well_type"]]


def real_spills(w, start, end):
    return [i for i in w["inspections"]
            if i["spill_detected"] and i["inspection_id"] not in w["corrected"] and start <= day(i) <= end]


def downtime_days(w, start, end):
    return 7 * sum(1 for i in w["inspections"] if start <= day(i) <= end and not producing(w, i))


def main():
    areas, wells = load()
    w1 = wells["WELL-0001"]
    print(f"wells={len(wells)} inspections={sum(len(w['inspections']) for w in wells.values())}")

    print("\nQ1 WELL-0001 latest")
    i = w1["inspections"][-1]
    print(i["inspection_id"], i["inspected_at"], i["method"], i["inspector_user_id"],
          "delta", delta(w1, i), "producing", producing(w1, i), "recorded", w1["recorded_status"])

    print("\nQ2 NE-08 not producing now")
    for wid in areas["NE-08"]["asset_ids"]:
        w = wells[wid]
        last = w["inspections"][-1]
        if not producing(w, last):
            print(wid, w["well_type"], day(last), delta(w, last), w["recorded_status"], repr(last["notes"]))

    print("\nQ3 WELL-0001 last 13 readings")
    last13 = w1["inspections"][-13:]
    print(len(last13), day(last13[0]), day(last13[-1]), [str(day(x)) for x in last13 if not producing(w1, x)])

    print("\nQ4 WELL-0001 spills")
    sp = real_spills(w1, date(2022, 10, 20), TODAY)
    print(len(sp), round(sum(s["spill_est_volume_bbl"] for s in sp), 1),
          [(str(day(s)), s["spill_est_volume_bbl"]) for s in sp])

    print("\nQ5 WELL-0001 downtime 2025:", downtime_days(w1, date(2025, 1, 1), date(2025, 12, 31)))

    print("\nQ6 NE-07 top 3 downtime, 16 Jun-16 Oct 2026")
    dt = sorted(((downtime_days(wells[x], date(2026, 6, 16), TODAY), x, wells[x]["well_type"])
                 for x in areas["NE-07"]["asset_ids"]), reverse=True)
    print(dt[:4])

    print("\nQ7 NE-08 real spills, last 90 days")
    start = TODAY - timedelta(days=90)
    for wid in areas["NE-08"]["asset_ids"]:
        for s in real_spills(wells[wid], start, TODAY):
            print(wid, day(s), s["spill_est_volume_bbl"], wells[wid]["location"])

    print("\nQ8 2026 spills by area x month")
    for aid, a in areas.items():
        row = defaultdict(int)
        for wid in a["asset_ids"]:
            for s in real_spills(wells[wid], date(2026, 1, 1), TODAY):
                row[day(s).month] += 1
        print(aid, [row[m] for m in range(1, 11)], sum(row.values()))


if __name__ == "__main__":
    main()
