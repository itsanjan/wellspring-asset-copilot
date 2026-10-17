-- L0 raw records (read-only after load), L1 derived views, L2 documents.

-- Load facts: "today" for the dataset, data sources, load time.
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE teams (team_id TEXT PRIMARY KEY, name TEXT, lead_user_id TEXT);
CREATE TABLE users (user_id TEXT PRIMARY KEY, name TEXT, role TEXT, team_id TEXT);
CREATE TABLE areas (area_id TEXT PRIMARY KEY, name TEXT, team_id TEXT, center_lat REAL, center_lon REAL);

CREATE TABLE wells (
    asset_id TEXT PRIMARY KEY, name TEXT, well_type TEXT, area_id TEXT, team_id TEXT,
    in_charge_user_id TEXT, lat REAL, lon REAL, installed_on TEXT, recorded_status TEXT,
    last_inspection_id TEXT, source TEXT,
    status TEXT, decommissioned_on TEXT      -- plugged_and_abandoned wells: permanently closed
);
-- A well's type and team over time (metadata type_history / team_history). Empty for wells that never changed.
CREATE TABLE well_type_history (asset_id TEXT, well_type TEXT, valid_from TEXT, valid_to TEXT);
CREATE TABLE well_team_history (asset_id TEXT, team_id TEXT, valid_from TEXT, valid_to TEXT);
CREATE TABLE equipment (equipment_id TEXT PRIMARY KEY, asset_id TEXT, kind TEXT, model TEXT);

CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, inspected_at TEXT NOT NULL,
    inspector_user_id TEXT, method TEXT, ambient_temp_c REAL, wellhead_temp_c REAL,
    spill_detected INTEGER, spill_area_pct REAL, spill_est_volume_bbl REAL,
    evidence_images TEXT, notes TEXT,
    recorded_at TEXT                         -- set when the inspection was entered late
);
CREATE INDEX ix_ins_asset_time ON inspections (asset_id, inspected_at);

CREATE TABLE maintenance (
    maintenance_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    maintenance_type TEXT, equipment_kind TEXT, issue TEXT, action_taken TEXT,
    technician_user_id TEXT, contractor TEXT, downtime_hours REAL,
    cost_labor REAL, cost_parts REAL, cost_total REAL,
    triggered_by_inspection_id TEXT, report_file TEXT,
    recorded_at TEXT,                        -- set when the job was entered late
    cost_original_total REAL, cost_revised_on TEXT, cost_revision_reason TEXT
);
CREATE INDEX ix_mnt_asset_time ON maintenance (asset_id, started_at);

CREATE TABLE regulatory_events (
    event_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, date TEXT, agency TEXT, event_type TEXT,
    protocol_id TEXT, related_inspection_id TEXT, hours_to_report REAL, detail TEXT,
    fine_usd REAL, status TEXT
);

