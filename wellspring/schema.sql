-- L0 raw records (read-only after load), L1 derived views, L2 documents.

CREATE TABLE teams (team_id TEXT PRIMARY KEY, name TEXT, lead_user_id TEXT);
CREATE TABLE users (user_id TEXT PRIMARY KEY, name TEXT, role TEXT, team_id TEXT);
CREATE TABLE areas (area_id TEXT PRIMARY KEY, name TEXT, team_id TEXT, center_lat REAL, center_lon REAL);

CREATE TABLE wells (
    asset_id TEXT PRIMARY KEY, name TEXT, well_type TEXT, area_id TEXT, team_id TEXT,
    in_charge_user_id TEXT, lat REAL, lon REAL, installed_on TEXT, recorded_status TEXT,
    last_inspection_id TEXT, source TEXT
);
CREATE TABLE equipment (equipment_id TEXT PRIMARY KEY, asset_id TEXT, kind TEXT, model TEXT);

CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, inspected_at TEXT NOT NULL,
    inspector_user_id TEXT, method TEXT, ambient_temp_c REAL, wellhead_temp_c REAL,
    spill_detected INTEGER, spill_area_pct REAL, spill_est_volume_bbl REAL,
    evidence_images TEXT, notes TEXT
);
CREATE INDEX ix_ins_asset_time ON inspections (asset_id, inspected_at);

CREATE TABLE maintenance (
    maintenance_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    maintenance_type TEXT, equipment_kind TEXT, issue TEXT, action_taken TEXT,
    technician_user_id TEXT, contractor TEXT, downtime_hours REAL,
    cost_labor REAL, cost_parts REAL, cost_total REAL,
    triggered_by_inspection_id TEXT, report_file TEXT
);
CREATE INDEX ix_mnt_asset_time ON maintenance (asset_id, started_at);

CREATE TABLE regulatory_events (
    event_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, date TEXT, agency TEXT, event_type TEXT,
    protocol_id TEXT, related_inspection_id TEXT, hours_to_report REAL, detail TEXT,
    fine_usd REAL, status TEXT
);

CREATE TABLE notes (
    note_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, created_at TEXT, author_user_id TEXT,
    note_type TEXT, inspection_id TEXT, text TEXT, valid_from TEXT, valid_to TEXT
);
CREATE INDEX ix_notes_ins ON notes (inspection_id);

-- Facts extracted from the structured compliance table of repair reports.
CREATE TABLE report_facts (
    maintenance_id TEXT, asset_id TEXT, inspection_id TEXT, protocol TEXT, result TEXT,
    contained_hours REAL, limit_hours REAL, report_path TEXT,
    UNIQUE (maintenance_id, protocol)
);
CREATE INDEX ix_rf_ins ON report_facts (inspection_id);

-- Rule values parsed from manuals/SOPs, with their source section for citation.
CREATE TABLE rule_params (well_type TEXT, name TEXT, value REAL, source TEXT, PRIMARY KEY (well_type, name));

-- L2 documents: manuals, SOPs and reports chunked by heading, for citation and search.
CREATE TABLE doc_chunks (
    chunk_id INTEGER PRIMARY KEY, doc_kind TEXT, doc_id TEXT, asset_id TEXT, section TEXT,
    heading TEXT, path TEXT, text TEXT,
    UNIQUE (doc_kind, doc_id, path, section, heading)
);
CREATE INDEX ix_chunks_doc ON doc_chunks (doc_id);

-- L1 derived views --------------------------------------------------------------------------

-- One row per inspection with the producing flag and effective (post-correction) spill flag.
-- producing is NULL when the well type has no parsed threshold: unknown, never guessed.
CREATE VIEW v_inspection AS
SELECT i.*, w.well_type, w.area_id, w.lat, w.lon,
       rp.value AS threshold_c,
       ROUND(i.wellhead_temp_c - i.ambient_temp_c, 1) AS delta_c,
       CASE WHEN rp.value IS NULL THEN NULL
            WHEN ROUND(i.wellhead_temp_c - i.ambient_temp_c, 1) >= rp.value THEN 1 ELSE 0 END AS producing,
       EXISTS (SELECT 1 FROM notes n WHERE n.note_type = 'correction' AND n.inspection_id = i.inspection_id) AS is_corrected,
       CASE WHEN i.spill_detected = 1 AND NOT EXISTS
            (SELECT 1 FROM notes n WHERE n.note_type = 'correction' AND n.inspection_id = i.inspection_id)
            THEN 1 ELSE 0 END AS real_spill,
       SUBSTR(i.inspected_at, 1, 10) AS day
FROM inspections i
JOIN wells w ON w.asset_id = i.asset_id
LEFT JOIN rule_params rp ON rp.well_type = w.well_type AND rp.name = 'producing_delta_c';

-- Each non-producing inspection counts as 7 days of downtime; missed inspections add nothing.
CREATE VIEW v_downtime AS
SELECT inspection_id, asset_id, area_id, well_type, day, 7 AS downtime_days
FROM v_inspection WHERE producing = 0;

CREATE VIEW v_latest_inspection AS
SELECT v.* FROM v_inspection v
WHERE v.inspected_at = (SELECT MAX(inspected_at) FROM inspections x WHERE x.asset_id = v.asset_id);

CREATE VIEW v_real_spills AS
SELECT * FROM v_inspection WHERE real_spill = 1;
