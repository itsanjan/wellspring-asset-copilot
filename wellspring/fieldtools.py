"""Tools for operator notes and the documents added with the test dataset: gas-odor responses (OPS-1), temporary
bypasses (OPS-2), status conflicts (OPS-3), freeze checks (OPS-5), the supplier recall, the regulator's letter,
shut-in delays, temperature by area, teams, and what the data does not contain.

Same contract as tools.py: each returns {rows, summary, evidence, visuals, flags}; rules come from the documents,
with their source, and anything the data cannot show is reported as missing, never filled in.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta

from . import overlay
from .fieldnotes import SYMPTOMS
from .tools import (_AREA, _DATE, _IDS, MONTHS, TOOLS, ToolError, _area_names, _d, _resolve_area, _result, _rows, _rule, _rule_on, _status_now,
                    _table, _users, _well_filter, _window)


def _ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _hours(a, b):
    return round((_ts(b) - _ts(a)).total_seconds() / 3600, 1)


def _src(asset_id, f):
    return f"assets/{asset_id}/{f}"


def _stamp(ctx):
    """Timestamp for a new user entry: the dataset's today with the current clock time, so entries sort after the data."""
    return f"{ctx.today.isoformat()}T{datetime.utcnow():%H:%M:%S}Z"


def _end_of(ctx):
    return ctx.today.isoformat() + "T23:59:59Z"


# --- OPS-1 gas odor response -------------------------------------------------------------------------

def gas_reports(ctx, start=None, end=None, area_id=None, asset_ids=None):
    """Operator notes reporting gas (odor, rotten-egg smell, H2S alarm) and whether a field response met the SOP-OPS
    OPS-1 deadline: a maintenance job or a MANUAL inspection (drones do not count) starting after the note, within
    24 h (12 h for reports from 1 Jan 2027). Also gives any H2S reading recorded by the responding crew."""
    s, e = _window(ctx, start, end)
    where, params = _well_filter(ctx, asset_ids, area_id)
    notes = _rows(ctx, f"""SELECT n.note_id, n.asset_id, w.area_id, n.created_at, n.author_user_id, n.text FROM notes n
                           JOIN wells w ON w.asset_id = n.asset_id WHERE n.topic = 'gas' AND SUBSTR(n.created_at, 1, 10) BETWEEN ? AND ?
                           {where} ORDER BY n.created_at""", [s, e] + params)
    users, areas = _users(ctx), _area_names(ctx)
    rows, evidence, flags = [], [], []
    for n in notes:
        rule = _rule_on(ctx, "ops1_response_hours", n["created_at"])
        if not rule:
            raise ToolError("SOP-OPS OPS-1 response time not found in the SOPs")
        limit = rule["value"]
        job = _rows(ctx, "SELECT maintenance_id, started_at, issue FROM maintenance WHERE asset_id = ? AND started_at > ? "
                         "ORDER BY started_at LIMIT 1", (n["asset_id"], n["created_at"]))
        ins = _rows(ctx, "SELECT inspection_id, inspected_at FROM inspections WHERE asset_id = ? AND method = 'manual' "
                         "AND inspected_at > ? ORDER BY inspected_at LIMIT 1", (n["asset_id"], n["created_at"]))
        drones = _rows(ctx, "SELECT inspection_id, inspected_at FROM inspections WHERE asset_id = ? AND method = 'drone' "
                            "AND inspected_at > ? AND inspected_at <= ? ORDER BY inspected_at",
                       (n["asset_id"], n["created_at"], (_ts(n["created_at"]) + timedelta(hours=limit)).strftime("%Y-%m-%dT%H:%M:%SZ")))
        cands = [("maintenance job", j["maintenance_id"], j["started_at"], j["issue"]) for j in job] + \
                [("manual inspection", i["inspection_id"], i["inspected_at"], None) for i in ins]
        resp = min(cands, key=lambda c: c[2]) if cands else None
        due = _ts(n["created_at"]) + timedelta(hours=limit)
        hours = _hours(n["created_at"], resp[2]) if resp else None
        if resp and hours <= limit:
            verdict = "Met"
        elif resp or due.date() < ctx.today or due.strftime("%Y-%m-%dT%H:%M:%SZ") <= _end_of(ctx) and due.date() <= ctx.today:
            verdict = "Missed"
        else:
            verdict = "Open (deadline not passed)"
        h2s = None
        if resp and resp[0] == "maintenance job":
            d = _rows(ctx, "SELECT h2s_max_ppm, report_path FROM report_details WHERE maintenance_id = ?", (resp[1],))
            h2s = d[0]["h2s_max_ppm"] if d else None
        row = {"asset_id": n["asset_id"], "area": areas.get(n["area_id"]), "note_id": n["note_id"],
               "reported_at": n["created_at"], "reported_by": users.get(n["author_user_id"], n["author_user_id"]), "note": n["text"],
               "response": f"{resp[1]} ({resp[0]}) at {resp[2][:16].replace('T', ' ')}" if resp else "none",
               "response_id": resp[1] if resp else None, "response_kind": resp[0] if resp else None,
               "response_at": resp[2] if resp else None, "hours_to_response": hours, "deadline_hours": limit,
               "deadline_rule": rule["source"], "result": verdict,
               "drone_flights_not_counted": [f"{x['inspection_id']} {x['inspected_at'][:16]}" for x in drones],
               "h2s_reading_ppm": h2s,
               "h2s_note": (f"highest H2S reading {h2s:g} ppm in report {resp[1]}" if h2s is not None else
                            "no H2S reading on record: the response was not a repair job with a report" if resp and resp[0] != "maintenance job" or not resp else
                            "the responding job has no report with an H2S reading")}
        rows.append(row)
        evidence.append({"kind": "note", "ref": n["note_id"], "detail": f"{n['created_at']} {n['text']}", "source": _src(n["asset_id"], "context.jsonl")})
        if resp:
            evidence.append({"kind": "repair" if resp[0] == "maintenance job" else "inspection", "ref": resp[1],
                             "detail": f"{resp[0]} started {resp[2]}", "source": _src(n["asset_id"], "maintenance.jsonl" if resp[0] == "maintenance job" else "inspections.jsonl")})
        if drones and verdict != "Met":
            flags.append({"kind": "not_counted", "refs": [n["note_id"]] + [x["inspection_id"] for x in drones],
                          "message": f"{n['asset_id']}: drone flight(s) {', '.join(x['inspection_id'] for x in drones)} after the report do not count as a response (OPS-1)"})
    for rule_src in sorted({r["deadline_rule"] for r in rows}):
        evidence.append({"kind": "sop", "ref": rule_src, "detail": "gas odor response: maintenance job or manual inspection within 24 h (12 h from 1 Jan 2027)", "source": "fleet/sops/operations-sop-rev2.md"})
    cols = ["asset_id", "area", "reported_at", "response", "hours_to_response", "deadline_hours", "result"]
    summary = {"window": [s, e], "reports": len(rows), "met": sum(r["result"] == "Met" for r in rows),
               "missed": sum(r["result"] == "Missed" for r in rows), "open": sum(r["result"].startswith("Open") for r in rows)}
    return _result(rows, summary, evidence, {"table": _table(rows, cols)}, flags)


# --- OPS-2 temporary bypasses ------------------------------------------------------------------------

def _approvals(ctx):
    """asset_id -> approval {moc_ids, effective_from, valid_until}. An extending memo replaces the expiry."""
    mocs = {m["moc_id"]: m for m in _rows(ctx, "SELECT * FROM moc_memos")}
    named = {}
    for r in _rows(ctx, "SELECT moc_id, asset_id FROM moc_wells"):
        named.setdefault(r["asset_id"], []).append(r["moc_id"])
    out = {}
    for well, ids in named.items():
        chain = sorted(set(ids), key=lambda i: mocs[i]["memo_date"] or "")
        originals = [mocs[i] for i in chain if not mocs[i]["extends_moc"]] or [mocs[chain[0]]]
        expiry = max(mocs[i]["expires_on"] for i in chain if mocs[i]["expires_on"])
        out[well] = {"moc_ids": chain, "effective_from": min(m["effective_from"] for m in originals if m["effective_from"]),
                     "valid_until": expiry, "approved_by": "; ".join(sorted({mocs[i]["approved_by"] for i in chain if mocs[i]["approved_by"]}))}
    return out, mocs


