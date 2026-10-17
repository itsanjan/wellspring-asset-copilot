"""Parse rule values from the well-type manuals so new well types or changed thresholds
need no code change. Every value keeps its source section for citation.
"""
from __future__ import annotations

import glob
import os
import re

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

PRODUCING = re.compile(r"at least\s+\*\*(\d+(?:\.\d+)?)\s*°C\s+above", re.I)
ALARM = re.compile(r"at or below\s+(\d+(?:\.\d+)?)\s*°C", re.I)
RCA_OR_MORE = re.compile(r"(\w+)\s+or more\s+\*\*corrective\*\*", re.I)
RCA_AFTER = re.compile(r"RCA after\s+(\w+)\s+corrective", re.I)
MONTHS = re.compile(r"in the\s+(\d+)\s+months", re.I)


def _num(word):
    w = word.lower()
    return NUMBER_WORDS.get(w) if not w.isdigit() else int(w)


def _sections(text):
    """Yield (section_number, heading, body) for '## 9.3 ...' style headings."""
    parts = re.split(r"^(#{2,3})\s+(.*)$", text, flags=re.M)
    for i in range(1, len(parts), 3):
        heading = parts[i + 1].strip()
        m = re.match(r"(\d+(?:\.\d+)*)\.?\s", heading)
        yield (m.group(1) if m else heading), heading, parts[i + 2]


def parse_manual(path):
    """Return {name: (value, source)} for one manual."""
    with open(path) as f:
        text = f.read()
    doc_id = re.search(r"Document ID:\s*(\S+)", text)
    doc = doc_id.group(1) if doc_id else os.path.basename(path)
    out = {}
    for sec, heading, body in _sections(text):
        src = f"{doc} section {sec}"
        if m := PRODUCING.search(body):
            out["producing_delta_c"] = (float(m.group(1)), src)
        if m := ALARM.search(body):
            out["temp_alarm_c"] = (float(m.group(1)), src)
        if m := (RCA_OR_MORE.search(body) or RCA_AFTER.search(body)):
            n = _num(m.group(1))
            if n:
                out["rca_corrective_repairs"] = (float(n), src)
                if mm := MONTHS.search(body):
                    out["rca_window_months"] = (float(mm.group(1)), src)
                elif "12 months" in body:
                    out["rca_window_months"] = (12.0, src)
    return out


def parse_all(fleet_dir):
    """{well_type: {name: (value, source)}}; T350-style manuals inherit what they omit from T200."""
    types = {}
    for path in sorted(glob.glob(os.path.join(fleet_dir, "well-types", "*.md"))):
        types[os.path.splitext(os.path.basename(path))[0]] = parse_manual(path)
    base = types.get("T200", {})
    for t, params in types.items():
        for name in ("rca_corrective_repairs", "rca_window_months"):
            if name not in params and name in base:
                params[name] = (base[name][0], base[name][1] + f" (inherited by {t})")
    return types
