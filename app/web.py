"""Small same-origin demo UI; all data endpoints retain existing authentication."""

from pathlib import Path

from fastapi import APIRouter
from starlette.responses import FileResponse

router = APIRouter(include_in_schema=False)
ASSETS = Path(__file__).with_suffix("")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


@router.get("/")
async def home():
    return FileResponse(ASSETS / "index.html", headers=HEADERS)


@router.get("/ui/app.js")
async def javascript():
    return FileResponse(ASSETS / "app.js", media_type="text/javascript", headers=HEADERS)


@router.get("/ui/style.css")
async def stylesheet():
    return FileResponse(ASSETS / "style.css", media_type="text/css", headers=HEADERS)
