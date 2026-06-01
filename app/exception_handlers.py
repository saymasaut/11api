"""
Custom exception handlers — HTML for browsers, JSON for /api/* routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.static_assets import static_asset_url
from app.services.downloader_exceptions import normalize_http_detail

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


def _wants_json(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept


def _json_error(status_code: int, detail: Any) -> JSONResponse:
    body = normalize_http_detail(detail)
    body["status_code"] = status_code
    return JSONResponse(status_code=status_code, content=body)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if _wants_json(request):
        return _json_error(422, exc.errors())
    return _json_error(422, exc.errors())


async def not_found_handler(request: Request, exc):
    detail = getattr(exc, "detail", "Not Found")
    if _wants_json(request):
        return _json_error(
            404,
            {"error_code": "NOT_FOUND", "message": str(detail)},
        )
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "message": "Page Not Found",
            "description": "The page you're looking for doesn't exist or has been moved.",
            "detail": str(detail) if detail else None,
            "error_bg_url": static_asset_url("images/gif.gif"),
        },
        status_code=404,
    )


async def internal_error_handler(request: Request, exc):
    if _wants_json(request):
        return _json_error(
            500,
            {"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "message": "Internal Server Error",
            "description": "Something went wrong on our end. We're working to fix it!",
            "detail": "Server encountered an error",
            "error_bg_url": static_asset_url("images/gif.gif"),
        },
        status_code=500,
    )


async def general_exception_handler(request: Request, exc):
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", str(exc))

    if _wants_json(request):
        return _json_error(status_code, detail)

    messages = {
        400: ("Bad Request", "The request was invalid or cannot be served."),
        401: ("Unauthorized", "You need to be authenticated to access this resource."),
        403: ("Forbidden", "You don't have permission to access this resource."),
        404: ("Not Found", "The requested resource could not be found."),
        422: ("Unprocessable Entity", "The request could not be processed."),
        429: ("Too Many Requests", "You've made too many requests. Please slow down."),
        500: ("Internal Server Error", "Something went wrong on our end."),
        503: ("Service Unavailable", "The service is temporarily unavailable."),
    }

    message, description = messages.get(status_code, ("Error", "An error occurred"))

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": status_code,
            "message": message,
            "description": description,
            "detail": detail,
            "error_bg_url": static_asset_url("images/gif.gif"),
        },
        status_code=status_code,
    )