CREATE TABLE notes (
    note_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, created_at TEXT, author_user_id TEXT,
    note_type TEXT, inspection_id TEXT, text TEXT, valid_from TEXT, valid_to TEXT,
    field TEXT, original_value TEXT, corrected_value TEXT,   -- data_correction notes
    topic TEXT,          -- operator notes, classified at load (fieldnotes.py): gas, shut_in, restart, bypass_in, ...
    status_effect TEXT,  -- shut_in | restart | NULL (OPS-3: only these notes change a well's status)
    equipment_kind TEXT  -- equipment the note is about, when it names a symptom
);
CREATE INDEX ix_notes_asset ON notes (asset_id, created_at);
CREATE INDEX ix_notes_ins ON notes (inspection_id);

-- Facts extracted from the structured compliance table of repair reports.
CREATE TABLE report_facts (
    maintenance_id TEXT, asset_id TEXT, inspection_id TEXT, protocol TEXT, result TEXT, detail TEXT,
    contained_hours REAL, limit_hours REAL, report_path TEXT,
    UNIQUE (maintenance_id, protocol)
);
CREATE INDEX ix_rf_ins ON report_facts (inspection_id);

-- Free-text facts from repair reports: root cause, pump fitted, follow-up, why the well waited, H2S, soil samples.
CREATE TABLE report_details (
    maintenance_id TEXT PRIMARY KEY, asset_id TEXT, root_cause TEXT, failure_category TEXT,
    parts_model TEXT, parts_serial TEXT, parts_lot TEXT,
    follow_up_days INTEGER, follow_up_text TEXT, follow_up_of TEXT,
    delay_days REAL, delay_reason TEXT, delay_text TEXT,
    h2s_max_ppm REAL, h2s_stop_work INTEGER, soil_samples_lab INTEGER, cleanup_text TEXT, interim INTEGER,
    report_path TEXT
);

-- Rule values parsed from manuals/SOPs, with their source section for citation.
CREATE TABLE rule_params (well_type TEXT, name TEXT, value REAL, source TEXT, PRIMARY KEY (well_type, name));
-- Rules whose value depends on the date (SOP revisions): the value in force on a day is the row covering it.
CREATE TABLE rule_versions (name TEXT, value REAL, valid_from TEXT, valid_to TEXT, source TEXT);

-- Management-of-change memos (temporary bypass approvals). An extending memo replaces the expiry.
CREATE TABLE moc_memos (moc_id TEXT PRIMARY KEY, title TEXT, memo_date TEXT, effective_from TEXT, expires_on TEXT,
                        extends_moc TEXT, approved_by TEXT, path TEXT);
CREATE TABLE moc_wells (moc_id TEXT, asset_id TEXT, PRIMARY KEY (moc_id, asset_id));
-- Supplier recalls: which pump lots must be replaced, by when.
CREATE TABLE recalls (notice_id TEXT, model TEXT, lot TEXT, notice_date TEXT, replace_by TEXT, excludes TEXT, path TEXT,
                      PRIMARY KEY (notice_id, lot));
-- Regulator letters: what they cover and what counts as compliant.
CREATE TABLE letters (doc_id TEXT PRIMARY KEY, sender TEXT, addressee TEXT, letter_date TEXT, subject TEXT,
                      area_names TEXT, since TEXT, due TEXT, min_bbl REAL, excludes TEXT, required_text TEXT,
                      required_text_source TEXT, path TEXT);

-- L4 private: per-user preferences (sorting, units). Only ever read for that user.
CREATE TABLE user_prefs (user_id TEXT, name TEXT, value TEXT, text TEXT, created_at TEXT, PRIMARY KEY (user_id, name));

-- L2 documents: manuals, SOPs and reports chunked by heading, for citation and search.
CREATE TABLE doc_chunks (
    chunk_id INTEGER PRIMARY KEY, doc_kind TEXT, doc_id TEXT, asset_id TEXT, section TEXT,
    heading TEXT, path TEXT, text TEXT,
    UNIQUE (doc_kind, doc_id, path, section, heading)
);
CREATE INDEX ix_chunks_doc ON doc_chunks (doc_id);
CREATE VIRTUAL TABLE doc_fts USING fts5(heading, text, content='doc_chunks', content_rowid='chunk_id');

-- L4 private memory: each user's own conversations. Only ever queried with that user's ID.
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, started_at TEXT NOT NULL, title TEXT
);
CREATE TABLE turns (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT, asset_ids TEXT, created_at TEXT NOT NULL,
    answer_json TEXT   -- full answer (visual, evidence, flags, usage) for assistant turns
);
CREATE INDEX ix_turns_user ON turns (user_id, created_at);

