import sqlite3

import pytest

from ledger.db import initialize


async def test_schema_is_repeatable_and_history_is_append_only(tmp_path):
    path = tmp_path / "ledger.sqlite"
    await initialize(path)
    await initialize(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO runs(run_id,user,policy_json,created_at) VALUES ('r','u','{}','now')")
        db.execute("INSERT INTO findings(run_id,finding_id,agent,status,severity) "
                   "VALUES ('r','f','identity','UNVERIFIABLE',2)")
        db.execute("INSERT INTO finding_revisions(run_id,revision_id,finding_id,author,severity) "
                   "VALUES ('r','rev','f','identity',3)")
        assert db.execute("SELECT severity,severity_final FROM effective_findings").fetchone() == (2,3)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE findings SET severity=5")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM findings")
        db.execute("UPDATE runs SET phase='ROUND1'")
        db.execute("INSERT INTO events(run_id,kind,payload_json) VALUES ('r','start','{}')")
        assert db.execute("SELECT event_id FROM events").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO events(run_id,kind,payload_json) VALUES ('other','start','{}')")
