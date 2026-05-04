"""Authentication: register, login, session tokens, user management."""
import hashlib
import secrets
from datetime import datetime, timedelta

from db.schema import get_connection, DB_LOCK

TOKEN_EXPIRY_DAYS = 90
_SALT = b"care_lead_gen_v1"


def _hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 100_000).hex()


def register_user(username: str, password: str) -> tuple[bool, str]:
    """Returns (success, error_msg). First registered user is auto-promoted to admin."""
    username = username.strip()
    if not username:
        return False, "Username cannot be blank."
    if not password:
        return False, "Password cannot be blank."
    pw_hash = _hash_password(password)
    try:
        with DB_LOCK:
            conn = get_connection()
            count = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()["cnt"]
            is_admin = 1 if count == 0 else 0
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                (username, pw_hash, is_admin),
            )
            conn.commit()
            conn.close()
        return True, ""
    except Exception as e:
        if "UNIQUE" in str(e):
            return False, "Username already taken."
        return False, str(e)


def login_user(username: str, password: str) -> tuple[str | None, str]:
    """Returns (session_token, error_msg). Token is None on failure."""
    pw_hash = _hash_password(password)
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM users WHERE username=? AND password_hash=?",
        (username.strip(), pw_hash),
    ).fetchone()
    conn.close()
    if not row:
        return None, "Invalid username or password."
    user_id = row["id"]
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)).isoformat()
    with DB_LOCK:
        conn = get_connection()
        conn.execute(
            "INSERT INTO user_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.commit()
        conn.close()
    return token, ""


def get_user_from_token(token: str) -> dict | None:
    """Returns {id, username, is_admin} if token valid and unexpired, else None."""
    if not token:
        return None
    conn = get_connection()
    row = conn.execute(
        """
        SELECT u.id, u.username, u.is_admin, s.expires_at
        FROM user_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        logout_user(token)
        return None
    return dict(row)


def logout_user(token: str):
    with DB_LOCK:
        conn = get_connection()
        conn.execute("DELETE FROM user_sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()


def get_all_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.is_admin, u.created_at,
               COUNT(sr.id) AS search_count,
               MAX(sr.run_at) AS last_search
        FROM users u
        LEFT JOIN search_runs sr ON sr.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_admin(user_id: int, is_admin: bool):
    with DB_LOCK:
        conn = get_connection()
        conn.execute(
            "UPDATE users SET is_admin=? WHERE id=?",
            (1 if is_admin else 0, user_id),
        )
        conn.commit()
        conn.close()
