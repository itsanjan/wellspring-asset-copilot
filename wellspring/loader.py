"""Load one or more datasets in the challenge format into a SQLite database.

    python3 -m wellspring.loader --data data/sample-data [--data data/test-data] --db var/wellspring.db

The load is a full rebuild, so it is idempotent. Passing several --data folders loads them
side by side (later folders add wells, or replace records with the same ID).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys

from . import reports, rules

SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")
# User state that must survive a dataset reload (data tables are rebuilt from scratch).
PRESERVE = ("overlay_entries", "conversations", "turns")


def jl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def insert(con, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def chunk_markdown(text):
    """Split a markdown doc into (section, heading, body) chunks by ##/### headings."""
    parts = re.split(r"^(#{1,3})\s+(.*)$", text, flags=re.M)
    chunks = [("0", "(intro)", parts[0].strip())] if parts[0].strip() else []
    for i in range(1, len(parts), 3):
        heading = parts[i + 1].strip()
        m = re.match(r"(\d+(?:\.\d+)*)\.?\s", heading)
        body = parts[i + 2].strip()
        if body:
            chunks.append((m.group(1) if m else heading, heading, body))
    return chunks


def add_doc(con, kind, doc_id, path, text, asset_id=None):
    for section, heading, body in chunk_markdown(text):
        insert(con, "doc_chunks", {"doc_kind": kind, "doc_id": doc_id, "asset_id": asset_id,
                                   "section": section, "heading": heading, "path": path, "text": body})


def load_fleet(con, root, stats):
    fleet = os.path.join(root, "fleet")
    areas = json.load(open(f"{fleet}/areas.json"))
    for t in areas.get("teams", []):
        insert(con, "teams", t)
    for a in areas["areas"]:
        insert(con, "areas", {"area_id": a["area_id"], "name": a["name"], "team_id": a.get("team_id"),
                              "center_lat": a.get("center", {}).get("lat"), "center_lon": a.get("center", {}).get("lon")})
    for u in json.load(open(f"{fleet}/users.json"))["users"]:
        insert(con, "users", u)

    for t, params in rules.parse_all(fleet).items():
        for name, (value, source) in params.items():
            insert(con, "rule_params", {"well_type": t, "name": name, "value": value, "source": source})
    for name, (value, source) in rules.parse_sops(fleet).items():
        insert(con, "rule_params", {"well_type": "*", "name": name, "value": value, "source": source})
    for md in sorted(glob.glob(f"{fleet}/well-types/*.md") + glob.glob(f"{fleet}/sops/*.md")):
        text = open(md).read()
        m = re.search(r"Document ID:\s*(\S+)", text)
        kind = "manual" if "well-types" in md else "sop"
        add_doc(con, kind, m.group(1) if m else os.path.basename(md), os.path.relpath(md, root), text)
        stats["documents"] += 1


def load_well(con, root, well_dir, stats):
    meta = json.load(open(f"{well_dir}/metadata.json"))
    wid = meta["asset_id"]
    loc, last = meta.get("location", {}), meta.get("last_inspection", {})
    insert(con, "wells", {
        "asset_id": wid, "name": meta.get("name"), "well_type": meta["well_type"], "area_id": meta.get("area_id"),
        "team_id": meta.get("team_id"), "in_charge_user_id": meta.get("in_charge_user_id"),
        "lat": loc.get("lat"), "lon": loc.get("lon"), "installed_on": meta.get("installed_on"),
        "recorded_status": meta.get("recorded_status"), "last_inspection_id": last.get("inspection_id"),
        "source": os.path.relpath(root)})
    for e in meta.get("equipment", []):
        insert(con, "equipment", {"equipment_id": e["equipment_id"], "asset_id": wid, "kind": e.get("kind"), "model": e.get("model")})

    for r in jl(f"{well_dir}/inspections.jsonl"):
        insert(con, "inspections", {
            "inspection_id": r["inspection_id"], "asset_id": wid, "inspected_at": r["inspected_at"],
            "inspector_user_id": r.get("inspector_user_id"), "method": r.get("method"),
            "ambient_temp_c": r.get("ambient_temp_c"), "wellhead_temp_c": r.get("wellhead_temp_c"),
            "spill_detected": int(bool(r.get("spill_detected"))), "spill_area_pct": r.get("spill_area_pct"),
            "spill_est_volume_bbl": r.get("spill_est_volume_bbl"),
            "evidence_images": json.dumps(r.get("evidence_images") or []), "notes": r.get("notes")})
        stats["inspections"] += 1

    maint = jl(f"{well_dir}/maintenance.jsonl")
    for r in maint:
        c = r.get("cost_usd") or {}
        insert(con, "maintenance", {
            "maintenance_id": r["maintenance_id"], "asset_id": wid, "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"), "maintenance_type": r.get("maintenance_type"),
            "equipment_kind": r.get("equipment_kind"), "issue": r.get("issue"), "action_taken": r.get("action_taken"),
            "technician_user_id": r.get("technician_user_id"), "contractor": r.get("contractor"),
            "downtime_hours": r.get("downtime_hours"), "cost_labor": c.get("labor"), "cost_parts": c.get("parts"),
            "cost_total": c.get("total"), "triggered_by_inspection_id": r.get("triggered_by_inspection_id"),
            "report_file": r.get("report_file")})
        stats["maintenance"] += 1

    for r in jl(f"{well_dir}/regulatory-events.jsonl"):
        insert(con, "regulatory_events", {
            "event_id": r["event_id"], "asset_id": wid, "date": r.get("date"), "agency": r.get("agency"),
            "event_type": r.get("event_type"), "protocol_id": r.get("protocol_id"),
            "related_inspection_id": r.get("related_inspection_id"), "hours_to_report": r.get("hours_to_report"),
            "detail": r.get("detail"), "fine_usd": r.get("fine_usd"), "status": r.get("status")})
        stats["regulatory_events"] += 1

    for r in jl(f"{well_dir}/context.jsonl"):
        insert(con, "notes", {
            "note_id": r["note_id"], "asset_id": wid, "created_at": r.get("created_at"),
            "author_user_id": r.get("author_user_id"), "note_type": r.get("note_type"),
            "inspection_id": r.get("inspection_id"), "text": r.get("text"),
            "valid_from": r.get("valid_from"), "valid_to": r.get("valid_to")})
        stats["notes"] += 1

    # Follow report_file, never the file name pattern: names vary.
    for mid, f in reports.reports_for_well(well_dir, maint).items():
        for proto, result in f["checks"].items():
            sr02 = proto == "SR-02"
            insert(con, "report_facts", {
                "maintenance_id": mid, "asset_id": wid, "inspection_id": f["inspection_id"], "protocol": proto,
                "result": result, "detail": f["details"].get(proto), "contained_hours": f.get("contained_hours") if sr02 else None,
                "limit_hours": f.get("containment_limit_hours") if sr02 else None,
                "report_path": os.path.relpath(f["path"], root)})
        add_doc(con, "report", mid, os.path.relpath(f["path"], root), open(f["path"]).read(), asset_id=wid)
        stats["reports"] += 1


