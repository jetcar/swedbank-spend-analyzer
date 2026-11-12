"""Per-browser sandbox state. No files, PostgreSQL tables or auth records."""
import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .frontend import frontend_page
from .models import Base, TENANT_SCHEMA


COOKIE = "bank_demo"
PREFIX = "demo:"
router = APIRouter(prefix="/auth/demo")


def now():
    return datetime.now(timezone.utc)


@dataclass
class DemoSession:
    expires_at: datetime
    last_success_at: datetime | None = None
    engine: AsyncEngine | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict = field(default_factory=dict)
    revision: int = 0


class DemoStore:
    def __init__(self):
        self.sessions: dict[str, DemoSession] = {}

    def get(self, owner_id: str) -> DemoSession:
        session = self.sessions.get(owner_id)
        if session is None or session.expires_at <= now():
            raise HTTPException(401, "Demo expired; start a new demo")
        return session

    async def create(self) -> str:
        await self.cleanup()
        if len(self.sessions) >= 100:
            raise HTTPException(503, "Demo is busy; please try again later")
        owner_id = PREFIX + secrets.token_urlsafe(32)
        tomorrow = (now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.sessions[owner_id] = DemoSession(expires_at=tomorrow)
        return owner_id

    async def discard(self, owner_id: str):
        session = self.sessions.pop(owner_id, None)
        if session:
            async with session.lock:
                if session.engine:
                    await session.engine.dispose()

    async def cleanup(self):
        for owner_id, session in list(self.sessions.items()):
            if session.expires_at <= now():
                await self.discard(owner_id)

    async def sweep(self):
        while True:
            await asyncio.sleep(60)
            await self.cleanup()

    async def close(self):
        for owner_id in list(self.sessions):
            await self.discard(owner_id)

    @asynccontextmanager
    async def database(self, owner_id: str):
        session = self.get(owner_id)
        # A memory database has one connection: serialize complete transactions.
        async with session.lock:
            self.get(owner_id)
            if session.engine is None:
                engine = create_async_engine(
                    "sqlite+aiosqlite:///:memory:",
                    execution_options={"schema_translate_map": {TENANT_SCHEMA: None}},
                )

                @event.listens_for(engine.sync_engine, "connect")
                def configure(connection, _record):
                    cursor = connection.cursor()
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.close()

                try:
                    async with engine.begin() as connection:
                        await connection.run_sync(Base.metadata.create_all)
                except BaseException:
                    await engine.dispose()
                    raise
                session.engine = engine
            async with async_sessionmaker(session.engine, expire_on_commit=False)() as db:
                @event.listens_for(db.sync_session, "after_commit")
                def changed(_session):
                    session.revision += 1

                yield db


store = DemoStore()


def owner_from_request(request: Request) -> str:
    token = request.cookies.get(COOKIE, "")
    return PREFIX + token if token else ""


@router.get("")
async def demo_entry():
    return frontend_page("login.html")


@router.post("/start")
async def start(request: Request):
    await store.discard(owner_from_request(request))
    owner_id = await store.create()
    response = RedirectResponse("/", status_code=303)
    # Session cookie intentionally outlives the daily server-side expiration:
    # expired demo requests must never fall back to a real user's data.
    response.set_cookie(COOKIE, owner_id[len(PREFIX):], httponly=True,
                        secure=request.url.scheme == "https" or os.getenv("PASSI_AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"},
                        samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/exit")
async def exit_demo(request: Request):
    await store.discard(owner_from_request(request))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE)
    return response
