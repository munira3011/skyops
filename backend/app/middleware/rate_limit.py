import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_WINDOW_SECONDS = 60
_MAX_REQUESTS_PER_WINDOW = 30

_request_log: dict[str, list[float]] = defaultdict(list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window in-memory rate limit per client IP - single-process only, resets on
    restart; fine for this demo's scale, would need a shared store (e.g. Redis) if this ever
    ran with multiple worker processes."""

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start = now - _WINDOW_SECONDS
        timestamps = [t for t in _request_log[client_ip] if t > window_start]

        if len(timestamps) >= _MAX_REQUESTS_PER_WINDOW:
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limit_exceeded", "detail": "Too many requests - please slow down."},
            )

        timestamps.append(now)
        _request_log[client_ip] = timestamps
        return await call_next(request)
