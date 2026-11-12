from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import text

from app import database
from app.frontend import FRONTEND_DIR, frontend_page


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    async with database.engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.get("/")
async def frontend() -> FileResponse:
    return frontend_page("index.html")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_notice() -> str:
    return (FRONTEND_DIR / "privacy.html").read_text(encoding="utf-8")


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service() -> str:
    return (FRONTEND_DIR / "terms.html").read_text(encoding="utf-8")
