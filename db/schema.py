"""Database schema and connection management.

Supports SQLite (local dev) and PostgreSQL (production / Streamlit Cloud).
Set DATABASE_URL env var / Streamlit secret to switch to PostgreSQL.
"""
import os
import re
import sqlite3
import threading
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = Path(__file__).parent.parent / "leads.db"

_lock = threading.Lock()
DB_LOCK = _lock


def _is_pg() -> bool:
    return bool(DATABASE_URL)


def _adapt_sql(sql: str) -> str:
    """Convert SQLite-dialect SQL to PostgreSQL-compatible SQL."""
    # DDL
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"DEFAULT \(datetime\('now'\)\)", "DEFAULT NOW()", sql, flags=re.I)
    sql = re.sub(r"ADD COLUMN (?!IF NOT EXISTS)(\w+)", r"ADD COLUMN IF NOT EXISTS \1", sql)
    # DML
    sql = re.sub(r"\bdatetime\('now'\)", "NOW()", sql, flags=re.I)
    # Placeholders: ? → %s  and  :name → %(name)s
    sql = re.sub(r"\?", "%s", sql)
    sql = re.sub(r":(\w+)", r"%(\1)s", sql)
    return sql


class _Row(dict):
    """Dict that also supports integer-index access for sqlite3.Row compatibility."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, raw, backend: str):
        self._raw = raw
        self._backend = backend
        self.lastrowid: int | None = None

    def fetchone(self):
        row = self._raw.fetchone()
        if row is None:
            return None
        return _Row(dict(row)) if self._backend == "pg" else row

    def fetchall(self):
        rows = self._raw.fetchall()
        if self._backend == "pg":
            return [_Row(dict(r)) for r in rows]
        return rows


class _Conn:
    """Thin connection wrapper that normalises SQLite and PostgreSQL behaviour."""

    def __init__(self):
        self._backend = "pg" if _is_pg() else "sqlite"
        if self._backend == "pg":
            import psycopg2
            import psycopg2.extras
            url = DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            self._raw = psycopg2.connect(url)
            self._cf = psycopg2.extras.RealDictCursor
        else:
            self._raw = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            self._raw.row_factory = sqlite3.Row
            self._raw.execute("PRAGMA journal_mode=WAL")
            self._raw.execute("PRAGMA foreign_keys=ON")
            self._cf = None

    def execute(self, sql: str, params=None):
        if self._backend == "pg":
            adapted = _adapt_sql(sql)
            # Auto-append RETURNING id to plain INSERTs so lastrowid is available
            is_insert = adapted.strip().upper().startswith("INSERT")
            wants_returning = (
                is_insert
                and "RETURNING" not in adapted.upper()
                and "ON CONFLICT" not in adapted.upper()
            )
            if wants_returning:
                adapted = adapted.rstrip().rstrip(";") + " RETURNING *"
            cur = self._raw.cursor(cursor_factory=self._cf)
            cur.execute(adapted, params)
            c = _Cursor(cur, "pg")
            if wants_returning:
                row = cur.fetchone()
                if row and isinstance(row, dict) and "id" in row:
                    c.lastrowid = row["id"]
            return c
        else:
            raw = self._raw.execute(sql, params if params is not None else ())
            c = _Cursor(raw, "sqlite")
            c.lastrowid = raw.lastrowid
            return c

    def executescript(self, script: str):
        if self._backend == "pg":
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if not stmt or stmt.upper().startswith("PRAGMA"):
                    continue
                adapted = _adapt_sql(stmt)
                try:
                    self._raw.cursor().execute(adapted)
                except Exception:
                    self._raw.rollback()
            self._raw.commit()
        else:
            self._raw.executescript(script)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def get_connection() -> _Conn:
    return _Conn()


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
                org_types       TEXT,
                hospital_depts  TEXT,
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
                email           TEXT,
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

        # Migrations — idempotent on both backends
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

        # One-time dedup cleanup (SQLite only — PG won't have legacy dups)
        try:
            conn.execute("""
                DELETE FROM leads
                WHERE id IN (
                    SELECT l2.id
                    FROM leads l1
                    JOIN leads l2
                      ON l1.search_run_id = l2.search_run_id
                      AND l1.id < l2.id
                    JOIN organisations o1 ON o1.id = l1.org_id
                    JOIN organisations o2 ON o2.id = l2.org_id
                    WHERE o1.name = o2.name
                      AND o1.org_type = o2.org_type
                      AND o1.source = o2.source
                      AND abs(COALESCE(o1.lat,0) - COALESCE(o2.lat,0)) < 0.01
                      AND abs(COALESCE(o1.lon,0) - COALESCE(o2.lon,0)) < 0.01
                )
            """)
            conn.commit()
        except Exception:
            pass

        conn.close()
