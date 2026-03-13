"""Database schema and connection management."""
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "leads.db"

_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with _lock:
        conn = get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin      INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS search_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                care_home_name  TEXT NOT NULL,
                postcode        TEXT NOT NULL,
                radius_km       REAL NOT NULL,
                lat             REAL,
                lon             REAL,
                sources         TEXT,
                user_id         INTEGER REFERENCES users(id),
                run_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS organisations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                org_type        TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_id       TEXT,
                address_line1   TEXT,
                address_line2   TEXT,
                town            TEXT,
                postcode        TEXT,
                lat             REAL,
                lon             REAL,
                distance_km     REAL,
                phone           TEXT,
                website         TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(source, source_id)
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id          INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
                name            TEXT,
                role            TEXT,
                email           TEXT,
                phone           TEXT,
                source_notes    TEXT
            );

            CREATE TABLE IF NOT EXISTS leads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id          INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
                search_run_id   INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
                priority_score  REAL NOT NULL DEFAULT 0.0,
                score_breakdown TEXT,
                status          TEXT DEFAULT 'new',
                contacted_at    TEXT,
                outcome         TEXT,
                notes           TEXT,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(org_id, search_run_id)
            );

            CREATE TABLE IF NOT EXISTS scoring_weights (
                org_type        TEXT PRIMARY KEY,
                base_weight     REAL NOT NULL DEFAULT 1.0,
                conversion_rate REAL DEFAULT 0.0,
                contacted_count INTEGER DEFAULT 0,
                converted_count INTEGER DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
        # Migrations
        for ddl in [
            "ALTER TABLE search_runs ADD COLUMN sources TEXT",
            "ALTER TABLE search_runs ADD COLUMN org_types TEXT",
            "ALTER TABLE search_runs ADD COLUMN hospital_depts TEXT",
            "ALTER TABLE search_runs ADD COLUMN user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE organisations ADD COLUMN email TEXT",
        ]:
            try:
                conn.execute(ddl)
                conn.commit()
            except Exception:
                pass
        conn.close()


DB_LOCK = _lock
