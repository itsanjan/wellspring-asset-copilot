"""Parse structured facts out of free-text maintenance reports.

The compliance table at the end of each report is regular, so plain regexes are enough;
an LLM is only needed for prose that has no table row. Always follow `report_file` from
maintenance.jsonl (file names vary).
"""
import os
import re

ROW = re.compile(r"^\|\s*(?P<proto>[A-Z]{2}-\d\d)\s*\|\s*(?P<result>[^|]+?)\s*\|\s*(?P<detail>[^|]*?)\s*\|\s*$", re.M)
CONTAINED = re.compile(r"Contained about ([\d.]+) hours? after discovery \(limit ([\d.]+) hours?\)")
TRIGGER = re.compile(r"\*\*Triggered by:\*\*\s*inspection (INS-\d+-\d+)")
PARTS = re.compile(r"\*\*Parts installed:\*\*\s*(?P<model>.+?) pump, serial (?P<serial>\S+?), lot (?P<lot>[\w-]+)")
FOLLOW_UP = re.compile(r"\*\*Follow-up:\*\*\s*(?P<text>.*?within (?P<days>\d+) days?.*)")
# Why a well stayed shut in longer than the repair itself (the "What happened" prose).
RIG_WAIT = re.compile(r"(?P<who>.+?)(?:'s)? workover rig was busy on other wells, so the well waited about (?P<days>[\d.]+) days? for it")
EXTRA = re.compile(r"(?:stayed|is expected to stay) shut in for about (?P<days>[\d.]+) days? longer than the repair itself because (?P<why>[^.]+)")
H2S_MAX = re.compile(r"highest(?: reading)? (?P<ppm>[\d.]+) ppm")
H2S_STOP = re.compile(r"monitor read (?P<ppm>[\d.]+) ppm, above the")


def section(text, heading):
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def delay_reason(text):
    """(category, extra_days, sentence) for why the well waited, or (None, None, None). Categories: parts,
    crews (company rig busy), contractors (contractor rig busy or contractor slot), weather."""
    m = EXTRA.search(text)
    if m:
        why = m["why"].lower()
        cat = ("parts" if re.search(r"\bparts?\b|back-order|supplier", why) else "contractors" if "contractor" in why
               else "weather" if re.search(r"weather|rain|snow|ice|storm|flood|road|wind|freez", why)
               else "crews" if re.search(r"crew|rig", why) else "other")
        return cat, float(m["days"]), m.group(0)
    m = RIG_WAIT.search(text)
    if m:
        who = m["who"].strip().split(". ")[-1]
        return ("crews" if who.lower().startswith("the company") else "contractors"), float(m["days"]), m.group(0)
    if re.search(r"weather", section(text, "What happened") or "", re.I):
        return "weather", None, None
    return None, None, None


def parse_report(path):
    text = open(path).read()
    rows = {m["proto"]: (m["result"].replace("*", "").strip(), m["detail"]) for m in ROW.finditer(text)}
    facts = {"checks": {p: r for p, (r, _) in rows.items()}, "details": {p: d for p, (_, d) in rows.items()}, "path": path}
    t = TRIGGER.search(text)
    facts["inspection_id"] = t.group(1) if t else None
    sr02 = rows.get("SR-02")
    if sr02:
        m = CONTAINED.search(sr02[1])
        if m:
            facts["contained_hours"] = float(m.group(1))
            facts["containment_limit_hours"] = float(m.group(2))
    facts["details_text"] = details(text)
    return facts


def details(text):
    """Free-text facts other tools need: root cause, pump fitted, follow-up, why the well waited, H2S, soil samples."""
    root = section(text, "Root cause")
    parts, follow = PARTS.search(text), FOLLOW_UP.search(text)
    cat, days, sentence = delay_reason(text)
    readings = [float(x) for x in H2S_MAX.findall(text)] + [float(x) for x in H2S_STOP.findall(text)]
    cleanup = section(text, "Spill clean-up and waste")
    return {"root_cause": " ".join(root.split()) if root else None,
            "parts_model": parts["model"] if parts else None, "parts_serial": parts["serial"] if parts else None,
            "parts_lot": parts["lot"] if parts else None,
            "follow_up_days": int(follow["days"]) if follow else None, "follow_up_text": follow["text"].strip() if follow else None,
            "delay_reason": cat, "delay_days": days, "delay_text": sentence,
            "h2s_max_ppm": max(readings) if readings else None, "h2s_stop_work": int(bool(H2S_STOP.search(text))),
            "soil_samples_lab": None if cleanup is None else int("soil samples sent to the lab" in cleanup.lower()),
            "cleanup_text": cleanup, "interim": int("work in progress" in text.lower() or "## Work done so far" in text)}


def reports_for_well(well_dir, maintenance):
    """Map maintenance_id -> parsed facts, following report_file."""
    out = {}
    for m in maintenance:
        rf = m.get("report_file")
        if not rf:
            continue
        p = rf if os.path.isabs(rf) else os.path.join(well_dir, rf)
        if not os.path.exists(p):
            p = os.path.join(well_dir, "maintenance-reports", os.path.basename(rf))
        if os.path.exists(p):
            out[m["maintenance_id"]] = parse_report(p)
    return out
