"""Parse the structured parts of fleet documents beyond the manuals: SOP revisions whose deadlines depend on
the date, management-of-change (MOC) memos, supplier recall notices and regulator letters.

Everything parsed keeps its source (document and section) for citation. A document that does not match is
simply skipped; the loader reports what it found so nothing is silently assumed.
"""
from __future__ import annotations

import glob
import os
import re
from datetime import date, datetime, timedelta

WELL = re.compile(r"WELL-\d{4}")
DATE = r"(\d{1,2} [A-Z][a-z]{2,8} \d{4})"


def to_iso(text):
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _day_before(iso):
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _doc_id(text, path):
    m = re.search(r"Document ID:\s*([\w-]+)", text) or re.search(r"Management of Change\s*[—-]\s*(MOC-[\w-]+)", text)
    return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]


def _field(text, name):
    m = re.search(rf"\*\*{name}:\*\*\s*(.+)", text)
    return m.group(1).strip() if m else None


def _section(text, prefix):
    m = re.search(rf"^## {re.escape(prefix)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def rule_versions(text, path):
    """Date-dependent deadlines: [(name, value, valid_from, valid_to, source)]."""
    out = []
    doc = _doc_id(text, path)
    # SOP-OPS OPS-1: "**24 hours** for reports noted before 1 Jan 2027; **12 hours** for reports noted from 1 Jan 2027"
    ops1 = _section(text, "OPS-1")
    before = re.search(r"\*\*(\d+) hours\*\* for reports noted before " + DATE, ops1)
    after = re.search(r"\*\*(\d+) hours\*\* for reports noted from " + DATE, ops1)
    if before and after:
        cut = to_iso(after.group(2))
        out += [("ops1_response_hours", float(before.group(1)), None, _day_before(cut), f"{doc} OPS-1"),
                ("ops1_response_hours", float(after.group(1)), cut, None, f"{doc} OPS-1")]
    # SOP-SR SR-01: "within **12 hours** of discovery (24 hours for spills discovered before 1 Jan 2027)"
    m = re.search(r"\*\*SR-01\*\*.*?within\s+\*\*(\d+)\s*hours\*\*\s+of discovery\s*\((\d+) hours for spills discovered before " + DATE, text, re.S)
    if m:
        cut = to_iso(m.group(3))
        out += [("sr01_report_hours", float(m.group(2)), None, _day_before(cut), f"{doc} SR-01 (revision before {m.group(3)})"),
                ("sr01_report_hours", float(m.group(1)), cut, None, f"{doc} SR-01")]
    return out


def ops_rules(text, path):
    """Fixed SOP-OPS values: {name: (value, source)} plus the OPS-4 failure categories [(order, category, [keywords])]."""
    doc, out = _doc_id(text, path), {}
    ops5 = _section(text, "OPS-5")
    if m := re.search(r"at or below \*\*([−-]?\d+(?:\.\d+)?) °C\*\*", ops5):
        out["ops5_ambient_c"] = (float(m.group(1).replace("−", "-")), f"{doc} OPS-5")
    if m := re.search(r"within \*\*(\d+) days\*\* after the inspection", ops5):
        out["ops5_check_days"] = (float(m.group(1)), f"{doc} OPS-5")
    if m := re.search(r"\((\d{1,2}) ([A-Z][a-z]{2}) [–-] (\d{1,2}) ([A-Z][a-z]{2})\)", text[text.find("OPS-5"):]):
        months = {mo: i + 1 for i, mo in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
        out["ops5_season_start"] = (months[m.group(2)] * 100 + int(m.group(1)), f"{doc} OPS-5 (MMDD)")
        out["ops5_season_end"] = (months[m.group(4)] * 100 + int(m.group(3)), f"{doc} OPS-5 (MMDD)")
    if m := re.search(r"within \*\*(\d+) days\*\* after the previous corrective job", _section(text, "OPS-6")):
        out["ops6_repeat_days"] = (float(m.group(1)), f"{doc} OPS-6")
    cats = []
    for row in re.finditer(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", _section(text, "OPS-4"), re.M):
        kws = [k.strip().lower() for k in row.group(3).split(",") if k.strip() and "no keyword" not in k]
        cats.append((int(row.group(1)), row.group(2).strip(), kws))
    return out, sorted(cats)


def classify_failure(root_cause, categories):
    """SOP-OPS OPS-4: the first category (in order) with a keyword in the root-cause text."""
    if not root_cause or not categories:
        return None
    low = root_cause.lower()
    for _, cat, kws in categories:
        if not kws or any(k in low for k in kws):
            return cat
    return None


def moc(text, path):
    m = re.search(r"Management of Change\s*[—-]\s*(MOC-[\w-]+)", text)
    if not m:
        return None
    change = _section(text, "Change")
    ext = re.search(r"extends (MOC-[\w-]+)", change)
    eff = re.search(r"Effective from " + DATE, text)
    exp = re.search(r"expires on " + DATE, text)
    return {"moc_id": m.group(1), "title": _field(text, "Title"), "memo_date": to_iso(_field(text, "Date") or ""),
            "effective_from": to_iso(eff.group(1)) if eff else to_iso(_field(text, "Date") or ""),
            "expires_on": to_iso(exp.group(1)) if exp else None, "extends_moc": ext.group(1) if ext else None,
            "approved_by": _field(text, "Approved by"), "wells": sorted(set(WELL.findall(change)))}


def recall(text, path):
    if not re.search(r"Recall Notice", text, re.I):
        return None
    ref = _field(text, "Reference") or os.path.basename(path)
    what = _section(text, "What is recalled")
    model = re.search(r"Highline ([\w-]+)|^(\S+ [\w-]+) downhole", what)
    lots = re.findall(r"^- (LOT-[\w-]+)", what, re.M)
    by = re.search(r"by " + DATE, _section(text, "What you must do"))
    excl = re.search(r"([^.]*ESP[^.]*not affected[^.]*\.)", what)
    return [{"notice_id": ref, "model": re.search(r"(?:Highline )?(RP-\d+)", what).group(1) if re.search(r"RP-\d+", what) else (model.group(0) if model else None),
             "lot": lot, "notice_date": to_iso(_field(text, "Date") or ""), "replace_by": to_iso(by.group(1)) if by else None,
             "excludes": excl.group(1).strip() if excl else None, "path": path} for lot in lots]


def letter(text, path):
    if not re.search(r"Letter from", text) or "Commission" not in text:
        return None
    body = re.search(r"For every reportable spill in (.+?) since " + DATE + r".*?results submitted by " + DATE, text, re.S)
    if not body:
        return None
    areas = [a.strip() for a in re.split(r",| or | and ", body.group(1)) if a.strip()]
    req = re.search(r'contains the sentence "([^"]+)"', text)
    note = re.search(r"\*\*Internal note from HSE \(([^)]+)\), ([^:]+):\*\*", text)
    excl = re.search(r"(Spills that you later\s+confirmed were not crude oil do not need samples\.)", text)
    return {"doc_id": os.path.splitext(os.path.basename(path))[0], "sender": _field(text, "From"), "addressee": _field(text, "To"),
            "letter_date": to_iso(_field(text, "Date") or ""), "subject": _field(text, "Subject"), "area_names": "|".join(areas),
            "since": to_iso(body.group(2)), "due": to_iso(body.group(3)),
            "min_bbl": float(re.search(r"\((\d+(?:\.\d+)?) bbl or more\)", text).group(1)) if re.search(r"\((\d+(?:\.\d+)?) bbl or more\)", text) else None,
            "excludes": " ".join(excl.group(1).split()) if excl else None,
            "required_text": req.group(1) if req else None,
            "required_text_source": f"internal note from HSE ({note.group(1)}), {note.group(2)}" if note else None, "path": path}


def parse_fleet_docs(fleet_dir):
    """Everything structured in fleet/sops: {'versions', 'ops', 'ops4', 'mocs', 'recalls', 'letters'}."""
    out = {"versions": [], "ops": {}, "ops4": [], "mocs": [], "recalls": [], "letters": []}
    for path in sorted(glob.glob(os.path.join(fleet_dir, "sops", "*.md"))):
        with open(path) as f:
            text = f.read()
        rel = os.path.relpath(path, os.path.dirname(fleet_dir))
        out["versions"] += rule_versions(text, rel)
        if "OPS-4" in text and "OPS-1" in text:
            ops, cats = ops_rules(text, rel)
            out["ops"].update(ops)
            out["ops4"] = cats or out["ops4"]
        if m := moc(text, rel):
            out["mocs"].append(m)
        out["recalls"] += recall(text, rel) or []
        if lt := letter(text, rel):
            out["letters"].append(lt)
    return out
