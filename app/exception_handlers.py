"""
Custom exception handlers for beautiful error pages
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.core.static_assets import static_asset_url

# Setup templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


async def not_found_handler(request: Request, exc):
    """Custom 404 error page"""
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "message": "Page Not Found",
            "description": "The page you're looking for doesn't exist or has been moved.",
            "detail": str(exc.detail) if hasattr(exc, 'detail') else None,
            "error_bg_url": static_asset_url("images/gif.gif"),
        },
        status_code=404
    )


async def internal_error_handler(request: Request, exc):
    """Custom 500 error page"""
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "message": "Internal Server Error",
            "description": "Something went wrong on our end. We're working to fix it!",
            "detail": str(exc) if not isinstance(exc, Exception) else "Server encountered an error",
            "error_bg_url": static_asset_url("images/gif.gif"),
        },
        status_code=500
    )


async def general_exception_handler(request: Request, exc):
    """Handler for HTTPException"""
    status_code = getattr(exc, 'status_code', 500)
    detail = getattr(exc, 'detail', str(exc))
    
    messages = {
        400: ("Bad Request", "The request was invalid or cannot be served."),
        401: ("Unauthorized", "You need to be authenticated to access this resource."),
        403: ("Forbidden", "You don't have permission to access this resource."),
        404: ("Not Found", "The requested resource could not be found."),
        429: ("Too Many Requests", "You've made too many requests. Please slow down."),
        500: ("Internal Server Error", "Something went wrong on our end."),
        503: ("Service Unavailable", "The service is temporarily unavailable.")
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
        status_code=status_code
    )
