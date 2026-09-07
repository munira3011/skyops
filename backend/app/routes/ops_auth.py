from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.db import verify_staff_login
from app.sessions import create_session, delete_session

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    """Verifies username/password against the staff_users SQLite table (see app/db.py) and
    issues a session token - not gated by X-API-Key, since staff prove identity with their own
    credentials rather than a shared client key. Rate-limited like every other route."""
    user = verify_staff_login(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = create_session(user["username"], user["role"])
    return LoginResponse(token=token, username=user["username"], role=user["role"])


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)) -> dict:
    if authorization and authorization.lower().startswith("bearer "):
        delete_session(authorization[len("bearer ") :])
    return {"status": "logged_out"}
