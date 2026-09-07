import secrets
import time
from typing import Optional

# In-memory only - resets on restart, single-process. Matches this project's existing choice
# (InMemorySaver checkpointer, in-memory rate limiter) to avoid external infra for a demo.
_SESSION_TTL_SECONDS = 8 * 60 * 60
_sessions: dict[str, dict] = {}


def create_session(username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"username": username, "role": role, "expires_at": time.time() + _SESSION_TTL_SECONDS}
    return token


def get_session(token: str) -> Optional[dict]:
    session = _sessions.get(token)
    if session is None:
        return None
    if session["expires_at"] < time.time():
        del _sessions[token]
        return None
    return session


def delete_session(token: str) -> None:
    _sessions.pop(token, None)
