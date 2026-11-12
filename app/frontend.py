"""Serve the built frontend, with an uncached HTML entry point."""
import json
from pathlib import Path
import re

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware


APP_DIR = Path(__file__).resolve().parent
BUILD_DIR = APP_DIR.parent / "frontend-dist"
# Direct Python development can use sources; Docker always includes the build.
FRONTEND_DIR = BUILD_DIR if (BUILD_DIR / "manifest.json").is_file() else APP_DIR / "static"
MANIFEST = json.loads((FRONTEND_DIR / "manifest.json").read_text()) if FRONTEND_DIR == BUILD_DIR else {}
IMMUTABLE = "public, max-age=31536000, immutable"
HASHED_ASSET = re.compile(r"^/static/assets/[^/]+-[A-Za-z0-9_-]{8,}\.(?:js|css|svg)$")


def asset_url(name: str) -> str:
    return MANIFEST.get(name, f"/static/{name}")


def frontend_page(name: str) -> FileResponse:
    return FileResponse(FRONTEND_DIR / name, headers={"Cache-Control": "no-store"})


class FrontendFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers):
        # HTML must be sent fresh, even when a client supplies old validators.
        if response_headers.get("content-type", "").startswith("text/html"):
            return False
        return super().is_not_modified(response_headers, request_headers)


class FrontendCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
            for header in ("etag", "last-modified"):
                if header in response.headers:
                    del response.headers[header]
        elif HASHED_ASSET.fullmatch(request.url.path) and response.status_code in {200, 304}:
            response.headers["Cache-Control"] = IMMUTABLE
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response