def _saved_state(db_path):
    """Rows of the PRESERVE tables from an existing database, so a reload keeps user state."""
    saved = {}
    if not os.path.exists(db_path):
        return saved
    con = sqlite3.connect(db_path)
    for table in PRESERVE:
        try:
            cur = con.execute(f"SELECT * FROM {table}")
            saved[table] = ([d[0] for d in cur.description], cur.fetchall())
        except sqlite3.OperationalError:
            pass  # table did not exist in an older database
    con.close()
    return saved


def load(data_dirs, db_path):
    saved = _saved_state(db_path)
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    with open(SCHEMA) as f:
        con.executescript(f.read())
    for table, (cols, rows) in saved.items():
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
    stats = {k: 0 for k in ("documents", "inspections", "maintenance", "regulatory_events", "notes", "reports")}
    for root in data_dirs:
        load_fleet(con, root, stats)
        wells = sorted(glob.glob(f"{root}/assets/WELL-*"))
        for well_dir in wells:
            load_well(con, root, well_dir, stats)
    con.execute("INSERT INTO doc_fts(doc_fts) VALUES ('rebuild')")
    con.commit()
    problems = check(con)
    stats = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
             ("wells", "inspections", "maintenance", "regulatory_events", "notes", "report_facts", "doc_chunks",
              "overlay_entries")}
    con.close()
    return stats, problems


def check(con):
    """Load-time sanity checks. Returns a list of human-readable problems (empty = fine)."""
    problems = []
    for (t,) in con.execute("SELECT DISTINCT well_type FROM wells WHERE well_type NOT IN "
                            "(SELECT well_type FROM rule_params WHERE name='producing_delta_c')"):
        problems.append(f"well type {t}: no producing threshold found in its manual; producing status will be unknown")
    n = con.execute("SELECT COUNT(*) FROM maintenance WHERE report_file IS NOT NULL AND maintenance_id NOT IN "
                    "(SELECT maintenance_id FROM report_facts)").fetchone()[0]
    if n:
        problems.append(f"{n} maintenance records point at a report file that was not found or had no compliance table")
    have = {r[0] for r in con.execute("SELECT name FROM rule_params WHERE well_type = '*'")}
    for name in sorted(set(rules.SOP_PATTERNS) - have):
        problems.append(f"SOP rule {name} not found in the SOPs; answers that need it will say so")
    n = con.execute("SELECT COUNT(*) FROM inspections WHERE asset_id NOT IN (SELECT asset_id FROM wells)").fetchone()[0]
    if n:
        problems.append(f"{n} inspections reference unknown wells")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", action="append", required=True, help="dataset folder containing fleet/ and assets/ (repeatable)")
    ap.add_argument("--db", default="var/wellspring.db")
    args = ap.parse_args(argv)
    stats, problems = load(args.data, args.db)
    print(json.dumps(stats))
    for p in problems:
        print("WARNING:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
