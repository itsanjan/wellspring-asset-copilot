"""Run the sample questions through the copilot and check each answer for the expected facts and visual.

    .venv/bin/python scripts/run_eval.py [--only Q1,Q5] [--db var/wellspring.db]

A fact passes if any of its "|"-separated alternatives appears in the answer text or the visual's data.
Results go to var/eval-<provider>-<model>.json and a summary table is printed. Single-turn questions only
(Q1-Q10); the memory and multi-user scenarios (Q11-Q18) need scripted conversations.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wellspring.agent import Copilot  # noqa: E402

CASES = [
    ("Q1", "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show me its status card.",
     ["not producing", "INS-0001-0203", "Ryan Brooks", "CTX-0001-003", "3.2"], "card"),
    ("Q2", "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.",
     ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175", "MNT-0170-031", "MNT-0172-028"], "table"),
    ("Q3", "Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks, and mark when it was not producing oil.",
     ["2026-10-08|8 Oct|Oct 8|October 8", "2026-10-15|15 Oct|Oct 15|October 15", "2026-07-23|23 Jul|Jul 23|July 23"], "line"),
    ("Q4", "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? List them in a table.",
     ["6 spills|six spills|6 real spills|6 oil spills", "15.1", "INS-0001-0070|rainwater"], "table"),
    ("Q5", "How many days was WELL-0001 down in 2025?", ["21 days|21", "MNT-0001-021", "MNT-0001-023"], None),
    ("Q6", "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.",
     ["WELL-0121", "49", "WELL-0138", "WELL-0107", "42"], "bar"),
    ("Q7", "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.",
     ["WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"], "map"),
    ("Q8", "Show a heatmap of 2026 spills by geographic area and month.", ["43", "35", "37", "26"], "heatmap"),
    ("Q9", "Which rules has WELL-0001 broken in the last 12 months, and what is still open?",
     ["SR-01", "SR-02", "SR-03", "RC-04", "5,000|5000"], None),
    ("Q10", "In Southwest Block 24, which spills in the last 90 days were not contained within the SOP time limit?",
     ["WELL-0082", "WELL-0100", "7.1", "8.1"], None),
]


def check(answer, facts, visual):
    hay = (answer["text"] + " " + json.dumps(answer.get("visual") or {})).lower()
    got = {f: any(alt.lower() in hay for alt in f.split("|")) for f in facts}
    vt = (answer.get("visual") or {}).get("type")
    return got, (visual is None or vt == visual), vt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="var/wellspring.db")
    ap.add_argument("--user", default="U-201")
    ap.add_argument("--only", help="comma-separated case IDs")
    args = ap.parse_args()
    bot = Copilot(args.db)
    only = set(args.only.split(",")) if args.only else None
    out, rows = [], []
    for cid, q, facts, visual in CASES:
        if only and cid not in only:
            continue
        t = time.time()
        try:
            a = bot.ask(args.user, q)  # each question in a fresh conversation
            got, vis_ok, vt = check(a, facts, visual)
            err = None
        except Exception as e:  # keep going: one failure should not stop the run
            a, got, vis_ok, vt, err = {"text": "", "usage": {}}, {f: False for f in facts}, False, None, f"{type(e).__name__}: {e}"
        secs = round(time.time() - t, 1)
        score = sum(got.values())
        rows.append((cid, f"{score}/{len(facts)}", "ok" if vis_ok else f"got {vt}, want {visual}", secs,
                     ", ".join(f for f, ok in got.items() if not ok) or "-"))
        out.append({"id": cid, "question": q, "facts": got, "visual_expected": visual, "visual_got": vt, "seconds": secs,
                    "error": err, "answer": a.get("text"), "tools": [t["tool"] for t in a.get("tools_used", [])], "usage": a.get("usage")})
        print(f"{cid}: facts {score}/{len(facts)}, visual {'ok' if vis_ok else vt}, {secs}s", flush=True)
    b = bot.backend
    suffix = ("-" + "-".join(sorted(only))) if only else ""  # partial runs never overwrite the full results
    path = f"var/eval-{b.provider}-{b.model.replace('/', '_').replace(':', '_')}{suffix}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n| Case | Facts | Visual | Seconds | Missing |\n|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")
    total = sum(sum(o["facts"].values()) for o in out), sum(len(o["facts"]) for o in out)
    print(f"\nFacts: {total[0]}/{total[1]}  Visuals: {sum(1 for o in out if o['visual_expected'] in (None, o['visual_got']))}/{len(out)}  -> {path}")


if __name__ == "__main__":
    main()
