"""Q9: which rules has a well broken in the last 12 months, and what is still open?

Deadlines come from the SOPs: SR-01 24h written notice (2h if major, >=25 bbl), SR-02 contain
within 4h, SR-03 agency verification within 60 days for reportable spills, RC-04 answer a
notice of violation within 30 days. RCA: >=3 corrective repairs on one equipment kind in 12 months.

Usage: python3 -I ground_truth/q9.py [WELL-0001]
"""
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import ground_truth as gt
import reports


def d(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def check(w, today=gt.TODAY, rca_threshold=3):
    start = today - timedelta(days=365)
    out = {"breaches": [], "open": [], "met": []}
    rep = reports.reports_for_well(f"{gt.ROOT}/assets/{w['asset_id']}", w["maintenance"])
    by_ins = {}
    for mid, f in rep.items():
        by_ins.setdefault(f["inspection_id"], []).append((mid, f))

    for s in gt.real_spills(w, start, today):
        vol = s["spill_est_volume_bbl"] or 0
        if vol < 5:
            continue
        iid, found = s["inspection_id"], gt.day(s)
        limit = 2 if vol >= 25 else 24
        ev = [e for e in w["regulatory"] if e.get("related_inspection_id") == iid]
        filed = [e for e in ev if e["event_type"] == "spill_report_filed"]
        if not filed:
            if today > found + timedelta(hours=limit):
                out["breaches"].append(("SR-01", iid, f"no written notice recorded, {limit}h limit"))
        elif filed[0]["hours_to_report"] > limit:
            out["breaches"].append(("SR-01", iid, f"reported after {filed[0]['hours_to_report']}h, limit {limit}h ({filed[0]['event_id']})"))
        for mid, f in by_ins.get(iid, []):
            if f["checks"].get("SR-02") == "Not met":
                out["breaches"].append(("SR-02", iid, f"contained after {f.get('contained_hours')}h, limit 4h ({mid})"))
        due = found + timedelta(days=60)
        verified = [e for e in ev if e["event_type"] == "site_inspection" and e["status"] == "closed"]
        if not verified:
            tag = "breaches" if today > due else "open"
            out[tag].append(("SR-03", iid, f"verification due {due}, not closed"))
        for nov in (e for e in ev if e["event_type"] == "notice_of_violation"):
            resp_due = d(nov["date"]) + timedelta(days=30)
            answered = any(e["event_type"] == "nov_response_filed" for e in w["regulatory"]
                           if d(e["date"]) >= d(nov["date"]))
            if not answered:
                tag = "breaches" if today > resp_due else "open"
                out[tag].append(("RC-04", nov["event_id"], f"response due {resp_due}, none recorded; fine {nov['fine_usd']} {nov['status']}"))

    ins = [gt.day(i) for i in w["inspections"] if gt.day(i) >= start]
    gaps = [(b - a).days for a, b in zip(ins, ins[1:])] + [(today - ins[-1]).days] if ins else []
    if gaps and max(gaps) > 14:
        out["breaches"].append(("RC-01", None, f"longest gap between inspections {max(gaps)} days"))
    yr = [m for m in w["maintenance"] if m["issue"] == "Annual flowline integrity test" and d(m["started_at"]).year == today.year]
    (out["met"] if yr else out["open"]).append(("RC-02", None, f"annual flowline test {'done ' + str(d(yr[0]['started_at'])) if yr else 'not yet done this year'}"))

    corr = Counter(m["equipment_kind"] for m in w["maintenance"]
                   if m["maintenance_type"] == "corrective" and start <= d(m["started_at"]) <= today)
    for kind, n in corr.items():
        if n >= rca_threshold:
            ids = [m["maintenance_id"] for m in w["maintenance"] if m["equipment_kind"] == kind and m["maintenance_type"] == "corrective" and start <= d(m["started_at"]) <= today]
            out["open"].append(("RCA", kind, f"{n} corrective repairs: {', '.join(ids)}"))
    for n in w["context"]:
        if n["note_type"] == "operator_feedback" and (n.get("valid_to") is None or n["valid_to"] >= str(today)):
            out["open"].append(("NOTE", n["note_id"], n["text"]))
    return out


if __name__ == "__main__":
    _, wells = gt.load()
    wid = sys.argv[1] if len(sys.argv) > 1 else "WELL-0001"
    r = check(wells[wid])
    for k in ("breaches", "open", "met"):
        print(k.upper())
        for row in r[k]:
            print("  ", row)
