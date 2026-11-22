"""Versioned, append-only schema migrations. Never edit a released migration; add a new one."""

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", r"""
CREATE TABLE preferences (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,            -- health_connect | samsung_sdk | samsung_export | google_health | manual | synthetic | vitalis
  app_package TEXT,                  -- data origin (e.g. com.sec.android.app.shealth)
  device_type TEXT,
  device_manufacturer TEXT,
  device_model TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE(provider, app_package, device_type, device_manufacturer, device_model)
);

CREATE TABLE import_batches (
  id TEXT PRIMARY KEY,               -- batch UUID (sync batch_id or generated)
  provider TEXT NOT NULL,
  import_method TEXT NOT NULL,       -- lan_sync | file_sync | file_import | oauth_api | manual | synthetic
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,              -- running | completed | failed | partial
  records_received INTEGER DEFAULT 0,
  records_inserted INTEGER DEFAULT 0,
  records_updated INTEGER DEFAULT 0,
  records_duplicate INTEGER DEFAULT 0,
  records_rejected INTEGER DEFAULT 0,
  records_deleted INTEGER DEFAULT 0,
  detail TEXT                        -- JSON (errors, unsupported types, device info)
);

CREATE TABLE raw_records (
  id INTEGER PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES import_batches(id),
  provider TEXT NOT NULL,
  record_type TEXT NOT NULL,
  provider_record_id TEXT,
  payload TEXT NOT NULL,             -- exact JSON as received
  payload_sha256 TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE INDEX ix_raw_provider_id ON raw_records(provider, provider_record_id);
CREATE INDEX ix_raw_hash ON raw_records(payload_sha256);

CREATE TABLE measurements (
  id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,    -- stable UUIDv5
  provider TEXT NOT NULL,
  provider_record_id TEXT,
  source_id INTEGER REFERENCES sources(id),
  metric TEXT NOT NULL,
  value_num REAL,
  value_text TEXT,
  unit TEXT NOT NULL,
  orig_value TEXT,
  orig_unit TEXT,
  start_utc TEXT NOT NULL,
  end_utc TEXT,
  utc_offset_min INTEGER,            -- original offset at start, if known
  local_date TEXT NOT NULL,
  provenance TEXT NOT NULL,
  quality_flags INTEGER NOT NULL DEFAULT 0,
  confidence REAL,
  import_method TEXT NOT NULL,
  batch_id TEXT,
  raw_id INTEGER REFERENCES raw_records(id),
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  superseded_by TEXT,                -- record_id of the winning duplicate
  deleted_at TEXT,
  meta TEXT                          -- JSON (method, sensor location, context)
);
CREATE INDEX ix_meas_metric_time ON measurements(metric, start_utc);
CREATE INDEX ix_meas_metric_date ON measurements(metric, local_date);
CREATE INDEX ix_meas_provider_id ON measurements(provider, provider_record_id);

CREATE TABLE sleep_sessions (
  id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  provider_record_id TEXT,
  source_id INTEGER REFERENCES sources(id),
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  utc_offset_min INTEGER,
  local_date TEXT NOT NULL,          -- local date of wake time
  title TEXT,
  has_stages INTEGER NOT NULL DEFAULT 0,
  provenance TEXT NOT NULL,
  quality_flags INTEGER NOT NULL DEFAULT 0,
  import_method TEXT NOT NULL,
  batch_id TEXT,
  raw_id INTEGER,
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  superseded_by TEXT,
  deleted_at TEXT,
  meta TEXT
);
CREATE INDEX ix_sleep_date ON sleep_sessions(local_date);
CREATE INDEX ix_sleep_start ON sleep_sessions(start_utc);

CREATE TABLE sleep_stages (
  id INTEGER PRIMARY KEY,
  session_record_id TEXT NOT NULL REFERENCES sleep_sessions(record_id) ON DELETE CASCADE,
  stage TEXT NOT NULL,               -- awake | light | deep | rem | asleep | out_of_bed | awake_in_bed | unknown
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  orig_code TEXT
);
CREATE INDEX ix_stage_session ON sleep_stages(session_record_id);

CREATE TABLE exercise_sessions (
  id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  provider_record_id TEXT,
  source_id INTEGER REFERENCES sources(id),
  exercise_type TEXT NOT NULL,
  title TEXT,
  start_utc TEXT NOT NULL,
  end_utc TEXT NOT NULL,
  utc_offset_min INTEGER,
  local_date TEXT NOT NULL,
  distance_m REAL,
  active_kcal REAL,
  avg_hr REAL,
  max_hr REAL,
  provenance TEXT NOT NULL,
  quality_flags INTEGER NOT NULL DEFAULT 0,
  import_method TEXT NOT NULL,
  batch_id TEXT,
  raw_id INTEGER,
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  superseded_by TEXT,
  deleted_at TEXT,
  meta TEXT
);
CREATE INDEX ix_ex_date ON exercise_sessions(local_date);

CREATE TABLE daily_features (
  local_date TEXT NOT NULL,
  feature TEXT NOT NULL,
  value REAL,
  algo_version TEXT NOT NULL,
  provenance TEXT NOT NULL DEFAULT 'calculated',
  detail TEXT,                       -- JSON (inputs, sources, coverage)
  computed_at TEXT NOT NULL,
  PRIMARY KEY (local_date, feature)
);
CREATE INDEX ix_feat_feature ON daily_features(feature, local_date);

CREATE TABLE scores (
  local_date TEXT NOT NULL,
  kind TEXT NOT NULL,                -- recovery | energy_summary | arousal | load
  value REAL,
  z REAL,
  category TEXT,
  completeness REAL,
  baseline_status TEXT,
  algo_version TEXT NOT NULL,
  detail TEXT NOT NULL,              -- JSON: inputs used/missing, contributions, limitations
  computed_at TEXT NOT NULL,
  PRIMARY KEY (local_date, kind)
);

CREATE TABLE insights (
  id TEXT PRIMARY KEY,
  dedup_key TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL,
  severity TEXT NOT NULL,            -- info | notice | attention
  title TEXT NOT NULL,
  observation TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  numbers TEXT NOT NULL,             -- JSON
  baseline TEXT,
  completeness REAL,
  confidence TEXT NOT NULL,
  action TEXT,
  evidence TEXT NOT NULL,            -- JSON
  algo_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  dismissed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE journal_habits (
  key TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  category TEXT NOT NULL,
  value_type TEXT NOT NULL,          -- boolean | number | time | category | scale | text
  unit TEXT,
  options TEXT,                      -- JSON (choices, min, max)
  private INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  builtin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE journal_entries (
  local_date TEXT NOT NULL,
  habit_key TEXT NOT NULL REFERENCES journal_habits(key) ON DELETE CASCADE,
  value_num REAL,
  value_text TEXT,
  note TEXT,
  tags TEXT,
  provenance TEXT NOT NULL DEFAULT 'user_entered',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (local_date, habit_key)
);

CREATE TABLE day_notes (
  local_date TEXT PRIMARY KEY,
  note TEXT,
  tags TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE experiments (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  habit_key TEXT NOT NULL,
  condition TEXT NOT NULL,           -- JSON {op, value}
  outcome TEXT NOT NULL,
  lag_days INTEGER NOT NULL DEFAULT 0,
  design TEXT NOT NULL,              -- ab | randomized_blocks
  start_date TEXT NOT NULL,
  baseline_days INTEGER NOT NULL,
  intervention_days INTEGER NOT NULL,
  block_days INTEGER NOT NULL DEFAULT 2,
  seed INTEGER NOT NULL,
  schedule TEXT,                     -- JSON {date: "intervention"|"control"|"baseline"}
  status TEXT NOT NULL,              -- planned | running | completed | cancelled
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sleep_plans (
  id TEXT PRIMARY KEY,
  wake_time TEXT NOT NULL,
  bedtime_start TEXT NOT NULL,
  bedtime_end TEXT NOT NULL,
  need_min REAL NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT,
  routine TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE chat_threads (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  provider TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE chat_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                -- user | assistant | tool
  content TEXT NOT NULL,
  tool_calls TEXT,                   -- JSON
  evidence TEXT,                     -- JSON
  provider TEXT,
  model TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_msg_thread ON chat_messages(thread_id, created_at);

CREATE TABLE ai_disclosures (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  provider TEXT NOT NULL,
  model TEXT,
  categories TEXT NOT NULL,          -- JSON list of data categories
  payload_sha256 TEXT NOT NULL,
  payload_bytes INTEGER NOT NULL,
  approved_at TEXT NOT NULL
);

CREATE TABLE sync_devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen TEXT,
  revoked_at TEXT
);

CREATE TABLE sync_pairing_codes (
  code_sha256 TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE sync_secrets (
  name TEXT PRIMARY KEY,             -- tls_key_pem | tls_cert_pem | file_key
  value BLOB NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE availability_reports (
  id INTEGER PRIMARY KEY,
  device_id TEXT,
  provider TEXT NOT NULL,
  received_at TEXT NOT NULL,
  report TEXT NOT NULL               -- JSON
);

CREATE TABLE analysis_versions (
  component TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""),
    (2, "sleep end offset (DST-correct wake times)", r"""
ALTER TABLE sleep_sessions ADD COLUMN end_offset_min INTEGER;
"""),
    (3, "journal/experiment sync between phone and PC (change times and deletion markers)", r"""
ALTER TABLE journal_habits ADD COLUMN updated_at TEXT;
UPDATE journal_habits SET updated_at = created_at WHERE updated_at IS NULL;
CREATE TABLE journal_tombstones (
  local_date TEXT NOT NULL,
  habit_key TEXT NOT NULL,
  deleted_at TEXT NOT NULL,
  PRIMARY KEY (local_date, habit_key)
);
CREATE TABLE habit_tombstones (key TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
CREATE TABLE experiment_tombstones (id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
"""),
]
