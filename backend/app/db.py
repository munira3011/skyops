import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# Sibling to chroma_db/ - a local, runtime SQLite file, not committed fixture data (gitignored).
_DB_PATH = Path(__file__).resolve().parents[1] / "staff.db"
_PBKDF2_ITERATIONS = 260_000  # OWASP-recommended minimum for PBKDF2-HMAC-SHA256 as of 2023


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'staff',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_approvals (
                thread_id TEXT PRIMARY KEY,
                pnr TEXT NOT NULL,
                flight_number TEXT NOT NULL,
                compensation_usd INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


_init_db()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()


def create_staff_user(username: str, password: str, role: str = "staff") -> None:
    """Raises sqlite3.IntegrityError if `username` already exists."""
    salt = secrets.token_bytes(16)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO staff_users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, _hash_password(password, salt), salt.hex(), role, datetime.now(timezone.utc).isoformat()),
        )


def verify_staff_login(username: str, password: str) -> Optional[dict]:
    """Returns {"username", "role"} on success, None on unknown user or wrong password."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT password_hash, salt, role FROM staff_users WHERE username = ?", (username,)
        ).fetchone()
    if row is None:
        return None
    expected = _hash_password(password, bytes.fromhex(row["salt"]))
    if not secrets.compare_digest(expected, row["password_hash"]):
        return None
    return {"username": username, "role": row["role"]}


def record_pending_approval(thread_id: str, pending_approval: dict) -> None:
    """Upserts one row - a thread only ever has one outstanding pending_approval at a time."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pending_approvals
                (thread_id, pnr, flight_number, compensation_usd, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                pending_approval["pnr"],
                pending_approval["flight_number"],
                pending_approval["compensation_usd"],
                pending_approval["reason"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def resolve_pending_approval(thread_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM pending_approvals WHERE thread_id = ?", (thread_id,))


def list_pending_approvals() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM pending_approvals ORDER BY created_at ASC").fetchall()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: uv run python -m app.db <username> <password>")
        raise SystemExit(1)

    create_staff_user(sys.argv[1], sys.argv[2])
    print(f"Created staff user '{sys.argv[1]}' with role 'staff'.")