-- L3 overlay: user-supplied corrections, rules, notes and claims. Append-only: an entry is
-- never edited or deleted, only superseded. It survives dataset reloads (see loader.PRESERVE).
CREATE TABLE overlay_entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- correction | rule | note | claim
    target_type TEXT NOT NULL,     -- inspection | well | well_type
    target_ref TEXT NOT NULL,      -- inspection_id, asset_id or well type
    field TEXT,                    -- e.g. wellhead_temp_c, spill_est_volume_bbl, rca_corrective_repairs
    original_value TEXT,           -- value in the raw data / manual when the entry was made
    new_value TEXT,
    text TEXT,                     -- free text of the claim or note
    reason TEXT,
    author_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT,
    status TEXT NOT NULL DEFAULT 'active',   -- active | unconfirmed | superseded
    superseded_by INTEGER,
    confirmed_by TEXT, confirmed_at TEXT
);
CREATE INDEX ix_ovl_target ON overlay_entries (target_type, target_ref, field);

-- L1 derived views --------------------------------------------------------------------------

-- Newest active correction/rule per target and field.
CREATE VIEW v_current_overlay AS
SELECT o.* FROM overlay_entries o
WHERE o.kind IN ('correction', 'rule') AND o.status = 'active'
  AND o.seq = (SELECT MAX(p.seq) FROM overlay_entries p
               WHERE p.kind = o.kind AND p.status = 'active' AND p.target_type = o.target_type
                 AND p.target_ref = o.target_ref AND IFNULL(p.field, '') = IFNULL(o.field, ''));

-- One row per inspection. wellhead_temp_c, ambient_temp_c, spill_detected and spill_est_volume_bbl are
-- EFFECTIVE values: a user correction (overlay) wins, then a data_correction note in the dataset, then the raw
-- reading; the raw_* columns keep the originals. The threshold is the one for the well's type ON THE DAY of the
-- reading (type_history). producing is NULL when the type has no parsed threshold: unknown, never guessed.
CREATE VIEW v_inspection AS
WITH base AS (
  SELECT i.inspection_id, i.asset_id, i.inspected_at, i.inspector_user_id, i.method, i.spill_area_pct,
         i.evidence_images, i.notes, i.recorded_at,
         i.wellhead_temp_c AS raw_wellhead_temp_c, i.ambient_temp_c AS raw_ambient_temp_c,
         i.spill_detected AS raw_spill_detected, i.spill_est_volume_bbl AS raw_spill_est_volume_bbl,
         COALESCE((SELECT CAST(c.new_value AS REAL) FROM v_current_overlay c WHERE c.kind = 'correction'
                   AND c.target_type = 'inspection' AND c.target_ref = i.inspection_id AND c.field = 'wellhead_temp_c'),
                  (SELECT CAST(n.corrected_value AS REAL) FROM notes n WHERE n.note_type = 'data_correction'
                   AND n.inspection_id = i.inspection_id AND n.field = 'wellhead_temp_c' ORDER BY n.created_at DESC LIMIT 1),
                  i.wellhead_temp_c) AS wellhead_temp_c,
         COALESCE((SELECT CAST(c.new_value AS REAL) FROM v_current_overlay c WHERE c.kind = 'correction'
                   AND c.target_type = 'inspection' AND c.target_ref = i.inspection_id AND c.field = 'ambient_temp_c'),
                  (SELECT CAST(n.corrected_value AS REAL) FROM notes n WHERE n.note_type = 'data_correction'
                   AND n.inspection_id = i.inspection_id AND n.field = 'ambient_temp_c' ORDER BY n.created_at DESC LIMIT 1),
                  i.ambient_temp_c) AS ambient_temp_c,
         COALESCE((SELECT CAST(c.new_value AS INTEGER) FROM v_current_overlay c WHERE c.kind = 'correction'
                   AND c.target_type = 'inspection' AND c.target_ref = i.inspection_id AND c.field = 'spill_detected'),
                  (SELECT CASE WHEN n.corrected_value IN ('1', 'true', 'True') THEN 1 ELSE 0 END FROM notes n
                   WHERE n.note_type = 'data_correction' AND n.inspection_id = i.inspection_id AND n.field = 'spill_detected'
                   ORDER BY n.created_at DESC LIMIT 1),
                  i.spill_detected) AS spill_detected,
         COALESCE((SELECT CAST(c.new_value AS REAL) FROM v_current_overlay c WHERE c.kind = 'correction'
                   AND c.target_type = 'inspection' AND c.target_ref = i.inspection_id AND c.field = 'spill_est_volume_bbl'),
                  (SELECT CAST(n.corrected_value AS REAL) FROM notes n WHERE n.note_type = 'data_correction'
                   AND n.inspection_id = i.inspection_id AND n.field = 'spill_est_volume_bbl' ORDER BY n.created_at DESC LIMIT 1),
                  i.spill_est_volume_bbl) AS spill_est_volume_bbl,
         (SELECT GROUP_CONCAT('OVL-' || c.seq) FROM v_current_overlay c
           WHERE c.kind = 'correction' AND c.target_type = 'inspection' AND c.target_ref = i.inspection_id) AS overlay_ids,
         (SELECT GROUP_CONCAT(n.note_id) FROM notes n
           WHERE n.note_type = 'data_correction' AND n.inspection_id = i.inspection_id) AS data_correction_ids,
         COALESCE((SELECT h.well_type FROM well_type_history h WHERE h.asset_id = i.asset_id
                   AND SUBSTR(i.inspected_at, 1, 10) >= h.valid_from AND (h.valid_to IS NULL OR SUBSTR(i.inspected_at, 1, 10) <= h.valid_to)),
                  (SELECT w.well_type FROM wells w WHERE w.asset_id = i.asset_id)) AS well_type
  FROM inspections i
)
SELECT b.*, w.area_id, w.lat, w.lon,
       rp.value AS threshold_c,
       ROUND(b.wellhead_temp_c - b.ambient_temp_c, 1) AS delta_c,
       CASE WHEN rp.value IS NULL OR b.wellhead_temp_c IS NULL OR b.ambient_temp_c IS NULL THEN NULL
            WHEN ROUND(b.wellhead_temp_c - b.ambient_temp_c, 1) >= rp.value THEN 1 ELSE 0 END AS producing,
       EXISTS (SELECT 1 FROM notes n WHERE n.note_type = 'correction' AND n.inspection_id = b.inspection_id) AS is_corrected,
       CASE WHEN b.spill_detected = 1 AND NOT EXISTS
            (SELECT 1 FROM notes n WHERE n.note_type = 'correction' AND n.inspection_id = b.inspection_id)
            THEN 1 ELSE 0 END AS real_spill,
       SUBSTR(b.inspected_at, 1, 10) AS day
