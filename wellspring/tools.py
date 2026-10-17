"""Deterministic tools the copilot calls. The LLM decides which to call; these decide what is true.

Every tool takes a Context (database connection, current user, "today") plus JSON arguments and
returns a dict with:
  rows      the records the answer is built from (effective values: raw data + corrections)
  summary   totals and headline facts
  evidence  [{kind, ref, detail, source}] records and document sections used, for citations
  visuals   {type: spec} ready-made visuals this result can be shown as (card/table/line/bar/map/heatmap)
  flags     [{kind, message, refs}] stale records, conflicts, corrections applied, missing reasons...

Tools never guess: a missing rule or record is reported as missing.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import overlay

MAX_ROWS = 200
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Context:
    con: sqlite3.Connection
    user_id: str
    today: date | None = None  # resolved in __post_init__: WELLSPRING_TODAY, else the loaded dataset's "today"
    conversation_id: str | None = None

    def __post_init__(self):
        if self.today is None:
            env = os.environ.get("WELLSPRING_TODAY")
            try:
                row = None if env else self.con.execute("SELECT value FROM meta WHERE key = 'today'").fetchone()
            except sqlite3.OperationalError:  # database built before the meta table existed
                row = None
            self.today = date.fromisoformat(env or (row[0] if row and row[0] else "2026-10-16"))


# --- helpers ------------------------------------------------------------------------------------

def _rows(ctx, sql, params=()):
    cur = ctx.con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _window(ctx, start, end, default_days=None):
    end_d = _d(end) if end else ctx.today
    if start:
        start_d = _d(start)
    elif default_days:
        start_d = end_d - timedelta(days=default_days)
    else:
        start_d = date(1900, 1, 1)
    return start_d.isoformat(), end_d.isoformat()


def _rule(ctx, name, well_type="*"):
    r = _rows(ctx, "SELECT value, source, overridden, manual_value FROM v_rule_effective WHERE well_type = ? AND name = ?",
              (well_type, name))
    return r[0] if r else None


def _users(ctx):
    return {r["user_id"]: r["name"] for r in _rows(ctx, "SELECT user_id, name FROM users")}


def _area_names(ctx):
    return {r["area_id"]: r["name"] for r in _rows(ctx, "SELECT area_id, name FROM areas")}


def _well_filter(ctx, asset_ids, area_id, alias="w"):
    clauses, params = [], []
    if asset_ids:
        clauses.append(f"{alias}.asset_id IN ({','.join('?' * len(asset_ids))})")
        params += list(asset_ids)
    if area_id:
        clauses.append(f"{alias}.area_id = ?")
        params.append(_resolve_area(ctx, area_id))
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _resolve_area(ctx, area):
    """Accept an area ID or its name ("Northeast Block 8")."""
    for r in _rows(ctx, "SELECT area_id, name FROM areas"):
        if area.lower() in (r["area_id"].lower(), r["name"].lower()):
            return r["area_id"]
    raise ToolError(f"unknown area {area!r}")


def _spill_class(ctx, bbl):
    if bbl is None:
        return None
    major, rep, mod = (_rule(ctx, n) for n in ("spill_major_bbl", "spill_reportable_bbl", "spill_moderate_bbl"))
    if major and bbl >= major["value"]:
        return "major"
    if rep and bbl >= rep["value"]:
        return "reportable"
    if mod and bbl >= mod["value"]:
        return "moderate"
    return "minor"


def _table(rows, cols):
    return {"type": "table", "columns": cols, "rows": [[r.get(c) for c in cols] for r in rows[:MAX_ROWS]]}


def _result(rows=None, summary=None, evidence=None, visuals=None, flags=None):
    rows = rows or []
    out = {"rows": rows[:MAX_ROWS], "summary": summary or {}, "evidence": evidence or [],
           "visuals": visuals or {}, "flags": flags or []}
    if len(rows) > MAX_ROWS:
        out["summary"]["rows_truncated"] = f"showing {MAX_ROWS} of {len(rows)}"
    return out


class ToolError(ValueError):
    """Bad arguments; returned to the model as an error result so it can retry."""


# --- tools --------------------------------------------------------------------------------------

def well_status(ctx, asset_ids=None, area_id=None, only_not_producing=False):
    """Status of wells right now (latest inspection), with the reason when not producing."""
    where, params = _well_filter(ctx, asset_ids, area_id, alias="v")
    latest = _rows(ctx, f"SELECT v.* FROM v_latest_inspection v WHERE 1=1 {where} ORDER BY v.asset_id", params)
    if asset_ids:
        missing = set(asset_ids) - {r["asset_id"] for r in latest}
        if missing:
            raise ToolError(f"unknown wells: {sorted(missing)}")
    users, today = _users(ctx), ctx.today.isoformat()
    rows, evidence, flags = [], [], []
    for v in latest:
        if only_not_producing and v["producing"] != 0:
            continue
        w = _rows(ctx, "SELECT * FROM wells WHERE asset_id = ?", (v["asset_id"],))[0]
        thr = _rule(ctx, "producing_delta_c", v["well_type"])
        open_repairs = _rows(ctx, "SELECT maintenance_id, issue, equipment_kind, started_at, completed_at FROM maintenance "
                                  "WHERE asset_id = ? AND SUBSTR(started_at, 1, 10) <= ? AND SUBSTR(completed_at, 1, 10) > ?",
                             (v["asset_id"], today, today))
        notes = _rows(ctx, "SELECT note_id, note_type, author_user_id, text, valid_from, valid_to FROM notes WHERE asset_id = ? "
                           "AND note_type <> 'correction' AND IFNULL(valid_from, '0') <= ? AND (valid_to IS NULL OR valid_to >= ?)",
                      (v["asset_id"], today, today))
        user_says = [e for e in overlay.for_well(ctx.con, v["asset_id"]) if e["kind"] in ("note", "claim")
                     and (e["valid_to"] is None or e["valid_to"] >= today)]
        status = {None: "unknown (no rule for this well type)", 1: "producing", 0: "not producing"}[v["producing"]]
        stale = (w["recorded_status"] == "active" and v["producing"] == 0) or (w["recorded_status"] == "down" and v["producing"] == 1)
        explanations = ([f"open repair {m['maintenance_id']}: {m['issue']} (started {m['started_at'][:10]}, planned end {m['completed_at'][:10]})" for m in open_repairs]
                        + [f"note {n['note_id']} by {users.get(n['author_user_id'], n['author_user_id'])}: {n['text']}" for n in notes if n["valid_to"]]
                        + [f"{e['entry_id']} ({e['kind']}, {e['status']}) by {e['author_name']}: {e['text']}" for e in user_says])
        row = {
            "asset_id": v["asset_id"], "name": w["name"], "well_type": v["well_type"], "area_id": v["area_id"],
            "status": status, "producing": v["producing"], "delta_c": v["delta_c"],
            "wellhead_temp_c": v["wellhead_temp_c"], "ambient_temp_c": v["ambient_temp_c"],
            "threshold_c": thr["value"] if thr else None, "threshold_source": thr["source"] if thr else None,
            "inspection_id": v["inspection_id"], "inspected_at": v["inspected_at"], "method": v["method"],
            "inspector": users.get(v["inspector_user_id"], v["inspector_user_id"]), "inspector_note": v["notes"] or None,
            "recorded_status": w["recorded_status"], "recorded_status_stale": stale,
            "open_repairs": open_repairs, "active_notes": notes, "user_statements": user_says,
            "explanations": explanations, "lat": v["lat"], "lon": v["lon"],
        }
        if v["overlay_ids"]:
            row["raw_wellhead_temp_c"] = v["raw_wellhead_temp_c"]
            row["corrections_applied"] = [e for e in overlay.related_context(ctx.con, v["inspection_id"])["overlay"] if e["status"] == "active"]
            who = "; ".join(f"{c['entry_id']} by {c['author_name']}: {c['field']} {c['original_value']} -> {c['new_value']} ({c['reason']})"
                            for c in row["corrections_applied"])
            flags.append({"kind": "correction_applied", "refs": [v["inspection_id"], v["overlay_ids"]],
                          "message": f"{v['asset_id']}: latest reading corrected, {who}; raw value {v['raw_wellhead_temp_c']} °C kept"})
            if v["notes"]:
                flags.append({"kind": "conflict", "refs": [v["inspection_id"]],
                              "message": f"{v['asset_id']}: inspector remark '{v['notes']}' may disagree with the corrected value"})
        rows.append(row)
        evidence.append({"kind": "inspection", "ref": v["inspection_id"], "detail": f"{v['asset_id']} {v['inspected_at']} wellhead {v['wellhead_temp_c']} °C, air {v['ambient_temp_c']} °C", "source": f"assets/{v['asset_id']}/inspections.jsonl"})
        if thr:
            evidence.append({"kind": "manual", "ref": thr["source"], "detail": f"{v['well_type']} producing when wellhead >= {thr['value']} °C above ambient", "source": thr["source"]})
        evidence += [{"kind": "repair", "ref": m["maintenance_id"], "detail": m["issue"], "source": f"assets/{v['asset_id']}/maintenance.jsonl"} for m in open_repairs]
        evidence += [{"kind": "note", "ref": n["note_id"], "detail": n["text"], "source": f"assets/{v['asset_id']}/context.jsonl"} for n in notes]
        evidence += [{"kind": "overlay", "ref": e["entry_id"], "detail": e["text"], "source": "overlay"} for e in user_says]
        if stale:
            flags.append({"kind": "stale_status", "refs": [v["asset_id"]], "message": f"{v['asset_id']}: recorded status '{w['recorded_status']}' disagrees with the latest inspection ({status})"})
        if v["producing"] == 0 and not explanations:
            flags.append({"kind": "no_recorded_reason", "refs": [v["asset_id"]],
                          "message": f"{v['asset_id']}: not producing and no repair, note or statement explains it; suggest a field check"
                                     + (f" (inspector noted: '{v['notes']}')" if v["notes"] else "")})
        for e in user_says:
            if e["kind"] == "claim":
                flags.append({"kind": "unconfirmed_claim", "refs": [e["entry_id"]], "message": f"{e['author_name']} said: {e['text']} (unconfirmed, contradicts data)"})
    cols = ["asset_id", "well_type", "status", "delta_c", "threshold_c", "inspected_at", "inspector", "recorded_status", "explanations"]
    visuals = {"table": _table(rows, cols),
               "map": {"type": "map", "pins": [{"lat": r["lat"], "lon": r["lon"], "label": r["asset_id"], "detail": r["status"]} for r in rows]}}
    if len(rows) == 1:
        r = rows[0]
        visuals["card"] = {"type": "card", "title": f"{r['asset_id']} ({r['name']})", "status": r["status"], "fields": {
            "Well type": r["well_type"], "Wellhead above air": f"{r['delta_c']} °C (limit {r['threshold_c']} °C)",
            "Last inspection": f"{r['inspection_id']} on {r['inspected_at'][:10]}, {r['method']}, by {r['inspector']}",
            "Recorded status": r["recorded_status"] + (" (out of date)" if r["recorded_status_stale"] else ""),
            ("Why" if r["producing"] == 0 else "Context"): "; ".join(r["explanations"]) or ("no recorded reason" if r["producing"] == 0 else "—")}}
    return _result(rows, {"wells": len(rows), "not_producing": sum(1 for r in rows if r["producing"] == 0), "today": ctx.today.isoformat()},
                   evidence, visuals, flags)


def inspection_history(ctx, asset_id, start=None, end=None, last_n=None):
    """Readings over time for one well (effective values) with the producing flag per reading."""
    s, e = _window(ctx, start, end)
    rows = _rows(ctx, "SELECT inspection_id, day, wellhead_temp_c, ambient_temp_c, delta_c, threshold_c, producing, "
                      "spill_detected, real_spill, spill_est_volume_bbl, overlay_ids, raw_wellhead_temp_c, notes AS inspector_note "
                      "FROM v_inspection WHERE asset_id = ? AND day BETWEEN ? AND ? ORDER BY inspected_at", (asset_id, s, e))
    if not rows and not _rows(ctx, "SELECT 1 FROM wells WHERE asset_id = ?", (asset_id,)):
        raise ToolError(f"unknown well {asset_id}")
    if last_n:
        rows = rows[-int(last_n):]
    off = [r for r in rows if r["producing"] == 0]
    visuals = {"line": {"type": "line", "title": f"{asset_id} wellhead vs air temperature", "x": "day",
                        "series": [{"key": "wellhead_temp_c", "label": "Wellhead"}, {"key": "ambient_temp_c", "label": "Air"}],
                        "points": [{k: r[k] for k in ("day", "wellhead_temp_c", "ambient_temp_c", "producing")} for r in rows],
                        "markers": [{"x": r["day"], "label": "not producing"} for r in off]},
               "table": _table(rows, ["inspection_id", "day", "wellhead_temp_c", "ambient_temp_c", "delta_c", "producing"])}
    flags = [{"kind": "correction_applied", "refs": [r["inspection_id"]], "message": f"{r['inspection_id']} corrected ({r['overlay_ids']}), raw {r['raw_wellhead_temp_c']} °C"} for r in rows if r["overlay_ids"]]
    return _result(rows, {"readings": len(rows), "first": rows[0]["day"] if rows else None, "last": rows[-1]["day"] if rows else None,
                          "not_producing_days": [r["day"] for r in off]},
                   [{"kind": "inspection", "ref": r["inspection_id"], "detail": r["day"], "source": f"assets/{asset_id}/inspections.jsonl"} for r in rows],
                   visuals, flags)


def downtime(ctx, start=None, end=None, area_id=None, asset_ids=None, top_n=None):
    """Downtime per well: 7 days for each inspection in the window that found the well not producing."""
    s, e = _window(ctx, start, end)
    where, params = _well_filter(ctx, asset_ids, area_id)
    totals = _rows(ctx, f"""
        SELECT w.asset_id, w.well_type, w.area_id, COALESCE(SUM(d.downtime_days), 0) AS downtime_days,
               GROUP_CONCAT(d.inspection_id) AS inspections
        FROM wells w LEFT JOIN v_downtime d ON d.asset_id = w.asset_id AND d.day BETWEEN ? AND ?
        WHERE 1=1 {where} GROUP BY w.asset_id ORDER BY downtime_days DESC, w.asset_id""", [s, e] + params)
    ranked = totals[: int(top_n)] if top_n else totals
    nxt = totals[int(top_n)] if top_n and len(totals) > int(top_n) else None
    detail_rows, evidence = [], []
    for t in ranked:
        ids = (t["inspections"] or "").split(",") if t["inspections"] else []
        t["inspections"] = ids
        if len(ranked) <= 10 and ids:
            t["periods"] = [_explain_downtime(ctx, t["asset_id"], i) for i in ids]
        evidence += [{"kind": "inspection", "ref": i, "detail": f"{t['asset_id']} not producing", "source": f"assets/{t['asset_id']}/inspections.jsonl"} for i in ids]
    evidence.append({"kind": "rule", "ref": "dataset guide", "detail": "each non-producing weekly inspection = 7 days of downtime", "source": "02-dataset-guide.md"})
    visuals = {"bar": {"type": "bar", "title": f"Downtime {s} to {e}", "x": "asset_id", "y": "downtime_days",
                       "bars": [{"asset_id": r["asset_id"], "downtime_days": r["downtime_days"], "well_type": r["well_type"]} for r in ranked]},
               "table": _table(ranked, ["asset_id", "well_type", "area_id", "downtime_days"])}
    return _result(ranked, {"window": [s, e], "wells_considered": len(totals), "next_after_top": nxt and {k: nxt[k] for k in ("asset_id", "downtime_days")}},
                   evidence, visuals)


def _explain_downtime(ctx, asset_id, inspection_id):
    v = _rows(ctx, "SELECT day, wellhead_temp_c, ambient_temp_c, delta_c, threshold_c FROM v_inspection WHERE inspection_id = ?", (inspection_id,))[0]
    day = v["day"]
    week_after = (_d(day) + timedelta(days=7)).isoformat()
    causes = _rows(ctx, "SELECT maintenance_id, issue, maintenance_type, SUBSTR(started_at,1,10) AS started, SUBSTR(completed_at,1,10) AS completed "
                        "FROM maintenance WHERE asset_id = ? AND (triggered_by_inspection_id = ? "
                        "OR (SUBSTR(started_at,1,10) <= ? AND SUBSTR(completed_at,1,10) >= ?) "
                        "OR (maintenance_type = 'corrective' AND SUBSTR(started_at,1,10) BETWEEN ? AND ?))",
                   (asset_id, inspection_id, day, day, day, week_after))
    notes = _rows(ctx, "SELECT note_id, text FROM notes WHERE asset_id = ? AND note_type <> 'correction' AND valid_from <= ? "
                       "AND valid_to IS NOT NULL AND valid_to >= ?", (asset_id, day, day))
    return {"inspection_id": inspection_id, **v, "possible_causes": causes, "notes": notes}


def spills(ctx, start=None, end=None, area_id=None, asset_ids=None, min_class=None, group_by=None):
    """Real oil spills (detections minus corrections) with volume, SOP class and location.
    group_by: 'area_month' for a heatmap, 'area' or 'well' for comparisons."""
    s, e = _window(ctx, start, end)
    where, params = _well_filter(ctx, asset_ids, area_id)
    detections = _rows(ctx, f"""
        SELECT v.asset_id, v.area_id, v.inspection_id, v.day, v.inspected_at, v.spill_est_volume_bbl AS volume_bbl,
               v.raw_spill_est_volume_bbl AS raw_volume_bbl, v.spill_area_pct, v.real_spill, v.is_corrected, v.spill_detected,
               v.raw_spill_detected, v.overlay_ids, v.lat, v.lon, v.notes AS inspector_note
        FROM v_inspection v JOIN wells w ON w.asset_id = v.asset_id
        WHERE (v.raw_spill_detected = 1 OR v.spill_detected = 1) AND v.day BETWEEN ? AND ? {where}
        ORDER BY v.inspected_at""", [s, e] + params)
    areas = _area_names(ctx)
    order = ["minor", "moderate", "reportable", "major"]
    rows, excluded, evidence, flags = [], [], [], []
    for d in detections:
        if not d["real_spill"]:
            why = _rows(ctx, "SELECT note_id, text FROM notes WHERE note_type = 'correction' AND inspection_id = ?", (d["inspection_id"],))
            excluded.append({"inspection_id": d["inspection_id"], "asset_id": d["asset_id"], "day": d["day"],
                             "reason": why[0] if why else f"overlay correction {d['overlay_ids']}"})
            continue
        d["spill_class"] = _spill_class(ctx, d["volume_bbl"])
        d["area_name"] = areas.get(d["area_id"])
        if min_class and order.index(d["spill_class"]) < order.index(min_class):
            continue
        disagreement = _rows(ctx, "SELECT note_id, author_user_id, text FROM notes WHERE note_type <> 'correction' AND text LIKE ?",
                             (f"%{d['inspection_id']}%",))
        if disagreement:
            d["notes_about_detection"] = disagreement
            flags.append({"kind": "conflict", "refs": [d["inspection_id"]] + [n["note_id"] for n in disagreement],
                          "message": f"{d['inspection_id']}: notes discuss this detection but none is a formal correction, so it stands"})
        # Containment time lives only in the repair report (SR-02 row); attach it so every spill answer has it.
        rf = _rows(ctx, "SELECT maintenance_id, contained_hours, limit_hours, report_path FROM report_facts "
                        "WHERE inspection_id = ? AND protocol = 'SR-02' AND contained_hours IS NOT NULL", (d["inspection_id"],))
        if rf:
            f = rf[0]
            d.update(contained_hours=f["contained_hours"], containment_limit_hours=f["limit_hours"],
                     contained_within_limit=f["contained_hours"] <= f["limit_hours"], repair_report=f["maintenance_id"])
            evidence.append({"kind": "report", "ref": f["maintenance_id"], "detail": f"contained after about {f['contained_hours']} h (limit {f['limit_hours']:g} h)", "source": f["report_path"]})
            if f["contained_hours"] > f["limit_hours"]:
                flags.append({"kind": "containment_breach", "refs": [d["inspection_id"], f["maintenance_id"]],
                              "message": f"{d['asset_id']} spill {d['day']}: contained after about {f['contained_hours']} h, over the {f['limit_hours']:g} h SR-02 limit (report {f['maintenance_id']})"})
        else:
            d["contained_hours"] = None  # no report with a containment time; say unknown, never assume met
        if d["overlay_ids"] and d["raw_volume_bbl"] != d["volume_bbl"]:
            flags.append({"kind": "correction_applied", "refs": [d["inspection_id"], d["overlay_ids"]],
                          "message": f"{d['inspection_id']}: volume corrected from AI estimate {d['raw_volume_bbl']} to {d['volume_bbl']} bbl ({d['overlay_ids']})"})
        rows.append(d)
        evidence.append({"kind": "inspection", "ref": d["inspection_id"], "detail": f"{d['asset_id']} {d['day']} {d['volume_bbl']} bbl", "source": f"assets/{d['asset_id']}/inspections.jsonl"})
    evidence += [{"kind": "note", "ref": x["reason"]["note_id"] if isinstance(x["reason"], dict) else x["inspection_id"],
                  "detail": f"{x['inspection_id']} excluded: {x['reason']}", "source": f"assets/{x['asset_id']}/context.jsonl"} for x in excluded]
    rep = _rule(ctx, "spill_reportable_bbl")
    if rep:
        evidence.append({"kind": "sop", "ref": rep["source"], "detail": f"reportable at >= {rep['value']} bbl", "source": rep["source"]})
    visuals = {"table": _table(rows, ["asset_id", "inspection_id", "day", "volume_bbl", "spill_class", "contained_hours"]),
               "map": {"type": "map", "pins": [{"lat": r["lat"], "lon": r["lon"], "label": r["asset_id"], "detail": f"{r['day']}: {r['volume_bbl']} bbl"} for r in rows]}}
    summary = {"window": [s, e], "spills": len(rows), "total_bbl": round(sum(r["volume_bbl"] or 0 for r in rows), 1),
               "containment_breaches": [r["asset_id"] + " " + r["day"] for r in rows if r.get("contained_within_limit") is False],
               "containment_unknown": sum(1 for r in rows if r.get("contained_hours") is None),
               "detections": len(detections), "excluded_as_corrected": len(excluded), "excluded": excluded}
    if group_by in ("area_month", "area", "well"):
        months = sorted({r["day"][:7] for r in rows}) or []
        if group_by == "area_month":
            first, last = _d(s if s > "1900" else (rows[0]["day"] if rows else e)), _d(e)
            months, m = [], date(first.year, first.month, 1)
            while m <= last:
                months.append(m.strftime("%Y-%m"))
                m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            area_ids = [a for a in areas if not area_id or a == _resolve_area(ctx, area_id)]
            grid = [[sum(1 for r in rows if r["area_id"] == a and r["day"][:7] == mo) for mo in months] for a in area_ids]
            visuals["heatmap"] = {"type": "heatmap", "title": "Real spills by area and month",
                                  "rows": [areas[a] for a in area_ids], "cols": [MONTHS[int(mo[5:]) - 1] + " " + mo[:4] for mo in months],
                                  "values": grid, "row_totals": [sum(g) for g in grid]}
            summary["by_area_month"] = {areas[a]: dict(zip(months, g)) for a, g in zip(area_ids, grid)}
        key = "area_name" if group_by in ("area", "area_month") else "asset_id"
        counts = {}
        for r in rows:
            c = counts.setdefault(r[key], {"spills": 0, "bbl": 0.0})
            c["spills"] += 1
            c["bbl"] = round(c["bbl"] + (r["volume_bbl"] or 0), 1)
        visuals["bar"] = {"type": "bar", "title": f"Spills by {key}", "x": key, "y": "spills",
                          "bars": [{key: k, **v} for k, v in sorted(counts.items())]}
        summary[f"by_{key}"] = counts
    return _result(rows, summary, evidence, visuals, flags)


def repairs(ctx, start=None, end=None, asset_ids=None, area_id=None, equipment_kind=None, maintenance_type=None, open_only=False):
    """Maintenance jobs (by start date) with costs, plus total cost per well."""
    s, e = _window(ctx, start, end)
    where, params = _well_filter(ctx, asset_ids, area_id)
    extra = ""
    if equipment_kind:
        extra += " AND m.equipment_kind = ?"
        params.append(equipment_kind)
    if maintenance_type:
        extra += " AND m.maintenance_type = ?"
        params.append(maintenance_type)
    if open_only:
        extra += " AND SUBSTR(m.completed_at, 1, 10) > ?"
        params.append(ctx.today.isoformat())
    rows = _rows(ctx, f"""
        SELECT m.maintenance_id, m.asset_id, w.well_type, SUBSTR(m.started_at,1,10) AS started, SUBSTR(m.completed_at,1,10) AS completed,
               m.maintenance_type, m.equipment_kind, m.issue, m.action_taken, m.downtime_hours, m.cost_total, m.cost_labor, m.cost_parts,
               m.triggered_by_inspection_id, m.report_file
        FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id
        WHERE SUBSTR(m.started_at,1,10) BETWEEN ? AND ? {where} {extra} ORDER BY m.started_at""", [s, e] + params)
    per_well = {}
    for r in rows:
        p = per_well.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "jobs": 0, "cost_usd": 0.0})
        p["jobs"] += 1
        p["cost_usd"] += r["cost_total"] or 0
    for want in asset_ids or []:
        per_well.setdefault(want, {"asset_id": want, "jobs": 0, "cost_usd": 0.0})
    per = sorted(per_well.values(), key=lambda p: -p["cost_usd"])
    visuals = {"table": _table(rows, ["maintenance_id", "asset_id", "started", "maintenance_type", "equipment_kind", "issue", "cost_total"]),
               "bar": {"type": "bar", "title": "Repair cost per well (USD)", "x": "asset_id", "y": "cost_usd", "bars": per}}
    return _result(rows, {"window": [s, e], "jobs": len(rows), "total_cost_usd": round(sum(r["cost_total"] or 0 for r in rows), 2), "per_well": per},
                   [{"kind": "repair", "ref": r["maintenance_id"], "detail": f"{r['issue']} USD {r['cost_total']}", "source": f"assets/{r['asset_id']}/maintenance.jsonl"} for r in rows],
                   visuals)


def maintenance_escalation(ctx, area_id=None, asset_ids=None, equipment_kind=None):
    """Wells that need a root-cause analysis: corrective repairs on one equipment kind within the rule window,
    at or above the threshold for their well type (manual rule, or a user rule for that type)."""
    where, params = _well_filter(ctx, asset_ids, area_id)
    wells = _rows(ctx, f"SELECT w.asset_id, w.well_type FROM wells w WHERE 1=1 {where}", params)
    hits, near, evidence = [], [], []
    for w in wells:
        thr, win = _rule(ctx, "rca_corrective_repairs", w["well_type"]), _rule(ctx, "rca_window_months", w["well_type"])
        if not thr:
            continue
        months = int(win["value"]) if win else 12
        since = (ctx.today - timedelta(days=round(months * 365 / 12))).isoformat()
        q = ("SELECT equipment_kind, COUNT(*) n, GROUP_CONCAT(maintenance_id) ids FROM maintenance WHERE asset_id = ? "
             "AND maintenance_type = 'corrective' AND SUBSTR(started_at,1,10) > ? AND SUBSTR(started_at,1,10) <= ?")
        p = [w["asset_id"], since, ctx.today.isoformat()]
        if equipment_kind:
            q += " AND equipment_kind = ?"
            p.append(equipment_kind)
        for g in _rows(ctx, q + " GROUP BY equipment_kind", p):
            row = {"asset_id": w["asset_id"], "well_type": w["well_type"], "equipment_kind": g["equipment_kind"], "corrective_repairs": g["n"],
                   "repair_ids": g["ids"].split(","), "threshold": thr["value"], "rule_source": thr["source"],
                   "user_rule": bool(thr["overridden"]), "manual_threshold": thr["manual_value"], "since": since}
            if g["n"] >= thr["value"]:
                hits.append(row)
                evidence += [{"kind": "repair", "ref": i, "detail": f"corrective {g['equipment_kind']}", "source": f"assets/{w['asset_id']}/maintenance.jsonl"} for i in row["repair_ids"]]
            elif g["n"] == thr["value"] - 1:
                near.append(row)
    rules_used = sorted({(r["well_type"], r["threshold"], r["rule_source"]) for r in hits + near})
    evidence += [{"kind": "rule", "ref": src, "detail": f"{wt}: RCA at {int(t)} corrective repairs", "source": src} for wt, t, src in rules_used]
    visuals = {"table": _table(hits, ["asset_id", "well_type", "equipment_kind", "corrective_repairs", "threshold", "rule_source"]),
               "bar": {"type": "bar", "title": "Corrective repairs (RCA candidates)", "x": "asset_id", "y": "corrective_repairs", "bars": hits}}
    return _result(hits, {"needs_rca": len(hits), "one_below_threshold": near}, evidence, visuals)


def compliance_check(ctx, asset_ids=None, area_id=None, start=None, end=None):
    """Rule breaches and open actions in a window (default: last 12 months): SR-01 reporting, SR-02 containment
    (from repair reports), SR-03 verification, RC-04 notice responses, RC-01 inspection gaps, RC-02 flowline test,
    plus open RCA and time-bounded notes. Uses effective (corrected) spill volumes."""
    s, e = _window(ctx, start, end, default_days=365)
    today = ctx.today
    where, params = _well_filter(ctx, asset_ids, area_id)
    wells = [r["asset_id"] for r in _rows(ctx, f"SELECT w.asset_id FROM wells w WHERE 1=1 {where} ORDER BY 1", params)]
    R = {n: _rule(ctx, n) for n in ("spill_reportable_bbl", "spill_major_bbl", "sr01_report_hours", "sr01_major_report_hours",
                                    "sr02_contain_hours", "sr03_verify_days", "rc04_response_days", "rc01_inspection_days")}
    missing = [n for n, v in R.items() if v is None]
    breaches, open_items, met, evidence, flags = [], [], [], [], []

    def add(lst, wid, proto, ref, msg, refs=()):
        lst.append({"asset_id": wid, "protocol": proto, "ref": ref, "detail": msg})
        for r in refs:
            evidence.append(r)

    for wid in wells:
        spills_ = _rows(ctx, "SELECT inspection_id, inspected_at, day, spill_est_volume_bbl AS bbl, raw_spill_est_volume_bbl AS raw_bbl, overlay_ids "
                             "FROM v_real_spills WHERE asset_id = ? AND day BETWEEN ? AND ?", (wid, s, e))
        for sp in spills_:
            iid, found = sp["inspection_id"], datetime.fromisoformat(sp["inspected_at"].replace("Z", "+00:00"))
            facts = _rows(ctx, "SELECT maintenance_id, protocol, result, detail, contained_hours, limit_hours, report_path FROM report_facts WHERE inspection_id = ?", (iid,))
            for f in facts:
                if f["protocol"] == "SR-02" and f["result"] == "Not met":
                    add(breaches, wid, "SR-02", iid, f"contained after about {f['contained_hours']} h, limit {f['limit_hours']:g} h (report {f['maintenance_id']})",
                        [{"kind": "report", "ref": f["maintenance_id"], "detail": f"SR-02: {f['detail']}", "source": f["report_path"]}])
            if R["spill_reportable_bbl"] is None or (sp["bbl"] or 0) < R["spill_reportable_bbl"]["value"]:
                continue
            major = R["spill_major_bbl"] and sp["bbl"] >= R["spill_major_bbl"]["value"]
            limit_h = (R["sr01_major_report_hours"] if major else R["sr01_report_hours"])["value"]
            ev = _rows(ctx, "SELECT * FROM regulatory_events WHERE related_inspection_id = ? ORDER BY date", (iid,))
            src = {"kind": "inspection", "ref": iid, "detail": f"{sp['bbl']} bbl reportable spill", "source": f"assets/{wid}/inspections.jsonl"}
            filed = [x for x in ev if x["event_type"] == "spill_report_filed"]
            if filed and filed[0]["hours_to_report"] is not None and filed[0]["hours_to_report"] > limit_h:
                add(breaches, wid, "SR-01", iid, f"written notice after {filed[0]['hours_to_report']:g} h, limit {limit_h:g} h ({filed[0]['event_id']})",
                    [src, {"kind": "regulatory", "ref": filed[0]["event_id"], "detail": filed[0]["detail"], "source": f"assets/{wid}/regulatory-events.jsonl"}])
            elif not filed:
                due = found + timedelta(hours=limit_h)
                add(breaches if datetime.now(due.tzinfo) > due or today > due.date() else open_items, wid, "SR-01", iid,
                    f"no written notice on record; was due {due:%d %b %Y %H:%M} UTC ({limit_h:g} h after the {found:%d %b %H:%M} inspection)", [src])
            for f in facts:
                if f["protocol"] in ("SR-04", "SR-03", "SR-01") and f["detail"] and re.search(r"\b(minor|moderate)\b", f["detail"], re.I):
                    flags.append({"kind": "report_outdated", "refs": [f["maintenance_id"], iid],
                                  "message": f"report {f['maintenance_id']} {f['protocol']} says '{f['detail']}', based on the old estimate {sp['raw_bbl']} bbl; at {sp['bbl']} bbl the spill is reportable"})
            due3 = found.date() + timedelta(days=int(R["sr03_verify_days"]["value"]))
            verified = [x for x in ev if x["event_type"] == "site_inspection" and x["status"] == "closed"]
            if not verified:
                pending = [x for x in ev if x["event_type"] == "site_inspection"]
                add(breaches if today > due3 else open_items, wid, "SR-03", iid, f"agency verification due {due3:%d %b %Y}, not closed"
                    + (f" ({pending[0]['event_id']}: {pending[0]['detail']})" if pending else ""),
                    [{"kind": "regulatory", "ref": x["event_id"], "detail": x["detail"], "source": f"assets/{wid}/regulatory-events.jsonl"} for x in pending])
        for nov in _rows(ctx, "SELECT * FROM regulatory_events WHERE asset_id = ? AND event_type = 'notice_of_violation' AND date BETWEEN ? AND ?", (wid, s, e)):
            due = _d(nov["date"]) + timedelta(days=int(R["rc04_response_days"]["value"]))
            answered = _rows(ctx, "SELECT event_id FROM regulatory_events WHERE asset_id = ? AND event_type = 'nov_response_filed' AND date >= ?", (wid, nov["date"]))
            refs = [{"kind": "regulatory", "ref": nov["event_id"], "detail": f"{nov['detail']} fine USD {nov['fine_usd']:g}, {nov['status']}", "source": f"assets/{wid}/regulatory-events.jsonl"}]
            if not answered:
                add(breaches if today > due else open_items, wid, "RC-04", nov["event_id"],
                    f"response due {due:%d %b %Y}, none recorded; fine USD {nov['fine_usd']:g} ({nov['status']})", refs)
            elif nov["status"] == "open":
                add(open_items, wid, "RC-04", nov["event_id"], f"answered, fine USD {nov['fine_usd']:g} still open", refs)
        days = [r["day"] for r in _rows(ctx, "SELECT day FROM v_inspection WHERE asset_id = ? AND day BETWEEN ? AND ? ORDER BY day", (wid, s, e))]
        if days and R["rc01_inspection_days"]:
            gaps = [((_d(b) - _d(a)).days, a, b) for a, b in zip(days, days[1:])]
            worst = max(gaps) if gaps else (0, None, None)
            if worst[0] > R["rc01_inspection_days"]["value"]:
                add(breaches, wid, "RC-01", None, f"{worst[0]} days between inspections on {worst[1]} and {worst[2]}")
        year = str(today.year)
        test = _rows(ctx, "SELECT maintenance_id, SUBSTR(started_at,1,10) d FROM maintenance WHERE asset_id = ? AND issue = 'Annual flowline integrity test' AND started_at LIKE ?", (wid, year + "%"))
        (met if test else open_items).append({"asset_id": wid, "protocol": "RC-02", "ref": test[0]["maintenance_id"] if test else None,
                                              "detail": f"annual flowline test done {test[0]['d']}" if test else f"no flowline integrity test recorded in {year} yet"})
        notes = _rows(ctx, "SELECT note_id, text, valid_from, valid_to FROM notes WHERE asset_id = ? AND note_type <> 'correction' AND valid_to IS NOT NULL AND valid_to >= ?", (wid, today.isoformat()))
        for n in notes:
            add(open_items, wid, "NOTE", n["note_id"], n["text"], [{"kind": "note", "ref": n["note_id"], "detail": n["text"], "source": f"assets/{wid}/context.jsonl"}])
    rca = maintenance_escalation(ctx, asset_ids=wells if wells else None)
    for r in rca["rows"]:
        open_items.append({"asset_id": r["asset_id"], "protocol": "RCA", "ref": r["equipment_kind"],
                           "detail": f"{r['corrective_repairs']} corrective {r['equipment_kind']} repairs since {r['since']}: {', '.join(r['repair_ids'])} ({r['rule_source']})"})
    evidence += rca["evidence"]
    evidence += [{"kind": "sop", "ref": v["source"], "detail": f"{n} = {v['value']:g}", "source": v["source"]} for n, v in R.items() if v]
    if missing:
        flags.append({"kind": "missing_rule", "refs": missing, "message": f"rules not found in the SOPs: {missing}; related checks skipped"})
    fines_open = sum(x["fine_usd"] or 0 for x in _rows(ctx, f"SELECT fine_usd FROM regulatory_events WHERE status = 'open' AND asset_id IN ({','.join('?' * len(wells))})", wells)) if wells else 0
    visuals = {"table": _table(breaches + open_items, ["asset_id", "protocol", "ref", "detail"])}
    return _result(breaches, {"window": [s, e], "breaches": len(breaches), "open_items": open_items, "met": met, "open_fines_usd": fines_open},
                   evidence, visuals, flags)


def search_docs(ctx, query, doc_kind=None, asset_id=None, limit=6):
    """Full-text search over manuals, SOPs and repair reports. Returns sections with snippets to cite."""
    terms = re.findall(r"[A-Za-z0-9-]+", query)
    if not terms:
        raise ToolError("empty query")
    fts = " OR ".join(f'"{t}"' for t in terms)
    extra, params = "", [fts]
    if doc_kind:
        extra += " AND c.doc_kind = ?"
        params.append(doc_kind)
    if asset_id:
        extra += " AND c.asset_id = ?"
        params.append(asset_id)
    rows = _rows(ctx, f"""SELECT c.doc_kind, c.doc_id, c.asset_id, c.section, c.heading, c.path,
                                 snippet(doc_fts, 1, '[', ']', ' ... ', 24) AS snippet
                          FROM doc_fts JOIN doc_chunks c ON c.chunk_id = doc_fts.rowid
                          WHERE doc_fts MATCH ? {extra} ORDER BY rank LIMIT ?""", params + [int(limit)])
    return _result(rows, {"query": query, "hits": len(rows)},
                   [{"kind": r["doc_kind"], "ref": f"{r['doc_id']} {r['heading']}", "detail": r["snippet"], "source": r["path"]} for r in rows])


def read_doc_section(ctx, doc_id, section=None):
    """Full text of a document (manual, SOP or repair report by maintenance ID), or one section of it."""
    q, p = "SELECT doc_kind, doc_id, section, heading, path, text FROM doc_chunks WHERE doc_id = ?", [doc_id]
    if section:
        q += " AND (section = ? OR heading LIKE ?)"
        p += [str(section), f"%{section}%"]
    rows = _rows(ctx, q + " ORDER BY chunk_id", p)
    if not rows:
        raise ToolError(f"no document {doc_id!r}" + (f" section {section!r}" if section else ""))
    return _result(rows, {"doc_id": doc_id, "sections": len(rows)},
                   [{"kind": rows[0]["doc_kind"], "ref": f"{doc_id} {r['heading']}", "detail": r["text"][:160], "source": r["path"]} for r in rows])


def well_context(ctx, asset_id):
    """Everything else on record for one well: details, equipment, human notes, user statements,
    corrections, rules for its type and regulator events."""
    w = _rows(ctx, "SELECT * FROM wells WHERE asset_id = ?", (asset_id,))
    if not w:
        raise ToolError(f"unknown well {asset_id}")
    users = _users(ctx)
    notes = _rows(ctx, "SELECT * FROM notes WHERE asset_id = ? ORDER BY created_at", (asset_id,))
    for n in notes:
        n["author_name"] = users.get(n["author_user_id"])
    reg = _rows(ctx, "SELECT * FROM regulatory_events WHERE asset_id = ? ORDER BY date", (asset_id,))
    eq = _rows(ctx, "SELECT kind, model FROM equipment WHERE asset_id = ?", (asset_id,))
    entries = overlay.for_well(ctx.con, asset_id, include_superseded=True)
    flags = [{"kind": "unconfirmed_claim", "refs": [e["entry_id"]], "message": f"{e['author_name']}: {e['text']} (unconfirmed)"} for e in entries if e["status"] == "unconfirmed"]
    return _result([{**w[0], "equipment": eq, "notes": notes, "user_entries": entries, "regulatory_events": reg}],
                   {"notes": len(notes), "user_entries": len(entries), "regulatory_events": len(reg)},
                   [{"kind": "note", "ref": n["note_id"], "detail": n["text"], "source": f"assets/{asset_id}/context.jsonl"} for n in notes]
                   + [{"kind": "overlay", "ref": e["entry_id"], "detail": e["text"] or f"{e['field']}: {e['original_value']} -> {e['new_value']}", "source": "overlay"} for e in entries]
                   + [{"kind": "regulatory", "ref": r["event_id"], "detail": r["detail"], "source": f"assets/{asset_id}/regulatory-events.jsonl"} for r in reg],
                   flags=flags)


# --- writing: feedback and statements (author is always the current user) ----------------------------

def record_correction(ctx, inspection_id, field, new_value, reason):
    """Correct a raw reading or an AI estimate on one inspection. Keeps the original; applies for every user."""
    e = overlay.record_correction(ctx.con, ctx.user_id, inspection_id, field, new_value, reason)
    asset = _rows(ctx, "SELECT asset_id FROM inspections WHERE inspection_id = ?", (inspection_id,))[0]["asset_id"]
    related = overlay.related_context(ctx.con, inspection_id)
    after = _rows(ctx, "SELECT inspection_id, day, wellhead_temp_c, ambient_temp_c, delta_c, threshold_c, producing, spill_est_volume_bbl, real_spill FROM v_inspection WHERE inspection_id = ?", (inspection_id,))[0]
    flags = []
    remark = related["inspection"]["inspector_note"]
    if remark:
        # A remark can contradict a corrected temperature ("pump jack not moving" vs a producing reading);
        # for spill fields it is context, not disagreement.
        temp = field in ("wellhead_temp_c", "ambient_temp_c")
        flags.append({"kind": "conflict" if temp else "context", "refs": [inspection_id],
                      "message": f"the inspection also has the remark '{remark}' by {related['inspection']['inspector_name']}"
                                 + ("; it may disagree with the corrected value, ask the user to confirm" if temp else "")})
    for n in related["data_notes"]:
        flags.append({"kind": "conflict", "refs": [n["note_id"]], "message": f"note {n['note_id']} by {n['author_name']} on this inspection: {n['text']}"})
    others = [x for x in related["overlay"] if x["seq"] != e["seq"] and x["field"] == field]
    for x in others:
        flags.append({"kind": "conflict", "refs": [x["entry_id"]], "message": f"earlier correction {x['entry_id']} by {x['author_name']} set {field} to {x['new_value']} ({x['status']})"})
    after["spill_class"] = _spill_class(ctx, after["spill_est_volume_bbl"]) if after["real_spill"] else None
    consequences = {}
    if field in ("wellhead_temp_c", "ambient_temp_c"):
        raw = _rows(ctx, "SELECT raw_wellhead_temp_c AS w, raw_ambient_temp_c AS a FROM v_inspection WHERE inspection_id = ?", (inspection_id,))[0]
        before = None if after["threshold_c"] is None else int(round(raw["w"] - raw["a"], 1) >= after["threshold_c"])
        word = {1: "producing", 0: "not producing", None: "unknown"}
        consequences["status_before"], consequences["status_after"] = word[before], word[after["producing"]]
        latest = _rows(ctx, "SELECT inspection_id FROM v_latest_inspection WHERE asset_id = ?", (asset,))[0]["inspection_id"]
        consequences["is_latest_inspection"] = latest == inspection_id
        if before != after["producing"]:
            flags.append({"kind": "status_changed", "refs": [inspection_id],
                          "message": f"{asset} on {after['day']}: was {word[before]}, now {word[after['producing']]} "
                                     f"({after['delta_c']} °C above air vs limit {after['threshold_c']} °C)"})
    if field in ("spill_est_volume_bbl", "spill_detected"):
        old_class = _spill_class(ctx, float(e["original_value"])) if field == "spill_est_volume_bbl" and e["original_value"] else None
        consequences["class_before"], consequences["class_after"] = old_class, after["spill_class"]
        cc = compliance_check(ctx, asset_ids=[asset], start=after["day"], end=ctx.today.isoformat())
        consequences["compliance"] = [b for b in cc["rows"] if b["ref"] == inspection_id] + \
                                     [o for o in cc["summary"]["open_items"] if o["ref"] == inspection_id]
        flags += [f for f in cc["flags"] if inspection_id in f["refs"]]
        if old_class != after["spill_class"]:
            flags.append({"kind": "status_changed", "refs": [inspection_id],
                          "message": f"spill class changed from {old_class} to {after['spill_class']}: "
                                     + "; ".join(f"{c['protocol']}: {c['detail']}" for c in consequences["compliance"])})
    return _result([after], {"recorded": e, "asset_id": asset, "scope": "this inspection only, visible to every user",
                             "consequences": consequences},
                   [{"kind": "overlay", "ref": e["entry_id"], "detail": f"{field}: {e['original_value']} -> {e['new_value']} by {e['author_name']}: {reason}", "source": "overlay"}],
                   flags=flags)


def record_type_rule(ctx, well_type, rule_name, value, reason):
    """Override a manual rule for every well of one type (and only that type), for every user."""
    e = overlay.record_rule(ctx.con, ctx.user_id, well_type, rule_name, value, reason)
    return _result([e], {"recorded": e, "scope": f"all {well_type} wells only; other types keep the manual rule"},
                   [{"kind": "overlay", "ref": e["entry_id"], "detail": f"{well_type} {rule_name}: {e['original_value']} -> {e['new_value']}", "source": "overlay"}])


def record_well_statement(ctx, asset_id, text, claimed_status=None, valid_from=None, valid_to=None):
    """Store shared knowledge a user gives about a well. If claimed_status ('producing' / 'not_producing')
    contradicts the latest inspection, it is stored as an UNCONFIRMED claim and the contradiction is returned."""
    st = well_status(ctx, asset_ids=[asset_id])
    latest = st["rows"][0]
    contradicts = False
    if claimed_status in ("producing", "not_producing") and latest["producing"] is not None:
        contradicts = (claimed_status == "producing") != bool(latest["producing"])
    e = overlay.record_statement(ctx.con, ctx.user_id, asset_id, text, contradicts, valid_from, valid_to)
    others = [x for x in overlay.for_well(ctx.con, asset_id) if x["kind"] in ("note", "claim") and x["seq"] != e["seq"]]
    flags = []
    if contradicts:
        flags.append({"kind": "conflict", "refs": [latest["inspection_id"], e["entry_id"]],
                      "message": f"claim '{claimed_status}' contradicts {latest['inspection_id']} ({latest['inspected_at'][:10]}, by {latest['inspector']}): "
                                 f"wellhead {latest['delta_c']} °C above air vs limit {latest['threshold_c']} °C -> {latest['status']}. Stored as unconfirmed; ask for confirmation"})
    for x in others:
        flags.append({"kind": "other_statement", "refs": [x["entry_id"]], "message": f"{x['author_name']} earlier said: {x['text']} ({x['status']})"})
    for n in latest["active_notes"]:
        flags.append({"kind": "data_note", "refs": [n["note_id"]], "message": f"note {n['note_id']}: {n['text']}"})
    return _result([e], {"recorded": e, "contradicts_data": contradicts, "latest": {k: latest[k] for k in ("inspection_id", "inspected_at", "inspector", "delta_c", "threshold_c", "status")}},
                   st["evidence"] + [{"kind": "overlay", "ref": e["entry_id"], "detail": text, "source": "overlay"}], flags=flags)


def confirm_claim(ctx, entry_id):
    """Confirm an unconfirmed claim (e.g. 'OVL-7'), making it shared knowledge."""
    seq = int(str(entry_id).replace("OVL-", ""))
    e = overlay.confirm(ctx.con, seq, ctx.user_id)
    return _result([e], {"confirmed": e})


def recall_my_conversations(ctx, limit=10):
    """The CURRENT user's own earlier conversations (never anyone else's), newest first, with the wells
    discussed. Use when the user refers to an earlier chat ('the wells we discussed yesterday')."""
    convs = _rows(ctx, "SELECT conversation_id, started_at, title FROM conversations WHERE user_id = ? AND conversation_id <> IFNULL(?, '') "
                       "ORDER BY started_at DESC LIMIT ?", (ctx.user_id, ctx.conversation_id, int(limit)))
    for c in convs:
        turns = _rows(ctx, "SELECT role, text, asset_ids, created_at FROM turns WHERE conversation_id = ? AND user_id = ? ORDER BY seq",
                      (c["conversation_id"], ctx.user_id))
        for t in turns:
            t["asset_ids"] = json.loads(t["asset_ids"] or "[]")
            t["text"] = (t["text"] or "")[:1200]
        c["turns"] = turns
    return _result(convs, {"user_id": ctx.user_id, "conversations": len(convs),
                           "note": "no earlier conversations with this user" if not convs else None})


# --- registry: JSON schemas for the model --------------------------------------------------------

_IDS = {"type": "array", "items": {"type": "string"}, "description": "Well IDs such as WELL-0001"}
_AREA = {"type": "string", "description": "Area ID (e.g. NE-08) or name (e.g. Northeast Block 8)"}
_DATE = {"type": "string", "description": "YYYY-MM-DD, inclusive"}

TOOLS = {
    "well_status": (well_status, {"asset_ids": _IDS, "area_id": _AREA, "only_not_producing": {"type": "boolean"}}, []),
    "inspection_history": (inspection_history, {"asset_id": {"type": "string"}, "start": _DATE, "end": _DATE,
                                                "last_n": {"type": "integer", "description": "only the last N readings"}}, ["asset_id"]),
    "downtime": (downtime, {"start": _DATE, "end": _DATE, "area_id": _AREA, "asset_ids": _IDS, "top_n": {"type": "integer"}}, []),
    "spills": (spills, {"start": _DATE, "end": _DATE, "area_id": _AREA, "asset_ids": _IDS,
                        "min_class": {"type": "string", "enum": ["minor", "moderate", "reportable", "major"]},
                        "group_by": {"type": "string", "enum": ["area_month", "area", "well"]}}, []),
    "repairs": (repairs, {"start": _DATE, "end": _DATE, "asset_ids": _IDS, "area_id": _AREA, "equipment_kind": {"type": "string"},
                          "maintenance_type": {"type": "string", "enum": ["scheduled", "corrective"]}, "open_only": {"type": "boolean"}}, []),
    "maintenance_escalation": (maintenance_escalation, {"area_id": _AREA, "asset_ids": _IDS, "equipment_kind": {"type": "string"}}, []),
    "compliance_check": (compliance_check, {"asset_ids": _IDS, "area_id": _AREA, "start": _DATE, "end": _DATE}, []),
    "search_docs": (search_docs, {"query": {"type": "string"}, "doc_kind": {"type": "string", "enum": ["manual", "sop", "report"]},
                                  "asset_id": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    "read_doc_section": (read_doc_section, {"doc_id": {"type": "string", "description": "e.g. OM-T200, SOP-SR, or a maintenance ID like MNT-0151-025"},
                                            "section": {"type": "string"}}, ["doc_id"]),
    "well_context": (well_context, {"asset_id": {"type": "string"}}, ["asset_id"]),
    "record_correction": (record_correction, {"inspection_id": {"type": "string"},
                                              "field": {"type": "string", "enum": sorted(overlay.CORRECTABLE_FIELDS)},
                                              "new_value": {"type": "string"}, "reason": {"type": "string"}},
                          ["inspection_id", "field", "new_value", "reason"]),
    "record_type_rule": (record_type_rule, {"well_type": {"type": "string"}, "rule_name": {"type": "string", "description": "e.g. rca_corrective_repairs"},
                                            "value": {"type": "number"}, "reason": {"type": "string"}}, ["well_type", "rule_name", "value", "reason"]),
    "record_well_statement": (record_well_statement, {"asset_id": {"type": "string"}, "text": {"type": "string"},
                                                      "claimed_status": {"type": "string", "enum": ["producing", "not_producing"]},
                                                      "valid_from": _DATE, "valid_to": _DATE}, ["asset_id", "text"]),
    "confirm_claim": (confirm_claim, {"entry_id": {"type": "string"}}, ["entry_id"]),
    "recall_my_conversations": (recall_my_conversations, {"limit": {"type": "integer"}}, []),
}


def tool_definitions():
    """Anthropic tool definitions, in a fixed order so the prompt prefix stays cacheable."""
    defs = []
    for name, (fn, props, required) in TOOLS.items():
        doc = " ".join((fn.__doc__ or "").split())
        defs.append({"name": name, "description": doc,
                     "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}})
    return defs


def run(ctx, name, args):
    """Execute one tool. Raises ToolError / OverlayError for bad input."""
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    fn, props, required = TOOLS[name]
    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"unknown arguments {sorted(unknown)}")
    missing = [r for r in required if r not in args]
    if missing:
        raise ToolError(f"missing arguments {missing}")
    return fn(ctx, **args)