def bypasses(ctx, area_id=None, asset_ids=None):
    """Temporary flowline bypasses from operator notes (put in / taken out), checked against the management-of-change
    memos (SOP-OPS OPS-2): Allowed, Expired (still in after the approval's last day) or Not approved (well in no MOC).
    Returns every bypass, past and present; in_place_now marks the ones still in."""
    where, params = _well_filter(ctx, asset_ids, area_id)
    notes = _rows(ctx, f"""SELECT n.note_id, n.asset_id, w.area_id, n.created_at, n.text, n.topic FROM notes n JOIN wells w ON w.asset_id = n.asset_id
                           WHERE n.topic IN ('bypass_in', 'bypass_out') {where} ORDER BY n.asset_id, n.created_at""", params)
    approvals, mocs = _approvals(ctx)
    areas, today = _area_names(ctx), ctx.today.isoformat()
    periods = []
    for n in notes:
        if n["topic"] == "bypass_in":
            periods.append({"asset_id": n["asset_id"], "area": areas.get(n["area_id"]), "in_note": n, "out_note": None})
        else:
            open_ = [p for p in periods if p["asset_id"] == n["asset_id"] and p["out_note"] is None]
            if open_:
                open_[-1]["out_note"] = n
            else:
                periods.append({"asset_id": n["asset_id"], "area": areas.get(n["area_id"]), "in_note": None, "out_note": n})
    rows, evidence, flags = [], [], []
    for p in periods:
        a = approvals.get(p["asset_id"])
        bin_ = p["in_note"]["created_at"][:10] if p["in_note"] else None
        bout = p["out_note"]["created_at"][:10] if p["out_note"] else None
        last_day = bout or today
        if not a:
            status, why = "Not approved", "well is not named in any MOC memo"
        elif last_day > a["valid_until"]:
            status, why = "Expired", f"still in after the approval ended on {a['valid_until']}" + (" (still in today)" if not bout else f" (removed {bout})")
        else:
            status, why = "Allowed", f"named in {', '.join(a['moc_ids'])}, valid until {a['valid_until']}"
        if a and bin_ and bin_ < a["effective_from"]:
            why += f"; note: put in on {bin_}, before the approval took effect on {a['effective_from']}"
        row = {"asset_id": p["asset_id"], "area": p["area"], "bypass_in": bin_ or "unknown (no put-in note)",
               "bypass_out": bout or "still in", "in_place_now": bout is None, "approval": ", ".join(a["moc_ids"]) if a else "none",
               "approval_valid_until": a["valid_until"] if a else None, "status": status, "why": why,
               "notes": [x["note_id"] for x in (p["in_note"], p["out_note"]) if x]}
        rows.append(row)
        for x in (p["in_note"], p["out_note"]):
            if x:
                evidence.append({"kind": "note", "ref": x["note_id"], "detail": f"{x['created_at']} {x['text']}", "source": _src(p["asset_id"], "context.jsonl")})
        if status != "Allowed":
            flags.append({"kind": "violation", "refs": row["notes"], "message": f"{p['asset_id']}: bypass {status.lower()}: {why} (SOP-OPS OPS-2)"})
    for m in mocs.values():
        evidence.append({"kind": "sop", "ref": m["moc_id"], "detail": f"{m['title']}; expires {m['expires_on']}" + (f"; extends {m['extends_moc']}" if m["extends_moc"] else ""), "source": m["path"]})
    evidence.append({"kind": "sop", "ref": "SOP-OPS OPS-2", "detail": "bypass allowed only on a well named in an unexpired MOC; an extension replaces the expiry", "source": "fleet/sops/operations-sop-rev2.md"})
    rows.sort(key=lambda r: (not r["in_place_now"], r["status"] == "Allowed", r["asset_id"]))
    cols = ["asset_id", "area", "bypass_in", "bypass_out", "approval", "approval_valid_until", "status"]
    summary = {"bypasses": len(rows), "in_place_now": [r["asset_id"] + " (" + r["status"] + ")" for r in rows if r["in_place_now"]],
               "violations_past_or_present": [r["asset_id"] + " (" + r["status"] + ")" for r in rows if r["status"] != "Allowed"],
               "mocs": {m["moc_id"]: {"wells": [w for w, a in approvals.items() if m["moc_id"] in a["moc_ids"]], "expires_on": m["expires_on"],
                                      "extends": m["extends_moc"]} for m in mocs.values()}}
    return _result(rows, summary, evidence, {"table": _table(rows, cols)}, flags)


# --- OPS-3 status conflicts ------------------------------------------------------------------------

def status_conflicts(ctx, area_id=None, asset_ids=None):
    """Wells where the latest inspection and the latest shut-in/restart operator note disagree about whether the
    well is producing, and the real status now (SOP-OPS OPS-3: the more recent one wins; a restart note gives
    'producing (unconfirmed)' until an inspection confirms it)."""
    where, params = _well_filter(ctx, asset_ids, area_id, alias="v")
    latest = _rows(ctx, f"SELECT v.* FROM v_latest_inspection v WHERE 1=1 {where} ORDER BY v.asset_id", params)
    areas, users = _area_names(ctx), _users(ctx)
    rows, evidence, flags = [], [], []
    for v in latest:
        w = _rows(ctx, "SELECT * FROM wells WHERE asset_id = ?", (v["asset_id"],))[0]
        if w.get("status") == "plugged_and_abandoned":
            continue
        n = _rows(ctx, "SELECT note_id, created_at, author_user_id, text, status_effect FROM notes WHERE asset_id = ? AND note_type = 'operator_note' "
                       "AND status_effect IS NOT NULL AND created_at <= ? ORDER BY created_at DESC LIMIT 1", (v["asset_id"], _end_of(ctx)))
        if not n or v["producing"] is None:
            continue
        n = n[0]
        note_says = 1 if n["status_effect"] == "restart" else 0
        if note_says == v["producing"]:
            continue
        prev = _rows(ctx, "SELECT MAX(inspected_at) t FROM inspections WHERE asset_id = ? AND inspected_at < ?", (v["asset_id"], v["inspected_at"]))[0]["t"]
        if prev and n["created_at"] < prev:
            continue  # an older note already superseded by more than one inspection: not a live disagreement
        now = _status_now(ctx, v, w)
        newer = "operator note" if n["created_at"] > v["inspected_at"] else "inspection"
        reading = f"{'producing' if v['producing'] else 'not producing'} ({v['delta_c']} °C above air, limit {v['threshold_c']:g} °C)"
        why = (f"the operator note ({n['created_at'][:16].replace('T', ' ')}) is newer than the inspection ({v['inspected_at'][:16].replace('T', ' ')}); OPS-3"
               + ("; a restart is unconfirmed until an inspection shows it producing" if n["status_effect"] == "restart" and newer == "operator note" else "")
               if newer == "operator note" else
               f"the inspection ({v['inspected_at'][:16].replace('T', ' ')}) is newer than the note ({n['created_at'][:16].replace('T', ' ')}); OPS-3")
        if v["data_correction_ids"] or v["overlay_ids"]:
            why += f"; the inspection reading was corrected ({v['data_correction_ids'] or v['overlay_ids']}): raw {v['raw_wellhead_temp_c']} °C -> {v['wellhead_temp_c']} °C"
        open_job = _rows(ctx, "SELECT maintenance_id, SUBSTR(completed_at,1,10) planned_end, issue FROM maintenance WHERE asset_id = ? AND started_at <= ? "
                              "AND completed_at > ?", (v["asset_id"], _end_of(ctx), _end_of(ctx)))
        if open_job and n["status_effect"] == "restart":
            j = open_job[0]
            why += f"; but repair {j['maintenance_id']} ({j['issue']}) is still open, planned to finish {j['planned_end']}, so the restart is doubtful"
            flags.append({"kind": "conflict", "refs": [n["note_id"], j["maintenance_id"]],
                          "message": f"{v['asset_id']}: note says restarted, but repair {j['maintenance_id']} is still in progress until {j['planned_end']}"})
            evidence.append({"kind": "repair", "ref": j["maintenance_id"], "detail": f"open until {j['planned_end']}: {j['issue']}", "source": _src(v["asset_id"], "maintenance.jsonl")})
        row = {"asset_id": v["asset_id"], "area": areas.get(v["area_id"]), "inspection_id": v["inspection_id"], "inspected_at": v["inspected_at"],
               "inspection_shows": reading, "note_id": n["note_id"], "note_at": n["created_at"], "note_by": users.get(n["author_user_id"], n["author_user_id"]),
               "note_says": n["text"], "status_now": now["status"], "why": why, "method": v["method"]}
        rows.append(row)
        evidence += [{"kind": "inspection", "ref": v["inspection_id"], "detail": f"{v['inspected_at']} {reading}", "source": _src(v["asset_id"], "inspections.jsonl")},
                     {"kind": "note", "ref": n["note_id"], "detail": f"{n['created_at']} {n['text']}", "source": _src(v["asset_id"], "context.jsonl")}]
        if v["data_correction_ids"]:
            for cid in v["data_correction_ids"].split(","):
                c = _rows(ctx, "SELECT text FROM notes WHERE note_id = ?", (cid,))[0]
                evidence.append({"kind": "note", "ref": cid, "detail": c["text"], "source": _src(v["asset_id"], "context.jsonl")})
    evidence.append({"kind": "sop", "ref": "SOP-OPS OPS-3", "detail": "status = the more recent of the latest inspection and the latest shut-in/restart note; restart is unconfirmed until inspected", "source": "fleet/sops/operations-sop-rev2.md"})
    for r in rows:
        if "unconfirmed" in r["status_now"]:
            flags.append({"kind": "unconfirmed", "refs": [r["note_id"]], "message": f"{r['asset_id']}: restart reported by {r['note_by']} but not yet confirmed by an inspection"})
    cols = ["asset_id", "area", "inspection_id", "inspected_at", "inspection_shows", "note_at", "note_says", "status_now"]
    return _result(rows, {"conflicts": len(rows), "wells_checked": len(latest),
                          "not_counted": "drive-trip resets ('restarted from the panel') on a running ESP are equipment remarks, not a restart of a "
                                         "shut-in well (OPS-3); notes older than the last two inspections are already superseded"},
                   evidence, {"table": _table(rows, cols)}, flags)


