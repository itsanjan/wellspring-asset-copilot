"""Smoke-check a loaded dataset without any model: run every read tool across every area and a sample of
wells, and report errors, timings and headline numbers to eyeball. Use right after loading new data.

    .venv/bin/python scripts/check_dataset.py [--db var/wellspring.db] [--wells 25]
Exit code 1 if any tool raised.
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wellspring import tools  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="var/wellspring.db")
    ap.add_argument("--wells", type=int, default=25, help="random wells to check individually")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    ctx = tools.Context(con, "U-201")
    today = ctx.today
    errors, timings = [], {}

    def run(name, **kw):
        t = time.time()
        try:
            return tools.run(ctx, name, kw)
        except Exception as e:  # report every failure, keep going
            errors.append(f"{name}({kw}): {type(e).__name__}: {e}")
            return None
        finally:
            timings.setdefault(name, []).append(time.time() - t)

    print(f"today = {today}")
    year_start = f"{today.year}-01-01"
    for (area,) in con.execute("SELECT area_id FROM areas ORDER BY 1"):
        st = run("well_status", area_id=area, only_not_producing=True)
        dt = run("downtime", area_id=area, start=(today - timedelta(days=122)).isoformat(), top_n=3)
        sp = run("spills", area_id=area, start=(today - timedelta(days=90)).isoformat())
        run("repairs", area_id=area, start=year_start)
        rca = run("maintenance_escalation", area_id=area)
        cc = run("compliance_check", area_id=area)
        if all([st, dt, sp, rca, cc]):
            top = ", ".join(f"{r['asset_id']} {r['downtime_days']}d" for r in dt["rows"])
            print(f"  {area}: {st['summary']['not_producing']} not producing now | top downtime {top} | "
                  f"{sp['summary']['spills']} spills in 90 days | {rca['summary']['needs_rca']} RCA | {cc['summary']['breaches']} breaches (12 months)")
    hm = run("spills", start=year_start, group_by="area_month")
    if hm:
        print(f"  {today.year} spills by area: " + ", ".join(f"{a}={sum(m.values())}" for a, m in hm["summary"].get("by_area_month", {}).items()))
    wells = [w for (w,) in con.execute("SELECT asset_id FROM wells")]
    for w in random.Random(7).sample(wells, min(args.wells, len(wells))):
        run("well_status", asset_ids=[w])
        run("inspection_history", asset_id=w, last_n=13)
        run("well_context", asset_id=w)
        run("compliance_check", asset_ids=[w])
        run("spills", asset_ids=[w])
    run("search_docs", query="spill reporting deadline")
    run("read_doc_section", doc_id=con.execute("SELECT doc_id FROM doc_chunks WHERE doc_kind='manual' LIMIT 1").fetchone()[0])
    # Tools for operator notes and the extra documents: they return empty results on data without them.
    for name, kw in [("gas_reports", {}), ("bypasses", {}), ("status_conflicts", {}), ("operator_concerns", {}), ("recalled_pumps", {}),
                     ("freeze_checks", {}), ("shut_in_delays", {}), ("temperature_by_area", {}), ("data_coverage", {}), ("production_proxy", {})]:
        r = run(name, **kw)
        if r:
            print(f"  {name}: {len(r['rows'])} rows")
    if con.execute("SELECT COUNT(*) FROM letters").fetchone()[0]:
        run("letter_compliance")
    team = con.execute("SELECT name FROM teams LIMIT 1").fetchone()[0]
    run("team_info", team=team)
    unknown = con.execute("SELECT COUNT(*) FROM v_inspection WHERE producing IS NULL").fetchone()[0]
    if unknown:
        print(f"  WARNING: {unknown} inspections have unknown producing status (well type without a parsed threshold)")
    print("timings (max s): " + ", ".join(f"{k} {max(v):.2f}" for k, v in sorted(timings.items())))
    for e in errors:
        print("ERROR:", e)
    print(f"{sum(len(v) for v in timings.values())} tool calls, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
