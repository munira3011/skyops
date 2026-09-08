import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.sessions import get_session

_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/ops/auth/login", "/ops/auth/logout"}


def _session_role(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    session = get_session(auth_header[len("bearer ") :])
    return session["role"] if session is not None else None


def _has_ops_access(request: Request) -> bool:
    """staff or admin - approvals are ordinary staff work."""
    ops_key = os.environ.get("SKYOPS_OPS_API_KEY")
    if ops_key and request.headers.get("x-ops-api-key") == ops_key:
        return True
    return _session_role(request) in ("staff", "admin")


def _has_admin_access(request: Request) -> bool:
    """admin only - eval results expose model/prompt internals, not something every staff
    account should see just by having approval rights."""
    ops_key = os.environ.get("SKYOPS_OPS_API_KEY")
    if ops_key and request.headers.get("x-ops-api-key") == ops_key:
        return True
    return _session_role(request) == "admin"


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires `X-API-Key` matching SKYOPS_API_KEY for every route except health/docs/staff
    login+logout. `/ops/*` (other than the login/logout endpoints themselves) additionally
    requires either `X-Ops-API-Key` matching SKYOPS_OPS_API_KEY (service-to-service) or a valid
    staff/admin session bearer token from POST /ops/auth/login (interactive use) - either grants
    access, so a leaked customer-chat key alone still can't reach approvals. `/ops/evals/*` is
    stricter still - admin role (or the ops API key) only, checked before the general `/ops`
    prefix since it's a more specific path."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        api_key = os.environ.get("SKYOPS_API_KEY")
        if not api_key or request.headers.get("x-api-key") != api_key:
            return JSONResponse(
                status_code=401, content={"error": "unauthorized", "detail": "Missing or invalid X-API-Key."}
            )

        if request.url.path.startswith("/ops/evals") and not _has_admin_access(request):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Eval results require an admin account."},
            )
        if request.url.path.startswith("/ops") and not _has_ops_access(request):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "detail": "Missing or invalid ops credentials."},
            )

        return await call_next(request)