# --- Q7: operator-reported symptoms -----------------------------------------------------------------

def _esp_warning(ctx, asset_id, since):
    """T500 early warning: the wellhead-to-air difference lower than the reading before, three times in a row
    (four readings), falling 3 °C or more in total, while producing. Returns the latest such run since `since`."""
    r = _rows(ctx, "SELECT inspection_id, day, delta_c, producing FROM v_inspection WHERE asset_id = ? ORDER BY inspected_at", (asset_id,))
    hit = None
    for i in range(3, len(r)):
        win = r[i - 3:i + 1]
        if all(x["producing"] == 1 for x in win) and all(win[k + 1]["delta_c"] < win[k]["delta_c"] for k in range(3)) \
                and win[0]["delta_c"] - win[3]["delta_c"] >= 3 and win[3]["day"] >= since:
            hit = {"inspections": [x["inspection_id"] for x in win], "deltas": [x["delta_c"] for x in win], "third_drop_on": win[3]["day"]}
    return hit


def operator_concerns(ctx, days=30, top_n=10, area_id=None):
    """Wells whose operators wrote notes in the last `days` days that the well sounds or behaves wrong (squeal,
    rattle, fluid pound, tagging bottom, drive trips, amps hunting, leaks) and that have had no repair since,
    ranked by how likely they are to need a repair (spill clean-ups excluded), with the notes quoted and other
    warning signs: T500 falling-temperature early warning, repeat corrective repairs, status now."""
    since = (ctx.today - timedelta(days=int(days))).isoformat()
    where, params = _well_filter(ctx, None, area_id)
    marks = ",".join("?" * len(SYMPTOMS))
    notes = _rows(ctx, f"""SELECT n.note_id, n.asset_id, w.area_id, w.well_type, n.created_at, n.text, n.topic, n.equipment_kind
                           FROM notes n JOIN wells w ON w.asset_id = n.asset_id WHERE n.topic IN ({marks}) AND SUBSTR(n.created_at, 1, 10) >= ?
                           AND IFNULL(w.status, '') <> 'plugged_and_abandoned' {where} ORDER BY n.created_at""", list(SYMPTOMS) + [since] + params)
    by_well = {}
    for n in notes:
        by_well.setdefault(n["asset_id"], []).append(n)
    areas, year_ago = _area_names(ctx), (ctx.today - timedelta(days=365)).isoformat()
    rows, repaired, evidence = [], [], []
    for well, ns in by_well.items():
        first = ns[0]["created_at"]
        eq = sorted({n["equipment_kind"] for n in ns})
        # Any maintenance job after the first symptom note counts as a repair (every maintenance record is a repair).
        fix = _rows(ctx, "SELECT maintenance_id, started_at, issue, equipment_kind FROM maintenance WHERE asset_id = ? AND started_at >= ? "
                         "ORDER BY started_at LIMIT 1", (well, first))
        if not fix:  # a repair already under way on the same equipment counts too
            fix = _rows(ctx, f"SELECT maintenance_id, started_at, issue, equipment_kind FROM maintenance WHERE asset_id = ? AND started_at < ? "
                             f"AND completed_at > ? AND issue NOT LIKE '%spill%' AND equipment_kind IN ({','.join('?' * len(eq))})",
                        [well, first, _end_of(ctx)] + eq)
        if fix:
            repaired.append({"asset_id": well, "repair": fix[0]["maintenance_id"], "started": fix[0]["started_at"][:10], "issue": fix[0]["issue"],
                             "in_progress": fix[0]["started_at"] < first})
            continue
        kinds = sorted({n["equipment_kind"] for n in ns})
        hist = _rows(ctx, "SELECT maintenance_id, SUBSTR(started_at,1,10) d, equipment_kind, issue FROM maintenance WHERE asset_id = ? AND maintenance_type = 'corrective' "
                          f"AND SUBSTR(started_at,1,10) > ? AND equipment_kind IN ({','.join('?' * len(kinds))}) ORDER BY started_at", [well, year_ago] + kinds)
        last = _rows(ctx, "SELECT maintenance_id, SUBSTR(started_at,1,10) d, issue, maintenance_type FROM maintenance WHERE asset_id = ? AND issue NOT LIKE 'Follow-up%' "
                          "AND started_at <= ? ORDER BY started_at DESC LIMIT 1", (well, _end_of(ctx)))
        v = _rows(ctx, "SELECT * FROM v_latest_inspection WHERE asset_id = ?", (well,))[0]
        w = _rows(ctx, "SELECT * FROM wells WHERE asset_id = ?", (well,))[0]
        status = _status_now(ctx, v, w)
        signs, score = [], len(ns) + len({n["topic"] for n in ns}) - 1
        recent = [n for n in ns if n["created_at"][:10] >= (ctx.today - timedelta(days=7)).isoformat()]
        score += len(recent) > 0
        if any(n["topic"] == "leak" for n in ns):
            score += 3
            signs.append("leak found and well shut in")
        if hist:
            score += min(len(hist), 3)
            signs.append(f"{len(hist)} corrective repair(s) on the {'/'.join(kinds)} in the last 12 months ({', '.join(h['maintenance_id'] for h in hist)})")
        esp = _esp_warning(ctx, well, (ctx.today - timedelta(days=60)).isoformat()) if w["well_type"] == "T500" else None
        if esp:
            score += 3
            signs.append(f"T500 early warning: wellhead-to-air difference fell {esp['deltas'][0]} -> {esp['deltas'][-1]} °C over "
                         f"{', '.join(esp['inspections'])} (OM-T500 section 5: ESP drive and insulation check within 14 days)")
            evidence.append({"kind": "manual", "ref": "OM-T500 section 5", "detail": "falling difference three readings in a row, 3 °C or more: schedule ESP check", "source": "fleet/well-types/T500.md"})
        rec = recalled_pumps(ctx, asset_ids=[well]) if "downhole_pump" in kinds else None
        if rec and rec["rows"]:
            p = rec["rows"][0]
            score += 2
            signs.append(f"recalled pump still in the well: lot {p['lot']} fitted by {p['installed_by']} ({rec['summary']['recall']}: plungers can crack and lose fluid)")
            evidence += rec["evidence"][:2]
        if status["producing"] == 0:
            score += 1
            signs.append(f"not producing now ({status['status']})")
        rows.append({"asset_id": well, "area": areas.get(ns[0]["area_id"]), "well_type": w["well_type"], "score": score,
                     "evidence_notes": [f"{n['created_at'][:10]} {n['note_id']}: \"{n['text']}\"" for n in ns],
                     "note_evidence": "; ".join(f"{n['created_at'][:10]}: \"{n['text']}\"" for n in ns[-2:]),
                     "symptoms": sorted({n["topic"] for n in ns}), "equipment": kinds,
                     "other_warning_signs": "; ".join(signs) or "none found in the data",
                     "last_repair": f"{last[0]['maintenance_id']} {last[0]['d']} ({last[0]['issue']})" if last else "none on record",
                     "status_now": status["status"], "esp_warning": esp})
        evidence += [{"kind": "note", "ref": n["note_id"], "detail": f"{n['created_at']} {n['text']}", "source": _src(well, "context.jsonl")} for n in ns]
        evidence += [{"kind": "repair", "ref": h["maintenance_id"], "detail": f"{h['d']} corrective {h['equipment_kind']}: {h['issue']}", "source": _src(well, "maintenance.jsonl")} for h in hist]
    rows.sort(key=lambda r: (-r["score"], r["asset_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    top = rows[: int(top_n)] if top_n else rows
    cols = ["rank", "asset_id", "area", "note_evidence", "other_warning_signs", "last_repair"]
    summary = {"since": since, "wells_with_symptom_notes": len(by_well), "already_repaired_since": repaired,
               "not_repaired": len(rows), "ranking_method": "points: each symptom note, extra distinct symptoms, a note in the last 7 days, "
               "a leak (+3), corrective repairs on the same equipment in 12 months (up to +3), T500 early warning (+3), recalled pump in the well (+2), "
               "not producing now (+1). Wells with any maintenance job since the first note, or a repair under way on that equipment, are excluded"}
    return _result(top, summary, evidence, {"table": _table(top, cols)})


# --- supplier recall ---------------------------------------------------------------------------------

def _pump_claims(ctx):
    """User reports that a recalled pump was swapped (overlay claims with field 'pump_replaced')."""
    return [e for e in _rows(ctx, "SELECT o.*, 'OVL-' || o.seq AS entry_id, u.name AS author_name FROM overlay_entries o "
                                  "LEFT JOIN users u ON u.user_id = o.author_user_id WHERE o.field = 'pump_replaced' AND o.status <> 'superseded' ORDER BY o.seq")]


def recalled_pumps(ctx, area_id=None, team_id=None, asset_ids=None):
    """Recalled downhole pumps still in the ground: the pump fitted most recently on each well (the 'Parts installed'
    line of its repair reports) is from a lot in a supplier recall notice. Gives the install job and its cost, and
    any user report that a pump was swapped (shown with who said it; not a work order, so not counted as replaced)."""
    recalls = _rows(ctx, "SELECT * FROM recalls")
    if not recalls:
        return _result([], {"note": "no supplier recall notice in the documents"})
    lots = {r["lot"]: r for r in recalls}
    where, params = _well_filter(ctx, asset_ids, area_id)
    if team_id:
        t = _rows(ctx, "SELECT team_id FROM teams WHERE team_id = ? OR LOWER(name) = LOWER(?)", (team_id, team_id))
        if not t:
            raise ToolError(f"unknown team {team_id!r}")
        where += " AND w.team_id = ?"
        params.append(t[0]["team_id"])
    installs = _rows(ctx, f"""SELECT d.asset_id, w.area_id, w.team_id, w.status, d.maintenance_id, m.started_at, m.completed_at, m.issue, m.cost_total,
                                     m.cost_original_total, d.parts_model, d.parts_serial, d.parts_lot, d.report_path
                              FROM report_details d JOIN maintenance m USING (maintenance_id) JOIN wells w ON w.asset_id = d.asset_id
                              WHERE d.parts_lot IS NOT NULL AND m.started_at <= ? {where} ORDER BY d.asset_id, m.started_at""", [_end_of(ctx)] + params)
    current = {}
    for i in installs:
        current[i["asset_id"]] = i  # last one wins: the pump in the well today
    claims = {}
    for c in _pump_claims(ctx):
        claims.setdefault(c["target_ref"], []).append(c)
    areas, teams = _area_names(ctx), {t["team_id"]: t["name"] for t in _rows(ctx, "SELECT * FROM teams")}
    rows, replaced, evidence, flags = [], [], [], []
    for well, i in sorted(current.items()):
        hist = [x for x in installs if x["asset_id"] == well]
        recalled_before = [x for x in hist[:-1] if x["parts_lot"] in lots]
        if i["parts_lot"] not in lots:
            if recalled_before:
                replaced.append({"asset_id": well, "recalled_lot": recalled_before[-1]["parts_lot"], "replaced_by": i["maintenance_id"],
                                 "on": i["started_at"][:10], "new_lot": i["parts_lot"]})
                evidence.append({"kind": "report", "ref": i["maintenance_id"], "detail": f"{i['started_at'][:10]} {i['issue']}: fitted lot {i['parts_lot']}", "source": i["report_path"]})
            continue
        if i["status"] == "plugged_and_abandoned":
            continue
        user = claims.get(well, [])
        row = {"asset_id": well, "area": areas.get(i["area_id"]), "area_id": i["area_id"], "team": teams.get(i["team_id"], i["team_id"]),
               "pump": f"{i['parts_model']} {i['parts_serial']}", "lot": i["parts_lot"], "installed_by": i["maintenance_id"],
               "installed_on": i["started_at"][:10], "install_cost_usd": i["cost_total"],
               "status": "in the ground (records)", "user_reports": [f"{c['entry_id']} {c['author_name']} ({c['created_at'][:10]}, {c['status']}): {c['text']}" for c in user]}
        if any(c["status"] == "active" for c in user):
            row["status"] = "reported swapped by a user, no work order yet"
        elif user:
            row["status"] = "in the ground (records); an unconfirmed report says swapped"
        rows.append(row)
        evidence.append({"kind": "report", "ref": i["maintenance_id"], "detail": f"{i['started_at'][:10]} fitted {i['parts_model']} serial {i['parts_serial']} lot {i['parts_lot']}, USD {i['cost_total']:g}", "source": i["report_path"]})
        for c in user:
            evidence.append({"kind": "overlay", "ref": c["entry_id"], "detail": f"{c['author_name']}: {c['text']} ({c['status']})", "source": "overlay"})
            flags.append({"kind": "user_report" if c["status"] == "active" else "unconfirmed_claim", "refs": [c["entry_id"]],
                          "message": f"{well}: {c['author_name']} said on {c['created_at'][:10]}: '{c['text']}' ({c['status']}; no work order in the maintenance records)"})
        if i["cost_original_total"]:
            flags.append({"kind": "cost_revised", "refs": [i["maintenance_id"]], "message": f"{i['maintenance_id']}: cost revised from {i['cost_original_total']:g} to {i['cost_total']:g} USD"})
    r0 = recalls[0]
    evidence.append({"kind": "sop", "ref": r0["notice_id"], "detail": f"{r0['model']} lots {', '.join(sorted(lots))}: replace by {r0['replace_by']}; {r0['excludes'] or ''}", "source": r0["path"]})
    rod_wells = _rows(ctx, f"SELECT COUNT(DISTINCT e.asset_id) n FROM equipment e JOIN wells w ON w.asset_id = e.asset_id WHERE e.kind = 'downhole_pump' "
                           f"AND IFNULL(w.status, '') <> 'plugged_and_abandoned' {where}", params)[0]["n"]
    by_area = {}
    for r in rows:
        by_area[r["area"]] = by_area.get(r["area"], 0) + 1
    overdue = ctx.today.isoformat() > (r0["replace_by"] or "9999")
    cols = ["asset_id", "area", "lot", "installed_by", "installed_on", "install_cost_usd", "status"]
    summary = {"recall": r0["notice_id"], "recalled_lots": sorted(lots), "replace_by": r0["replace_by"], "past_deadline": overdue,
               "still_in_ground": len(rows), "by_area": by_area, "already_replaced": replaced,
               "rod_pump_wells_without_recorded_lot": rod_wells - len(current),
               "note": "ESP (T500) wells are not affected. Wells whose pump was fitted before lots were recorded have no lot on record and cannot be checked."}
    if overdue and rows:
        flags.append({"kind": "overdue", "refs": [r0["notice_id"]], "message": f"replacement deadline {r0['replace_by']} has passed for {len(rows)} pump(s)"})
    return _result(rows, summary, evidence, {"table": _table(rows, cols)}, flags)


def report_equipment_swap(ctx, asset_id, text, relayed_from=None, swapped_on=None):
    """Record a user's report that a recalled pump on a well was swapped, when no work order is in the system yet.
    First-hand reports are stored as attributed shared notes. If the user is passing on what someone else said
    (relayed_from = that person's name), it is checked against that person's own reports: if they never said it,
    it is stored as an UNCONFIRMED claim and the pump stays on the recall list."""
    if not _rows(ctx, "SELECT 1 FROM wells WHERE asset_id = ?", (asset_id,)):
        raise ToolError(f"unknown well {asset_id}")
    users = {u["name"].lower(): u for u in _rows(ctx, "SELECT user_id, name FROM users")}
    flags, status, reason = [], "active", "first-hand report; work order not yet in the maintenance system"
    if relayed_from:
        src = users.get(relayed_from.lower()) or next((u for n, u in users.items() if n.split()[0] == relayed_from.lower().split()[0]), None)
        said = [c for c in _pump_claims(ctx) if src and c["author_user_id"] == src["user_id"] and c["target_ref"] == asset_id]
        others = [c for c in _pump_claims(ctx) if src and c["author_user_id"] == src["user_id"]]
        if said:
            reason = f"relayed from {src['name']}, who reported it in {said[0]['entry_id']}"
        else:
            status = "unconfirmed"
            reason = f"relayed from {relayed_from}; no record of {src['name'] if src else relayed_from} saying this"
            flags.append({"kind": "conflict", "refs": [c["entry_id"] for c in others],
                          "message": f"{src['name'] if src else relayed_from} has not reported a swap on {asset_id}"
                                     + (f"; their only report(s): {'; '.join(c['target_ref'] + ' (' + c['entry_id'] + ')' for c in others)}" if others else "")
                                     + ". Stored as unconfirmed; the pump stays on the recall list until a work order or the person confirms."})
    seq = overlay._insert(ctx.con, kind="claim" if status == "unconfirmed" else "note", target_type="well", target_ref=asset_id,
                          field="pump_replaced", new_value=swapped_on, text=text, reason=reason, author_user_id=ctx.user_id,
                          created_at=_stamp(ctx), valid_from=swapped_on, status=status)
    ctx.con.commit()
    e = overlay.get(ctx.con, seq)
    rec = recalled_pumps(ctx, asset_ids=[asset_id])
    on_list = bool(rec["rows"])
    if not on_list:
        flags.append({"kind": "context", "refs": [asset_id], "message": f"{asset_id} has no recalled pump on record (records show: {rec['summary'].get('already_replaced') or 'no recalled lot fitted'})"})
    return _result([e], {"recorded": e, "status": status, "reason": reason, "on_recall_list_by_records": on_list,
                         "scope": "shared with every user, attributed to the author; not a work order"},
                   rec["evidence"] + [{"kind": "overlay", "ref": e["entry_id"], "detail": f"{text} ({status})", "source": "overlay"}], flags=flags)


# --- regulator letter ---------------------------------------------------------------------------------

def letter_compliance(ctx, doc_id=None):
    """Spills covered by a regulator letter (its areas, since its date, at or above its volume) and whether each is
    compliant under the letter and the internal HSE note (the clean-up report must say the required sentence,
    e.g. 'Soil samples sent to the lab.'). Spills confirmed as not crude are excluded."""
    letters = _rows(ctx, "SELECT * FROM letters" + (" WHERE doc_id = ?" if doc_id else ""), (doc_id,) if doc_id else ())
    if not letters:
        raise ToolError("no regulator letter found in the documents" + (f" with id {doc_id}" if doc_id else ""))
    lt = letters[0]
    area_ids = [_resolve_area(ctx, a) for a in lt["area_names"].split("|")]
    marks = ",".join("?" * len(area_ids))
    det = _rows(ctx, f"""SELECT v.asset_id, v.area_id, v.inspection_id, v.day, v.spill_est_volume_bbl AS volume_bbl, v.raw_spill_est_volume_bbl AS raw_bbl,
                                v.real_spill, v.overlay_ids, v.data_correction_ids FROM v_inspection v
                         WHERE v.area_id IN ({marks}) AND v.day >= ? AND v.day <= ? AND (v.spill_detected = 1 OR v.raw_spill_detected = 1)
                         ORDER BY v.day""", area_ids + [lt["since"], ctx.today.isoformat()])
    areas = _area_names(ctx)
    rows, excluded, evidence, flags = [], [], [], []
    for s in det:
        if (s["volume_bbl"] or 0) < (lt["min_bbl"] or 0):
            continue
        if not s["real_spill"]:
            why = _rows(ctx, "SELECT note_id, text FROM notes WHERE note_type = 'correction' AND inspection_id = ?", (s["inspection_id"],))
            excluded.append({"asset_id": s["asset_id"], "inspection_id": s["inspection_id"], "reason": why[0]["text"] if why else "corrected as not oil"})
            continue
        jobs = _rows(ctx, "SELECT m.maintenance_id, SUBSTR(m.started_at,1,10) started, m.issue, d.soil_samples_lab, d.cleanup_text, d.interim, d.report_path "
                          "FROM maintenance m LEFT JOIN report_details d USING (maintenance_id) WHERE m.triggered_by_inspection_id = ? ORDER BY m.started_at", (s["inspection_id"],))
        with_cleanup = [j for j in jobs if j["cleanup_text"]]
        if not jobs:
            ok, why, job = False, "no clean-up job on record", "none"
        elif not with_cleanup:
            ok, why, job = False, "repair report has no spill clean-up section", ", ".join(j["maintenance_id"] for j in jobs)
        elif any(j["soil_samples_lab"] for j in with_cleanup):
            j = next(j for j in with_cleanup if j["soil_samples_lab"])
            ok, why, job = True, f"clean-up report {j['maintenance_id']} says \"{lt['required_text']}\"", j["maintenance_id"]
        else:
            j = with_cleanup[0]
            field = "field soil readings only" if "TPH" in (j["cleanup_text"] or "") else "no lab samples recorded"
            ok, why, job = False, f"clean-up report {j['maintenance_id']} has {field}, not \"{lt['required_text']}\"", j["maintenance_id"]
        if ok is False and any(j["interim"] for j in jobs):
            why += " (report is interim; work in progress)"
        disputes = _rows(ctx, "SELECT note_id, author_user_id, text FROM notes WHERE note_type <> 'correction' AND inspection_id IS NULL AND text LIKE ?", (f"%{s['inspection_id']}%",))
        row = {"asset_id": s["asset_id"], "area": areas.get(s["area_id"]), "inspection_id": s["inspection_id"], "day": s["day"],
               "inspection": f"{s['inspection_id']} ({s['day']})", "volume_bbl": s["volume_bbl"], "cleanup_job": job,
               "compliant": "Yes" if ok else "No", "reason": why}
        if s["volume_bbl"] != s["raw_bbl"]:
            row["reason"] += f"; volume corrected from {s['raw_bbl']} to {s['volume_bbl']} bbl ({s['data_correction_ids'] or s['overlay_ids']})"
        rows.append(row)
        evidence.append({"kind": "inspection", "ref": s["inspection_id"], "detail": f"{s['asset_id']} {s['day']} {s['volume_bbl']} bbl", "source": _src(s["asset_id"], "inspections.jsonl")})
        for j in with_cleanup or jobs:
            evidence.append({"kind": "report", "ref": j["maintenance_id"], "detail": (j["cleanup_text"] or j["issue"] or "")[:200], "source": j["report_path"] or _src(s["asset_id"], "maintenance.jsonl")})
        for dsp in disputes:
            flags.append({"kind": "conflict", "refs": [s["inspection_id"], dsp["note_id"]], "message": f"{s['inspection_id']}: note {dsp['note_id']}: {dsp['text']}"})
    evidence.append({"kind": "letter", "ref": lt["doc_id"], "detail": f"{lt['subject']}; reportable spills since {lt['since']}; results due {lt['due']}", "source": lt["path"]})
    if lt["required_text_source"]:
        evidence.append({"kind": "letter", "ref": f"{lt['doc_id']} HSE note", "detail": f"compliant only if the clean-up report says \"{lt['required_text']}\" ({lt['required_text_source']})", "source": lt["path"]})
    cols = ["asset_id", "area", "inspection", "volume_bbl", "cleanup_job", "compliant", "reason"]
    summary = {"letter": lt["doc_id"], "from": lt["sender"], "date": lt["letter_date"], "areas": lt["area_names"].split("|"), "since": lt["since"],
               "results_due": lt["due"], "min_bbl": lt["min_bbl"], "covered": len(rows), "not_compliant": sum(r["compliant"] == "No" for r in rows),
               "excluded_not_crude": excluded, "rule": f"compliant only if the clean-up report contains \"{lt['required_text']}\"; field readings do not count"}
    return _result(rows, summary, evidence, {"table": _table(rows, cols)}, flags)


# --- OPS-5 freeze protection ---------------------------------------------------------------------------

def freeze_checks(ctx, start=None, end=None, area_id=None):
    """SOP-OPS OPS-5 cold-weather rule (1 Dec - 28 Feb): an inspection at or below -5 °C air on a well that is
    producing at that reading needs a 'Heat trace check' job starting within 7 days after it. Lists every such
    reading and whether the check happened."""
    amb, days, a, b = (_rule(ctx, n) for n in ("ops5_ambient_c", "ops5_check_days", "ops5_season_start", "ops5_season_end"))
    if not amb or not days:
        return _result([], {"note": "no cold-weather (OPS-5) rule in the SOPs, so there is nothing to check"},
                       flags=[{"kind": "missing_rule", "refs": ["SOP-OPS OPS-5"], "message": "cold-weather rule not found in the SOPs"}])
    s, e = _window(ctx, start, end, default_days=60)
    where, params = _well_filter(ctx, None, area_id, alias="v")
    cold = _rows(ctx, f"""SELECT v.inspection_id, v.asset_id, v.area_id, v.inspected_at, v.day, v.ambient_temp_c, v.delta_c FROM v_inspection v
                          WHERE v.day BETWEEN ? AND ? AND v.ambient_temp_c <= ? AND v.producing = 1 {where} ORDER BY v.inspected_at""",
                 [s, e, amb["value"]] + params)
    season = lambda d: (int(d[5:7]) * 100 + int(d[8:10]) >= a["value"]) or (int(d[5:7]) * 100 + int(d[8:10]) <= b["value"]) if a and b else True
    areas, rows, evidence = _area_names(ctx), [], []
    for c in cold:
        if not season(c["day"]):
            continue
        due = (_d(c["day"]) + timedelta(days=int(days["value"]))).isoformat()
        job = _rows(ctx, "SELECT maintenance_id, SUBSTR(started_at,1,10) d FROM maintenance WHERE asset_id = ? AND issue LIKE '%Heat trace check%' "
                         "AND started_at >= ? AND SUBSTR(started_at,1,10) <= ? ORDER BY started_at LIMIT 1", (c["asset_id"], c["inspected_at"], due))
        result = "done" if job else ("missed" if due < ctx.today.isoformat() else "open (window not over)")
        rows.append({"asset_id": c["asset_id"], "area": areas.get(c["area_id"]), "inspection_id": c["inspection_id"], "day": c["day"],
                     "ambient_temp_c": c["ambient_temp_c"], "check_due_by": due, "check_job": f"{job[0]['maintenance_id']} ({job[0]['d']})" if job else "none",
                     "result": result})
        evidence.append({"kind": "inspection", "ref": c["inspection_id"], "detail": f"{c['day']} air {c['ambient_temp_c']} °C, producing", "source": _src(c["asset_id"], "inspections.jsonl")})
        if job:
            evidence.append({"kind": "repair", "ref": job[0]["maintenance_id"], "detail": "Heat trace check", "source": _src(c["asset_id"], "maintenance.jsonl")})
    evidence.append({"kind": "sop", "ref": "SOP-OPS OPS-5", "detail": f"1 Dec - 28 Feb: air at or below {amb['value']:g} °C on a producing well -> heat trace check within {days['value']:g} days", "source": "fleet/sops/operations-sop-rev2.md"})
    cols = ["asset_id", "area", "inspection_id", "day", "ambient_temp_c", "check_due_by", "check_job", "result"]
    missed = [r for r in rows if r["result"] == "missed"]
    return _result(rows, {"window": [s, e], "cold_readings_on_producing_wells": len(rows), "missed": len(missed),
                          "missed_wells": sorted({r["asset_id"] for r in missed})}, evidence, {"table": _table(rows, cols)})


# --- Q10 shut-in delays -----------------------------------------------------------------------------

def shut_in_delays(ctx, start=None, end=None, area_id=None):
    """Why wells stayed shut in longer than the repair itself (parts, crews, contractors, weather), from the repair
    reports, by area for jobs started in the window: share of delayed jobs per reason and the typical (median) extra
    days. A company workover rig being busy counts as crews; a contractor's rig or contractor slot as contractors."""
    s, e = _window(ctx, start, end, default_days=365)
    where, params = _well_filter(ctx, None, area_id)
    jobs = _rows(ctx, f"""SELECT m.maintenance_id, m.asset_id, w.area_id, SUBSTR(m.started_at,1,10) started, d.delay_reason, d.delay_days, d.delay_text, d.report_path
                          FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id JOIN report_details d USING (maintenance_id)
                          WHERE SUBSTR(m.started_at,1,10) BETWEEN ? AND ? AND d.delay_reason IS NOT NULL {where} ORDER BY m.started_at""", [s, e] + params)
    reported = _rows(ctx, f"""SELECT w.area_id, COUNT(*) n FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id JOIN report_details d USING (maintenance_id)
                              WHERE SUBSTR(m.started_at,1,10) BETWEEN ? AND ? AND d.report_path IS NOT NULL {where} GROUP BY w.area_id""", [s, e] + params)
    areas = _area_names(ctx)
    reasons = ["parts", "crews", "contractors", "weather", "other"]
    table, bars = [], []
    for aid in sorted({j["area_id"] for j in jobs}):
        aj = [j for j in jobs if j["area_id"] == aid]
        row = {"area": areas.get(aid), "delayed_jobs": len(aj), "reported_jobs": next((r["n"] for r in reported if r["area_id"] == aid), None),
               "median_extra_days": statistics.median([j["delay_days"] for j in aj if j["delay_days"] is not None] or [0])}
        bar = {"area": areas.get(aid)}
        for rs in reasons:
            rj = [j for j in aj if j["delay_reason"] == rs]
            if not rj and rs == "other":
                continue
            row[f"{rs}_share_pct"] = round(100 * len(rj) / len(aj), 1)
            row[f"{rs}_median_days"] = statistics.median([j["delay_days"] for j in rj if j["delay_days"] is not None]) if rj else None
            bar[rs] = len(rj)
        table.append(row)
        bars.append(bar)
    fleet = {rs: round(100 * sum(j["delay_reason"] == rs for j in jobs) / len(jobs), 1) for rs in reasons if jobs and any(j["delay_reason"] == rs for j in jobs)}
    fleet_days = {rs: statistics.median([j["delay_days"] for j in jobs if j["delay_reason"] == rs and j["delay_days"] is not None])
                  for rs in fleet}
    stand_out = []
    for r in table:
        for rs in fleet:
            share = r.get(f"{rs}_share_pct") or 0
            if share >= fleet[rs] + 15 or (r.get(f"{rs}_median_days") or 0) >= 1.5 * (fleet_days[rs] or 99):
                stand_out.append(f"{r['area']}: {rs} {share}% of delays (fleet {fleet[rs]}%), median {r.get(f'{rs}_median_days')} extra days (fleet {fleet_days[rs]})")
    cols = ["area", "delayed_jobs"] + [f"{rs}_share_pct" for rs in fleet] + [f"{rs}_median_days" for rs in fleet]
    evidence = [{"kind": "report", "ref": j["maintenance_id"], "detail": j["delay_text"] or j["delay_reason"], "source": j["report_path"]} for j in jobs[:60]]
    visuals = {"table": {"type": "table", "columns": cols, "rows": [[r.get(c) for c in cols] for r in table]},
               "bar": {"type": "bar", "title": "Jobs that waited longer than the repair, by reason", "x": "area", "stacked": True,
                       "series": [{"key": rs, "label": rs.capitalize()} for rs in fleet], "bars": bars}}
    summary = {"window": [s, e], "delayed_jobs": len(jobs), "fleet_share_pct": fleet, "fleet_median_extra_days": fleet_days,
               "stands_out": stand_out or ["no area differs from the fleet by 15 points or 1.5x the typical delay"],
               "by_area": table, "method": "reason and extra days from each repair report's 'What happened' section"}
    return _result(jobs, summary, evidence, visuals)


# --- Q11 temperature by area -------------------------------------------------------------------------

def temperature_by_area(ctx, start=None, end=None):
    """Which areas run hotter: monthly average wellhead temperature, air temperature and wellhead-above-air
    difference per area (producing readings only), plus a comparison within each well type so a different mix of
    T200/T350/T500 wells does not decide it. The manuals warn never to judge by absolute wellhead temperature."""
    s, e = _window(ctx, start, end, default_days=365)
    data = _rows(ctx, """SELECT v.area_id, SUBSTR(v.day,1,7) month, v.well_type, ROUND(AVG(v.wellhead_temp_c),1) wellhead, ROUND(AVG(v.ambient_temp_c),1) air,
                                ROUND(AVG(v.delta_c),2) delta, COUNT(*) n
                         FROM v_inspection v WHERE v.day BETWEEN ? AND ? AND v.producing = 1 GROUP BY v.area_id, month, v.well_type""", (s, e))
    areas = _area_names(ctx)
    months = sorted({d["month"] for d in data})
    types = sorted({d["well_type"] for d in data})
    fleet_type = {}
    for t in types:
        td = [d for d in data if d["well_type"] == t]
        fleet_type[t] = {m: sum(d["delta"] * d["n"] for d in td if d["month"] == m) / max(1, sum(d["n"] for d in td if d["month"] == m)) for m in months}
    rows, grid, ranks = [], [], {}
    for aid in sorted({d["area_id"] for d in data}):
        ad = [d for d in data if d["area_id"] == aid]
        n = sum(d["n"] for d in ad)
        excess = sum((d["delta"] - fleet_type[d["well_type"]][d["month"]]) * d["n"] for d in ad) / n
        monthly = []
        for m in months:
            md = [d for d in ad if d["month"] == m]
            mn = sum(d["n"] for d in md)
            monthly.append(round(sum((d["delta"] - fleet_type[d["well_type"]][m]) * d["n"] for d in md) / mn, 2) if mn else None)
        mix = {t: sum(d["n"] for d in ad if d["well_type"] == t) for t in types}
        rows.append({"area": areas.get(aid), "area_id": aid, "readings": n,
                     "avg_wellhead_c": round(sum(d["wellhead"] * d["n"] for d in ad) / n, 1), "avg_air_c": round(sum(d["air"] * d["n"] for d in ad) / n, 1),
                     "avg_above_air_c": round(sum(d["delta"] * d["n"] for d in ad) / n, 2), "above_type_average_c": round(excess, 2),
                     "type_mix_pct": {t: round(100 * c / n) for t, c in mix.items() if c},
                     "months_above_type_average": sum(1 for x in monthly if x is not None and x > 0), "months": len([x for x in monthly if x is not None])})
        grid.append(monthly)
    for i, m in enumerate(months):
        vals = [(g[i], r["area"]) for g, r in zip(grid, rows) if g[i] is not None]
        if vals:
            top = max(vals)[1]
            ranks[top] = ranks.get(top, 0) + 1
    for r in rows:
        r["months_hottest_area"] = ranks.get(r["area"], 0)
    for r, g in zip(rows, grid):
        r["monthly_vs_type_average_c"] = dict(zip(months, g))
    rows.sort(key=lambda r: -r["above_type_average_c"])
    cols = ["area", "avg_wellhead_c", "avg_air_c", "avg_above_air_c", "above_type_average_c", "months_above_type_average", "months_hottest_area"]
    visuals = {"table": _table(rows, cols),
               "bar": {"type": "bar", "title": "Wellhead above air vs type average (°C)", "x": "area", "y": "above_type_average_c",
                       "bars": [{"area": r["area"], "above_type_average_c": r["above_type_average_c"]} for r in rows]}}
    summary = {"window": [s, e], "months": len(months), "hottest_by_type_adjusted_difference": rows[0]["area"] if rows else None,
               "hottest_by_absolute_wellhead": max(rows, key=lambda r: r["avg_wellhead_c"])["area"] if rows else None,
               "type_adjusted_spread_c": round(rows[0]["above_type_average_c"] - rows[-1]["above_type_average_c"], 2) if rows else None,
               "verdict": (("no area runs consistently hotter once well type is accounted for: every area is within "
                            f"{max(abs(r['above_type_average_c']) for r in rows):.2f} °C of its type's average; differences in raw wellhead "
                            "temperature come from the mix of well types (T500 ESP wells run hotter by design)")
                           if rows and max(abs(r["above_type_average_c"]) for r in rows) < 1 else
                           f"{rows[0]['area']} runs hottest after accounting for well type" if rows else None),
               "type_mix_by_area": {r["area"]: r["type_mix_pct"] for r in rows},
               "method": "producing readings only; difference above air compared with the fleet average for the same well type and month",
               "caution": "absolute wellhead temperature follows air temperature, sun and well type; OM-T200 section 3 says never judge by it"}
    return _result(rows, summary, [{"kind": "manual", "ref": "OM-T200 section 3", "detail": "never judge production from absolute wellhead temperature", "source": "fleet/well-types/T200.md"}],
                   visuals)


# --- teams, data coverage, production --------------------------------------------------------------

def team_info(ctx, team):
    """A team's lead and the wells it runs now (a well's own team overrides its area's team), by area and type,
    with wells that moved between teams."""
    t = _rows(ctx, "SELECT * FROM teams WHERE team_id = ? OR LOWER(name) = LOWER(?) OR LOWER(name) LIKE LOWER(?)", (team, team, f"%{team}%"))
    if not t:
        raise ToolError(f"unknown team {team!r}")
    t = t[0]
    lead = _rows(ctx, "SELECT user_id, name, role FROM users WHERE user_id = ?", (t["lead_user_id"],))
    wells = _rows(ctx, "SELECT asset_id, area_id, well_type, status, decommissioned_on FROM wells WHERE team_id = ? ORDER BY asset_id", (t["team_id"],))
    active = [w for w in wells if w["status"] != "plugged_and_abandoned"]
    moved = _rows(ctx, "SELECT h.asset_id, h.team_id, h.valid_from, h.valid_to FROM well_team_history h WHERE h.asset_id IN "
                       "(SELECT asset_id FROM well_team_history WHERE team_id = ?) ORDER BY h.asset_id, h.valid_from", (t["team_id"],))
    areas = _area_names(ctx)
    by_area = {}
    for w in active:
        by_area[areas.get(w["area_id"])] = by_area.get(areas.get(w["area_id"]), 0) + 1
    members = _rows(ctx, "SELECT user_id, name, role FROM users WHERE team_id = ? ORDER BY user_id", (t["team_id"],))
    summary = {"team": t["name"], "team_id": t["team_id"], "lead": lead[0] if lead else None, "wells_now": len(active),
               "plugged_and_abandoned": [w["asset_id"] for w in wells if w["status"] == "plugged_and_abandoned"], "by_area": by_area,
               "by_type": {wt: sum(1 for w in active if w["well_type"] == wt) for wt in sorted({w["well_type"] for w in active})},
               "members": members, "team_changes": moved}
    return _result(active, summary, [{"kind": "record", "ref": t["team_id"], "detail": f"{t['name']}, lead {lead[0]['name'] if lead else '?'}", "source": "fleet/areas.json"},
                                     {"kind": "record", "ref": "metadata team_id", "detail": "a well's own team_id/team_history overrides its area's team", "source": "assets/*/metadata.json"}],
                   {"table": _table(active, ["asset_id", "area_id", "well_type"])})


def data_coverage(ctx):
    """What the records contain and what they do not (e.g. no pressure readings, no oil volumes), so questions that
    need missing data are answered honestly. Call this before answering about a measurement no tool provides."""
    ins_cols = [r[1] for r in ctx.con.execute("PRAGMA table_info(inspections)")]
    n = lambda sql: ctx.con.execute(sql).fetchone()[0]
    measured = {"inspections": f"{n('SELECT COUNT(*) FROM inspections')} readings: wellhead and air temperature, spill detected/area/estimated volume, method (drone/manual), inspector remark",
                "maintenance": f"{n('SELECT COUNT(*) FROM maintenance')} jobs: dates, type, equipment, issue, action, downtime hours, cost",
                "repair_reports": "what happened, root cause, parts installed (pump lot), follow-up, safety (H2S readings), clean-up, cost, compliance table",
                "notes": "operator shift notes, corrections, data corrections", "regulatory_events": "spill reports, site inspections, notices of violation",
                "documents": "well-type manuals, SOPs, MOC memos, supplier recall, regulator letter"}
    missing = {"pressure": "no pressure readings of any kind (reservoir, wellhead, flowline); the manuals give only design limits such as 3,000 psi maximum wellhead working pressure (OM-T200 section 9.1)",
               "production_volumes": "no oil volumes or rates (bbl/day) for any well; only whether a well is producing at each inspection (wellhead above air)",
               "flow_rates": "no flow, gas or water rates", "stroke_or_load": "no stroke rate or rod load readings"}
    proxies = {"production": "days/readings producing and the wellhead-above-air difference show whether a well flows, not how much (use production_proxy)",
               "pressure": "none: temperature does not measure pressure"}
    return _result([], {"inspection_fields": ins_cols, "measured": measured, "not_in_the_data": missing, "possible_proxies": proxies},
                   [{"kind": "manual", "ref": "OM-T200 section 9.1", "detail": "maximum allowable wellhead working pressure 3,000 psi (a limit, not a reading)", "source": "fleet/well-types/T200.md"}])


def production_proxy(ctx, start=None, end=None, top_n=10, area_id=None):
    """No oil volumes are recorded, so this ranks wells by the evidence that IS recorded for a period: share of
    inspections producing, producing days (7 per producing weekly inspection, capped at the period length) and average wellhead-above-air
    difference. It shows which wells flowed most consistently, not which produced the most barrels."""
    s, e = _window(ctx, start, end, default_days=365)
    where, params = _well_filter(ctx, None, area_id, alias="v")
    rows = _rows(ctx, f"""SELECT v.asset_id, v.well_type, v.area_id, COUNT(*) readings, SUM(v.producing) producing_readings,
                                 ROUND(100.0 * SUM(v.producing) / COUNT(*), 1) producing_pct, MIN(7 * SUM(v.producing), CAST(JULIANDAY(MIN(?, ?)) - JULIANDAY(?) + 1 AS INTEGER)) producing_days,
                                 ROUND(AVG(CASE WHEN v.producing = 1 THEN v.delta_c END), 1) avg_above_air_when_producing
                          FROM v_inspection v WHERE v.day BETWEEN ? AND ? {where} GROUP BY v.asset_id
                          ORDER BY producing_pct DESC, producing_days DESC, avg_above_air_when_producing DESC""",
                 [e, ctx.today.isoformat(), s, s, e] + params)
    top = rows[: int(top_n)]
    full = sum(1 for r in rows if r["producing_pct"] == 100)
    cols = ["asset_id", "well_type", "area_id", "producing_pct", "producing_days", "avg_above_air_when_producing"]
    return _result(top, {"window": [s, e], "wells": len(rows), "wells_producing_at_every_inspection": full,
                         "caveat": "no oil volumes in the data: this is a proxy for uptime, not barrels; many wells tie at 100%. "
                                   "T500 (ESP) wells are high-rate wells per OM-T500 section 1 but no rate is recorded"},
                   [{"kind": "manual", "ref": "OM-T500 section 1", "detail": "T500 is a high-rate well (ESP)", "source": "fleet/well-types/T500.md"}],
                   {"table": _table(top, cols)})


# --- private preferences -------------------------------------------------------------------------

PREFS = {"sort_wells": ["area_then_well", "well", "none"], "cost_unit": ["usd", "thousands"]}


def set_my_preference(ctx, sort_wells=None, cost_unit=None, text=None):
    """Save a display preference for the CURRENT user only (private; applied to their later answers in any
    session): sort_wells ('area_then_well' sorts lists by area, then well ID), cost_unit ('thousands' shows USD k)."""
    now = _stamp(ctx)
    saved = {}
    for name, value in (("sort_wells", sort_wells), ("cost_unit", cost_unit)):
        if value is None:
            continue
        if value not in PREFS[name]:
            raise ToolError(f"{name} must be one of {PREFS[name]}")
        ctx.con.execute("INSERT OR REPLACE INTO user_prefs (user_id, name, value, text, created_at) VALUES (?, ?, ?, ?, ?)",
                        (ctx.user_id, name, value, text, now))
        saved[name] = value
    if not saved:
        raise ToolError("nothing to save: give sort_wells and/or cost_unit")
    ctx.con.commit()
    return _result([saved], {"saved": saved, "scope": "this user only, all later answers; other users are not affected"})


def user_prefs(con, user_id):
    try:
        return {n: v for n, v in con.execute("SELECT name, value FROM user_prefs WHERE user_id = ?", (user_id,))}
    except Exception:
        return {}


def apply_prefs(visual, prefs):
    """Apply a user's display preferences to a table visual (sorting, cost units). Returns a new visual."""
    if not visual or visual.get("type") != "table" or not prefs:
        return visual
    cols, rows = list(visual["columns"]), [list(r) for r in visual["rows"]]
    if prefs.get("sort_wells") == "area_then_well" and "asset_id" in cols:
        ai = cols.index("asset_id")
        ar = next((cols.index(c) for c in ("area", "area_id", "area_name") if c in cols), None)
        rows.sort(key=lambda r: ((str(r[ar]) if ar is not None else ""), str(r[ai])))
    if prefs.get("cost_unit") == "thousands":
        for i, c in enumerate(cols):
            if "cost" in c and "usd" in c.lower() or c in ("cost_total", "cost_usd"):
                cols[i] = c.replace("_usd", "") + " (USD k)"
                for r in rows:
                    if isinstance(r[i], (int, float)):
                        r[i] = round(r[i] / 1000, 1)
    return dict(visual, columns=cols, rows=rows, preferences_applied=prefs)


TOOLS.update({
    "gas_reports": (gas_reports, {"start": _DATE, "end": _DATE, "area_id": _AREA, "asset_ids": _IDS}, []),
    "bypasses": (bypasses, {"area_id": _AREA, "asset_ids": _IDS}, []),
    "status_conflicts": (status_conflicts, {"area_id": _AREA, "asset_ids": _IDS}, []),
    "operator_concerns": (operator_concerns, {"days": {"type": "integer", "description": "look-back in days (default 30)"},
                                                 "top_n": {"type": "integer"}, "area_id": _AREA}, []),
    "recalled_pumps": (recalled_pumps, {"area_id": _AREA, "team_id": {"type": "string", "description": "team ID or name, e.g. TEAM-NW"},
                                           "asset_ids": _IDS}, []),
    "report_equipment_swap": (report_equipment_swap, {"asset_id": {"type": "string"}, "text": {"type": "string"},
                                                         "relayed_from": {"type": "string", "description": "name of the person the user heard it from, if not first-hand"},
                                                         "swapped_on": _DATE}, ["asset_id", "text"]),
    "letter_compliance": (letter_compliance, {"doc_id": {"type": "string"}}, []),
    "freeze_checks": (freeze_checks, {"start": _DATE, "end": _DATE, "area_id": _AREA}, []),
    "shut_in_delays": (shut_in_delays, {"start": _DATE, "end": _DATE, "area_id": _AREA}, []),
    "temperature_by_area": (temperature_by_area, {"start": _DATE, "end": _DATE}, []),
    "team_info": (team_info, {"team": {"type": "string", "description": "team name or ID"}}, ["team"]),
    "data_coverage": (data_coverage, {}, []),
    "production_proxy": (production_proxy, {"start": _DATE, "end": _DATE, "top_n": {"type": "integer"}, "area_id": _AREA}, []),
    "set_my_preference": (set_my_preference, {"sort_wells": {"type": "string", "enum": PREFS["sort_wells"]},
                                              "cost_unit": {"type": "string", "enum": PREFS["cost_unit"]}, "text": {"type": "string"}}, []),
})
