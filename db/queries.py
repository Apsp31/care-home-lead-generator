"""All database read/write helpers. No business logic here."""
import json
import sqlite3
from typing import Optional
from .schema import get_connection, DB_LOCK


# --- Search Runs ---

def create_search_run(care_home_name: str, postcode: str, radius_km: float,
                      lat: float, lon: float, sources: list[str] | None = None,
                      org_types: list[str] | None = None,
                      hospital_depts: list[str] | None = None) -> int:
    with DB_LOCK:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO search_runs "
            "(care_home_name, postcode, radius_km, lat, lon, sources, org_types, hospital_depts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (care_home_name, postcode, radius_km, lat, lon,
             json.dumps(sources or []),
             json.dumps(org_types) if org_types is not None else None,
             json.dumps(hospital_depts) if hospital_depts is not None else None)
        )
        conn.commit()
        run_id = cur.lastrowid
        conn.close()
    return run_id


def get_distinct_care_homes() -> list[dict]:
    """One row per unique care_home_name with the most recent run's settings."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT care_home_name, postcode, radius_km, sources, org_types, hospital_depts
        FROM search_runs
        WHERE id IN (
            SELECT MAX(id) FROM search_runs GROUP BY care_home_name
        )
        ORDER BY run_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_search_runs() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM search_runs ORDER BY run_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_search_run(run_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM search_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Organisations ---

def upsert_organisation(org: dict) -> int:
    """Insert or ignore (deduplication on source+source_id). Returns org id."""
    with DB_LOCK:
        conn = get_connection()
        conn.execute("""
            INSERT OR IGNORE INTO organisations
                (name, org_type, source, source_id, address_line1, address_line2,
                 town, postcode, lat, lon, distance_km, phone, email, website)
            VALUES (:name, :org_type, :source, :source_id, :address_line1,
                    :address_line2, :town, :postcode, :lat, :lon, :distance_km,
                    :phone, :email, :website)
        """, {
            "name": org.get("name", ""),
            "org_type": org.get("org_type", ""),
            "source": org.get("source", ""),
            "source_id": org.get("source_id", ""),
            "address_line1": org.get("address_line1", ""),
            "address_line2": org.get("address_line2", ""),
            "town": org.get("town", ""),
            "postcode": org.get("postcode", ""),
            "lat": org.get("lat"),
            "lon": org.get("lon"),
            "distance_km": org.get("distance_km"),
            "phone": org.get("phone", ""),
            "email": org.get("email", ""),
            "website": org.get("website", ""),
        })
        row = conn.execute(
            "SELECT id FROM organisations WHERE source=? AND source_id=?",
            (org.get("source", ""), org.get("source_id", ""))
        ).fetchone()
        # If source_id is blank (can't deduplicate), get by last insert
        if row is None:
            row = conn.execute(
                "SELECT id FROM organisations WHERE name=? AND postcode=?",
                (org.get("name", ""), org.get("postcode", ""))
            ).fetchone()
        org_id = row["id"] if row else None
        conn.commit()
        conn.close()
    return org_id


def get_org(org_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM organisations WHERE id=?", (org_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Contacts ---

def insert_contacts(org_id: int, contacts: list[dict]):
    with DB_LOCK:
        conn = get_connection()
        for c in contacts:
            conn.execute("""
                INSERT INTO contacts (org_id, name, role, email, phone, source_notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (org_id, c.get("name"), c.get("role"), c.get("email"),
                  c.get("phone"), c.get("source_notes")))
        conn.commit()
        conn.close()


def get_contacts_for_org(org_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM contacts WHERE org_id=?", (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Leads ---

def upsert_lead(org_id: int, run_id: int, score: float, breakdown: dict) -> int:
    with DB_LOCK:
        conn = get_connection()
        conn.execute("""
            INSERT INTO leads (org_id, search_run_id, priority_score, score_breakdown)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(org_id, search_run_id) DO UPDATE SET
                priority_score=excluded.priority_score,
                score_breakdown=excluded.score_breakdown,
                updated_at=datetime('now')
        """, (org_id, run_id, score, json.dumps(breakdown)))
        row = conn.execute(
            "SELECT id FROM leads WHERE org_id=? AND search_run_id=?",
            (org_id, run_id)
        ).fetchone()
        lead_id = row["id"]
        conn.commit()
        conn.close()
    return lead_id


def get_leads_for_run(run_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT l.*, o.name, o.org_type, o.source, o.source_id,
               o.address_line1, o.address_line2,
               o.town, o.postcode, o.distance_km, o.phone, o.email, o.website,
               o.lat, o.lon
        FROM leads l
        JOIN organisations o ON o.id = l.org_id
        WHERE l.search_run_id = ?
        ORDER BY l.priority_score DESC
    """, (run_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_lead_status(lead_id: int, status: str, notes: str = ""):
    with DB_LOCK:
        conn = get_connection()
        conn.execute("""
            UPDATE leads SET status=?, notes=?,
                contacted_at = CASE WHEN ? IN ('contacted','converted','not_converted')
                               THEN datetime('now') ELSE contacted_at END,
                updated_at=datetime('now')
            WHERE id=?
        """, (status, notes, status, lead_id))
        conn.commit()
        conn.close()


def get_lead(lead_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("""
        SELECT l.*, o.name, o.org_type, o.address_line1, o.address_line2,
               o.town, o.postcode, o.distance_km, o.phone, o.website
        FROM leads l JOIN organisations o ON o.id = l.org_id
        WHERE l.id=?
    """, (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Scoring Weights ---

def get_scoring_weights() -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM scoring_weights").fetchall()
    conn.close()
    return {r["org_type"]: dict(r) for r in rows}


def upsert_scoring_weight(org_type: str, base_weight: float,
                          contacted_count: int, converted_count: int):
    conversion_rate = converted_count / max(contacted_count, 1)
    with DB_LOCK:
        conn = get_connection()
        conn.execute("""
            INSERT INTO scoring_weights
                (org_type, base_weight, conversion_rate, contacted_count, converted_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(org_type) DO UPDATE SET
                base_weight=excluded.base_weight,
                conversion_rate=excluded.conversion_rate,
                contacted_count=excluded.contacted_count,
                converted_count=excluded.converted_count,
                updated_at=datetime('now')
        """, (org_type, base_weight, conversion_rate, contacted_count, converted_count))
        conn.commit()
        conn.close()


def get_feedback_counts_by_type() -> dict[str, dict]:
    """Returns contacted/converted counts grouped by org_type."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT o.org_type,
               COUNT(CASE WHEN l.status IN ('contacted','converted','not_converted') THEN 1 END) AS contacted,
               COUNT(CASE WHEN l.status = 'converted' THEN 1 END) AS converted
        FROM leads l JOIN organisations o ON o.id = l.org_id
        GROUP BY o.org_type
    """).fetchall()
    conn.close()
    return {r["org_type"]: {"contacted": r["contacted"], "converted": r["converted"]}
            for r in rows}