FROM base b
JOIN wells w ON w.asset_id = b.asset_id
LEFT JOIN v_rule_effective rp ON rp.well_type = b.well_type AND rp.name = 'producing_delta_c';

-- Manual rule values with user rules (overlay, type scope) applied. source says where each came from.
CREATE VIEW v_rule_effective AS
SELECT rp.well_type, rp.name,
       COALESCE(CAST(o.new_value AS REAL), rp.value) AS value,
       CASE WHEN o.seq IS NULL THEN rp.source ELSE 'user rule OVL-' || o.seq || ' by ' || o.author_user_id END AS source,
       rp.value AS manual_value, o.seq IS NOT NULL AS overridden
FROM rule_params rp
LEFT JOIN v_current_overlay o ON o.kind = 'rule' AND o.target_type = 'well_type'
     AND o.target_ref = rp.well_type AND o.field = rp.name;

-- Each non-producing inspection counts as 7 days of downtime; missed inspections add nothing.
CREATE VIEW v_downtime AS
SELECT inspection_id, asset_id, area_id, well_type, day, 7 AS downtime_days
FROM v_inspection WHERE producing = 0;

CREATE VIEW v_latest_inspection AS
SELECT v.* FROM v_inspection v
WHERE v.inspected_at = (SELECT MAX(inspected_at) FROM inspections x WHERE x.asset_id = v.asset_id);

CREATE VIEW v_real_spills AS
SELECT * FROM v_inspection WHERE real_spill = 1;
