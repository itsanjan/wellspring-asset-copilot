"""Classify operator notes (field operators' shift notes) so tools can query them.

Each note gets a topic, a status effect and, for symptoms, the equipment it points at. Patterns are
checked in order; the first match wins. Only shut-in and restart notes change a well's status (SOP-OPS
OPS-3); gas, bypass and equipment remarks never do.
"""
from __future__ import annotations

import re

# (topic, status_effect, equipment_kind, pattern)
PATTERNS = [
    ("gas", None, None, r"gas odou?r|smell(?:ed)? gas|gas smell|rotten.egg|H2S monitor alarm"),
    ("bypass_in", None, "flowline", r"bypass (?:installed|in place)|put the bypass in|had the bypass installed"),
    ("bypass_out", None, "flowline", r"bypass (?:removed|pulled|out)|took the bypass out"),
    ("drive_trip", None, "esp_pump", r"drive tripping|tripping on (?:under|over)load"),
    ("drive_amps", None, "esp_pump", r"amps hunting"),
    ("leak", "shut_in", "wellhead", r"leak(?:ing)? at the stuffing box|stuffing box leak"),
    ("restart", "restart", None, r"\brestarted\b|back on production|back online|put back on production"),
    ("shut_in", "shut_in", None, r"\bshut[- ]?in\b|shut the well in|stays shut in"),
    ("weather_delay", "shut_in", None, r"delayed by weather"),
    ("rod_squeal", None, "wellhead", r"rod squeal|squeal"),
    ("tagging_bottom", None, "downhole_pump", r"tagging bottom"),
    ("rattle", None, "pump_jack", r"rattl"),
    ("fluid_pound", None, "downhole_pump", r"fluid pound"),
    ("counterweights", None, "pump_jack", r"counterweight"),
    ("routine", None, None, r"pumping normally|level normal|nothing to report|pad dry and clean|readings normal|running steady|running smooth"),
]
_COMPILED = [(t, s, e, re.compile(p, re.I)) for t, s, e, p in PATTERNS]

# Topics that describe a well that sounds or behaves wrong (candidates for a repair).
SYMPTOMS = ("drive_trip", "drive_amps", "leak", "rod_squeal", "tagging_bottom", "rattle", "fluid_pound")


def classify(text):
    """Return (topic, status_effect, equipment_kind) for an operator note."""
    for topic, effect, kind, rx in _COMPILED:
        if rx.search(text or ""):
            # A drive trip that the operator reset is an equipment remark, not a restart of a shut-in well.
            return topic, effect, kind
    return "other", None, None
