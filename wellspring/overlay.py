"""L3 overlay: what users tell the copilot, kept beside the raw data and never mixed into it.

Every entry is append-only and attributed (author, reason, time). Corrections and rules are
read back through the SQL views (v_inspection, v_rule_effective), so every later answer, for
every user, sees them, but only at the entry's scope: one inspection, or one well type.

Kinds:
  correction  target an inspection field (raw reading or AI estimate); keeps the original value
  rule        target a well type and a rule name from the manuals (e.g. rca_corrective_repairs)
  note        shared knowledge about a well ("shut in from 17 Oct for a workover")
  claim       a statement that contradicts the data; stored as unconfirmed until someone confirms
"""
from __future__ import annotations

from datetime import datetime, timezone

CORRECTABLE_FIELDS = {
    "wellhead_temp_c": float,
    "ambient_temp_c": float,
    "spill_est_volume_bbl": float,
    "spill_detected": lambda v: int(str(v).strip().lower() in ("1", "true", "yes")),
}


class OverlayError(ValueError):
    """The entry cannot be recorded (unknown target, field or value)."""


def _now(now):
    return now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_user(con, user_id):
    if not con.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
        raise OverlayError(f"unknown user {user_id}")


def _insert(con, **row):
    cols = ", ".join(row)
    cur = con.execute(f"INSERT INTO overlay_entries ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return cur.lastrowid


def _supersede_previous(con, seq, kind, target_type, target_ref, field):
    con.execute(
        "UPDATE overlay_entries SET status = 'superseded', superseded_by = ? "
        "WHERE kind = ? AND target_type = ? AND target_ref = ? AND IFNULL(field, '') = IFNULL(?, '') "
        "AND status = 'active' AND seq <> ?",
        (seq, kind, target_type, target_ref, field, seq))


def get(con, seq):
    con.row_factory = _dict_row
    row = con.execute(
        "SELECT o.*, 'OVL-' || o.seq AS entry_id, u.name AS author_name FROM overlay_entries o "
        "LEFT JOIN users u ON u.user_id = o.author_user_id WHERE o.seq = ?", (seq,)).fetchone()
    con.row_factory = None
    return row


def record_correction(con, author, inspection_id, field, new_value, reason, now=None):
    """Correct one field of one inspection. Returns the stored entry (with the original value)."""
    _require_user(con, author)
    if field not in CORRECTABLE_FIELDS:
        raise OverlayError(f"field {field!r} cannot be corrected; allowed: {sorted(CORRECTABLE_FIELDS)}")
    row = con.execute(f"SELECT asset_id, {field} FROM inspections WHERE inspection_id = ?", (inspection_id,)).fetchone()
    if not row:
        raise OverlayError(f"unknown inspection {inspection_id}")
    try:
        value = CORRECTABLE_FIELDS[field](new_value)
    except (TypeError, ValueError):
        raise OverlayError(f"{new_value!r} is not a valid value for {field}")
    seq = _insert(con, kind="correction", target_type="inspection", target_ref=inspection_id, field=field,
                  original_value=None if row[1] is None else str(row[1]), new_value=str(value),
                  reason=reason, author_user_id=author, created_at=_now(now), status="active")
    _supersede_previous(con, seq, "correction", "inspection", inspection_id, field)
    con.commit()
    return get(con, seq)


def record_rule(con, author, well_type, name, value, reason, now=None):
    """Override a manual rule for every well of one type (and only that type)."""
    _require_user(con, author)
    row = con.execute("SELECT value FROM rule_params WHERE well_type = ? AND name = ?", (well_type, name)).fetchone()
    if not row:
        known = [r[0] for r in con.execute("SELECT DISTINCT name FROM rule_params WHERE well_type = ?", (well_type,))]
        raise OverlayError(f"no rule {name!r} for well type {well_type!r}; known: {known}")
    seq = _insert(con, kind="rule", target_type="well_type", target_ref=well_type, field=name,
                  original_value=str(row[0]), new_value=str(float(value)), reason=reason,
                  author_user_id=author, created_at=_now(now), status="active")
    _supersede_previous(con, seq, "rule", "well_type", well_type, name)
    con.commit()
    return get(con, seq)


def record_statement(con, author, asset_id, text, contradicts_data, valid_from=None, valid_to=None,
                     reason=None, now=None):
    """Store shared knowledge about a well. If it contradicts the data it becomes an unconfirmed claim.

    Whether it contradicts the data is decided by the caller from the tools (e.g. latest inspection),
    never assumed here.
    """
    _require_user(con, author)
    if not con.execute("SELECT 1 FROM wells WHERE asset_id = ?", (asset_id,)).fetchone():
        raise OverlayError(f"unknown well {asset_id}")
    kind, status = ("claim", "unconfirmed") if contradicts_data else ("note", "active")
    seq = _insert(con, kind=kind, target_type="well", target_ref=asset_id, text=text, reason=reason,
                  author_user_id=author, created_at=_now(now), valid_from=valid_from, valid_to=valid_to,
                  status=status)
    con.commit()
    return get(con, seq)


def confirm(con, seq, user_id, now=None):
    """Mark an unconfirmed claim as confirmed by someone (it then counts as an active note)."""
    _require_user(con, user_id)
    con.execute("UPDATE overlay_entries SET status = 'active', confirmed_by = ?, confirmed_at = ? "
                "WHERE seq = ? AND status = 'unconfirmed'", (user_id, _now(now), seq))
    con.commit()
    return get(con, seq)


def for_well(con, asset_id, include_superseded=False):
    """Everything users have said that applies to a well: its own notes/claims, corrections to its
    inspections, and rules for its well type."""
    con.row_factory = _dict_row
    status = "" if include_superseded else "AND o.status <> 'superseded'"
    rows = con.execute(f"""
        SELECT o.*, 'OVL-' || o.seq AS entry_id, u.name AS author_name
        FROM overlay_entries o LEFT JOIN users u ON u.user_id = o.author_user_id
        WHERE ((o.target_type = 'well' AND o.target_ref = :w)
           OR (o.target_type = 'inspection' AND o.target_ref IN (SELECT inspection_id FROM inspections WHERE asset_id = :w))
           OR (o.target_type = 'well_type' AND o.target_ref = (SELECT well_type FROM wells WHERE asset_id = :w)))
          {status}
        ORDER BY o.seq""", {"w": asset_id}).fetchall()
    con.row_factory = None
    return rows


def related_context(con, inspection_id):
    """What else is on record about one inspection: the inspector's own remark, data notes and
    overlay entries. Used to point out disagreement when someone corrects it (e.g. Q12)."""
    con.row_factory = _dict_row
    ins = con.execute("SELECT i.inspection_id, i.notes AS inspector_note, i.inspector_user_id, u.name AS inspector_name "
                      "FROM inspections i LEFT JOIN users u ON u.user_id = i.inspector_user_id "
                      "WHERE i.inspection_id = ?", (inspection_id,)).fetchone()
    notes = con.execute("SELECT n.*, u.name AS author_name FROM notes n LEFT JOIN users u ON u.user_id = n.author_user_id "
                        "WHERE n.inspection_id = ?", (inspection_id,)).fetchall()
    entries = con.execute("SELECT o.*, 'OVL-' || o.seq AS entry_id, u.name AS author_name FROM overlay_entries o "
                          "LEFT JOIN users u ON u.user_id = o.author_user_id "
                          "WHERE o.target_type = 'inspection' AND o.target_ref = ? ORDER BY o.seq",
                          (inspection_id,)).fetchall()
    con.row_factory = None
    return {"inspection": ins, "data_notes": notes, "overlay": entries}


def _dict_row(cursor, row):
    return {d[0]: v for d, v in zip(cursor.description, row)}
