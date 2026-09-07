import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.gateway.client import GatewayError

logger = logging.getLogger("skyops.errors")


def register_error_handlers(app: FastAPI) -> None:
    """Registers global handlers so an unhandled error returns a safe JSON body instead of a
    raw traceback. Every graph node already catches `GatewayError` internally with a keyword
    fallback, so this handler is defense-in-depth, not the primary path."""

    @app.exception_handler(GatewayError)
    async def _gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        logger.error("gateway error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=502, content={"error": "gateway_unavailable", "detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "Something went wrong - please try again."},
        )
