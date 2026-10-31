PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, user TEXT NOT NULL, company_name TEXT, domain TEXT,
  crustdata_company_id INTEGER, status TEXT NOT NULL DEFAULT 'queued',
  phase TEXT NOT NULL DEFAULT 'QUEUED', policy_json TEXT NOT NULL,
  deck_path TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
  run_id TEXT REFERENCES runs(run_id), claim_id TEXT, topic TEXT, text TEXT,
  value TEXT, unit TEXT, period TEXT, as_of TEXT, slide INTEGER, testable INTEGER,
  PRIMARY KEY (run_id, claim_id)
);
CREATE TABLE IF NOT EXISTS claim_sets (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id), company_name TEXT,
  is_stub INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evidence (
  run_id TEXT REFERENCES runs(run_id), evidence_id TEXT, agent TEXT, source TEXT,
  field_path TEXT, value_json TEXT, fetched_at TEXT, crustdata_updated_at TEXT,
  PRIMARY KEY (run_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT REFERENCES runs(run_id), agent TEXT, name TEXT, value REAL,
  unit TEXT, formula TEXT, evidence_ids TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS findings (
  run_id TEXT REFERENCES runs(run_id), finding_id TEXT, agent TEXT, claim_id TEXT,
  status TEXT CHECK (status IN ('VERIFIED','PARTIAL','CONTRADICTED','UNVERIFIABLE')),
  severity INTEGER CHECK (severity BETWEEN 1 AND 5), note TEXT, evidence_ids TEXT,
  round INTEGER, is_stub INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, finding_id),
  FOREIGN KEY (run_id, claim_id) REFERENCES claims(run_id, claim_id)
);
CREATE TABLE IF NOT EXISTS challenges (
  run_id TEXT REFERENCES runs(run_id), challenge_id TEXT, from_agent TEXT, to_agent TEXT,
  finding_id TEXT, type TEXT, argument TEXT, evidence_ids TEXT,
  PRIMARY KEY (run_id, challenge_id),
  FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS replies (
  run_id TEXT REFERENCES runs(run_id), reply_id TEXT, challenge_id TEXT,
  agent TEXT, action TEXT, text TEXT, new_severity INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, reply_id),
  FOREIGN KEY (run_id, challenge_id) REFERENCES challenges(run_id, challenge_id)
);
CREATE TABLE IF NOT EXISTS finding_revisions (
  revision_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT REFERENCES runs(run_id), revision_id TEXT NOT NULL,
  finding_id TEXT, author TEXT, severity INTEGER CHECK (severity BETWEEN 1 AND 5),
  reason TEXT, evidence_ids TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (run_id, revision_id),
  FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS verdicts (
  run_id TEXT REFERENCES runs(run_id), agent TEXT, score REAL, confidence REAL,
  stance TEXT, flags_json TEXT, is_stub INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, agent)
);
CREATE TABLE IF NOT EXISTS decisions (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id), total_score REAL,
  credibility REAL, confidence REAL, verdict TEXT, gates_json TEXT,
  check_size_usd REAL, narrative TEXT, breakdown_json TEXT, is_stub INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agent_status (
  run_id TEXT REFERENCES runs(run_id), agent TEXT, phase TEXT, state TEXT,
  error TEXT, started_at TEXT, ended_at TEXT, PRIMARY KEY (run_id, agent, phase)
);
CREATE TABLE IF NOT EXISTS context_packets (
  run_id TEXT REFERENCES runs(run_id), agent TEXT, packet_json TEXT NOT NULL,
  PRIMARY KEY (run_id, agent)
);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS events_run_cursor ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS revisions_finding_order
ON finding_revisions(run_id, finding_id, revision_seq);
CREATE INDEX IF NOT EXISTS findings_agent ON findings(run_id, agent);
CREATE UNIQUE INDEX IF NOT EXISTS replies_one_per_challenge ON replies(run_id, challenge_id);
CREATE TABLE IF NOT EXISTS api_cache (
  cache_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT,
  fetched_at TEXT, ttl_seconds INTEGER
);

CREATE VIEW IF NOT EXISTS effective_findings AS
SELECT f.*, COALESCE((
  SELECT r.severity FROM finding_revisions r
  WHERE r.run_id = f.run_id AND r.finding_id = f.finding_id
  ORDER BY r.revision_seq DESC LIMIT 1
), f.severity) AS severity_final
FROM findings f;

CREATE TRIGGER IF NOT EXISTS claim_sets_no_update
BEFORE UPDATE ON claim_sets BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS claim_sets_no_delete
BEFORE DELETE ON claim_sets BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;

-- Preserve history; only coordinator state and cache are mutable.
CREATE TRIGGER IF NOT EXISTS claims_no_update
BEFORE UPDATE ON claims BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS claims_no_delete
BEFORE DELETE ON claims BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS evidence_no_update
BEFORE UPDATE ON evidence BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete
BEFORE DELETE ON evidence BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS metrics_no_update
BEFORE UPDATE ON metrics BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS metrics_no_delete
BEFORE DELETE ON metrics BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS findings_no_update
BEFORE UPDATE ON findings BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS findings_no_delete
BEFORE DELETE ON findings BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS challenges_no_update
BEFORE UPDATE ON challenges BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS challenges_no_delete
BEFORE DELETE ON challenges BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS replies_no_update
BEFORE UPDATE ON replies BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS replies_no_delete
BEFORE DELETE ON replies BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS finding_revisions_no_update
BEFORE UPDATE ON finding_revisions BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS finding_revisions_no_delete
BEFORE DELETE ON finding_revisions BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS verdicts_no_update
BEFORE UPDATE ON verdicts BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS verdicts_no_delete
BEFORE DELETE ON verdicts BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS decisions_no_update
BEFORE UPDATE ON decisions BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON decisions BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS context_packets_no_update
BEFORE UPDATE ON context_packets BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS context_packets_no_delete
BEFORE DELETE ON context_packets BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'append-only record');
END;
