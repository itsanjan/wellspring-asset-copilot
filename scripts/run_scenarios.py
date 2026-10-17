"""Run the memory, feedback and multi-user scenarios (Q11-Q18) through the copilot, in order, on a fresh database.

    .venv/bin/python scripts/run_scenarios.py [--db var/scenarios.db]

Each step is one user message (optionally in a new conversation). A step can check:
  facts    "|"-separated alternatives that must appear in the answer text or visual data
  absent   strings that must NOT appear in the visual data (e.g. a well that should drop out of a list)
  db       SQL that must return at least one row afterwards (e.g. the correction was stored with its original)
Results go to var/scenarios-<provider>-<model>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wellspring import loader  # noqa: E402
from wellspring.agent import Copilot  # noqa: E402

MICHAEL, JESSICA, TYLER, JAKE, LAURA = "U-201", "U-202", "U-204", "U-102", "U-401"
Q2 = "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table."
RCA_Q = "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?"

# (scenario, user, message, new_conversation, checks)
STEPS = [
    ("Q11", MICHAEL, "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.", True,
     {"facts": ["WELL-0121", "WELL-0138", "WELL-0107"]}),
    ("Q11", MICHAEL, "For the three wells we discussed yesterday, what have repairs cost this year?", True,
     {"facts": ["25,794|25794", "46,942|46942", "5,176|5176"]}),
    ("Q12", MICHAEL, "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; "
                     "the field crew re-measured the wellhead at 41.0 °C.", True,
     {"facts": ["producing", "pump jack not moving"],
      "db": "SELECT 1 FROM overlay_entries WHERE kind='correction' AND target_ref='INS-0164-0205' AND field='wellhead_temp_c' "
            "AND CAST(new_value AS REAL)=41.0 AND CAST(original_value AS REAL)=24.0 AND author_user_id='U-201' AND status='active'"}),
    ("Q12", MICHAEL, Q2, False, {"facts": ["WELL-0170", "WELL-0172", "WELL-0175"], "absent": ["WELL-0164"]}),
    ("Q13", MICHAEL, "The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 bbl.", True,
     {"facts": ["reportable", "SR-01", "23 Nov|2026-11-23|November 23"],
      "db": "SELECT 1 FROM overlay_entries WHERE kind='correction' AND target_ref='INS-0151-0201' AND field='spill_est_volume_bbl' "
            "AND CAST(new_value AS REAL)=5.4 AND CAST(original_value AS REAL)=4.5"}),
    ("Q14", MICHAEL, RCA_Q, True, {"facts": ["WELL-0108", "WELL-0121", "WELL-0138"], "absent": ["WELL-0120"]}),
    ("Q14", MICHAEL, "Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, not 3.", False,
     {"db": "SELECT 1 FROM overlay_entries WHERE kind='rule' AND target_ref='T350' AND field='rca_corrective_repairs' AND CAST(new_value AS REAL)=2"}),
    ("Q14", MICHAEL, RCA_Q, True, {"facts": ["WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138", "Michael Turner|your rule|user rule|team"],
                                   "absent": ["WELL-0102"]}),
    ("Q15", JESSICA, "Is WELL-0164 producing oil right now?", True, {"facts": ["producing", "41", "Michael Turner"]}),
    ("Q15", LAURA, "Which spills in Northeast Block 8 in the last 90 days needed a report to the regulator?", True,
     {"facts": ["WELL-0151", "5.4"]}),
    ("Q16", JAKE, "FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.", True,
     {"db": "SELECT 1 FROM overlay_entries WHERE target_ref='WELL-0007' AND kind IN ('note','claim') AND author_user_id='U-102'"}),
    ("Q16", MICHAEL, "Is any downtime planned for WELL-0007?", True, {"facts": ["17 Oct|October 17|2026-10-17", "Jake Morales"]}),
    ("Q16", JESSICA, "For the three wells we discussed yesterday, what have repairs cost this year?", True,
     {"facts": ["no earlier|no previous|no prior|don't have|do not have|no record|couldn't find|could not find|not find|no conversation"],
      "absent_text": ["25,794", "46,942", "WELL-0121"]}),
    ("Q17", MICHAEL, "WELL-0175 has been shut in for planned work since 14 Oct.", True,
     {"db": "SELECT 1 FROM overlay_entries WHERE target_ref='WELL-0175' AND author_user_id='U-201' AND status='active'"}),
    ("Q17", TYLER, "WELL-0175 is running fine.", True,
     {"facts": ["0.4", "not producing", "unconfirmed|confirm"],
      "db": "SELECT 1 FROM overlay_entries WHERE target_ref='WELL-0175' AND author_user_id='U-204' AND kind='claim' AND status='unconfirmed'"}),
    ("Q17", JAKE, "WELL-0001 is back online since 14 Oct.", True,
     {"facts": ["not producing", "CTX-0001-003|20 Oct|October 20"],
      "db": "SELECT 1 FROM overlay_entries WHERE target_ref='WELL-0001' AND author_user_id='U-102' AND kind='claim' AND status='unconfirmed'"}),
    ("Q18", MICHAEL, "Was the 20 Aug spill at WELL-0101 real oil?", True,
     {"facts": ["yes|real oil|was crude|is crude|confirmed crude", "1.0", "Ryan Brooks", "Chris Dalton"]}),
]


def run_step(bot, db, conv, user, msg, new, checks):
    a = bot.ask(user, msg, None if new else conv.get(user))
    conv[user] = a["conversation_id"]
    text = a["text"].lower()
    vis = json.dumps(a.get("visual") or {}).lower()
    results = {}
    for f in checks.get("facts", []):
        results[f"has: {f}"] = any(alt.lower() in text + " " + vis for alt in f.split("|"))
    for s in checks.get("absent", []):
        results[f"not in visual: {s}"] = s.lower() not in vis
    for s in checks.get("absent_text", []):
        results[f"not in answer: {s}"] = s.lower() not in text + " " + vis
    if "db" in checks:
        con = sqlite3.connect(db)
        results["stored"] = bool(con.execute(checks["db"]).fetchone())
        con.close()
    return a, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="var/scenarios.db")
    ap.add_argument("--data", default="data/sample-data")
    args = ap.parse_args()
    if os.path.exists(args.db):
        os.remove(args.db)  # fresh: no earlier corrections or conversations
    loader.load([args.data], args.db)
    bot, conv, out = Copilot(args.db), {}, []
    for sid, user, msg, new, checks in STEPS:
        t = time.time()
        try:
            a, res = run_step(bot, args.db, conv, user, msg, new, checks)
            err = None
        except Exception as e:  # record and continue
            a, res, err = {"text": "", "tools_used": [], "usage": {}}, {"error": False}, f"{type(e).__name__}: {e}"
        secs = round(time.time() - t, 1)
        ok = sum(res.values())
        print(f"{sid} {user}: {ok}/{len(res)} {secs}s  {msg[:60]}", flush=True)
        out.append({"scenario": sid, "user": user, "message": msg, "checks": res, "seconds": secs, "error": err,
                    "answer": a.get("text"), "tools": [t["tool"] for t in a.get("tools_used", [])],
                    "visual": (a.get("visual") or {}).get("type"), "usage": a.get("usage")})
    b = bot.backend
    path = f"var/scenarios-{b.provider}-{b.model.replace('/', '_').replace(':', '_')}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n| Scenario | User | Checks | Failed |\n|---|---|---|---|")
    for o in out:
        failed = ", ".join(k for k, v in o["checks"].items() if not v) or "-"
        print(f"| {o['scenario']} | {o['user']} | {sum(o['checks'].values())}/{len(o['checks'])} | {failed} |")
    print(f"\nChecks: {sum(sum(o['checks'].values()) for o in out)}/{sum(len(o['checks']) for o in out)} -> {path}")


if __name__ == "__main__":
    main()
