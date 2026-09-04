PRAGMA journal_mode = WAL;

CREATE TABLE schema_info (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL
);

INSERT INTO schema_info (singleton, schema_version) VALUES (1, 1);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version >= 1),
    payload TEXT NOT NULL
);

CREATE TABLE todos (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE evidence (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE notes (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE gates (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE approvals (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE audit (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);

CREATE INDEX events_run_cursor ON events(run_id, event_id);
