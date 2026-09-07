import logging

from fastapi import FastAPI

from app.middleware.auth import AuthMiddleware
from app.middleware.error_handler import register_error_handlers
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.routes import chat, ops, ops_auth

# Without a configured handler, Python's logging module silently drops INFO-level records (its
# "handler of last resort" only surfaces WARNING+) - without this, middleware/logging.py and
# middleware/error_handler.py's logger calls would produce no visible output at all.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="SkyOps", description="Zenith Air multi-agent assistant API")

# Starlette executes middleware in the reverse of the order added (last added = outermost), so
# Logging is added last to see every request/response, including ones Auth/RateLimit reject.
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LoggingMiddleware)

register_error_handlers(app)

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(ops.router, prefix="/ops", tags=["ops"])
app.include_router(ops_auth.router, prefix="/ops/auth", tags=["ops-auth"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
