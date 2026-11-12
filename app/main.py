import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from app.frontend import FRONTEND_DIR, FrontendCacheMiddleware, FrontendFiles

from app import auth_sessions, database, demo, startup
from app.device_session import DeviceSessionMiddleware as SessionMiddleware
from app.passi_auth import (
    AUTH_ENABLED,
    PASSI_SESSION_MAX_AGE_SECONDS,
    PassiAuthMiddleware,
    router as passi_auth_router,
    validate_auth_configuration,
)
from app.routers import (
    accounts as accounts_router,
    banking as banking_router,
    categories as categories_router,
    events as events_router,
    pages as pages_router,
    planning as planning_router,
    reports as reports_router,
    transactions as transactions_router,
)
from app.services import auto_sync as auto_sync_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_auth_configuration()
    await startup.create_tables()
    auto_sync_task = (
        asyncio.create_task(auto_sync_service.auto_sync_loop())
        if auto_sync_service.auto_sync_enabled() else None
    )
    demo_cleanup = asyncio.create_task(demo.store.sweep())
    try:
        yield
    finally:
        if auto_sync_task is not None:
            auto_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_sync_task
        demo_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await demo_cleanup
        await demo.store.close()
        await database.engine.dispose()


app = FastAPI(title="Swedbank Spend Analyzer", version="0.1.0", lifespan=lifespan)
app.mount("/static", FrontendFiles(directory=FRONTEND_DIR), name="static")
app.add_middleware(PassiAuthMiddleware)
if AUTH_ENABLED:
    session_secret = os.getenv("PASSI_SESSION_SECRET")
    if not session_secret:
        raise RuntimeError("PASSI_SESSION_SECRET is required when PASSI_AUTH_ENABLED is enabled")
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        https_only=True,
        same_site="lax",
        max_age=PASSI_SESSION_MAX_AGE_SECONDS,
    )
app.include_router(passi_auth_router)
app.include_router(demo.router)
app.state.auth_session_store = auth_sessions.DatabaseAuthSessionStore()
app.include_router(banking_router.router)
app.include_router(pages_router.router)
app.include_router(events_router.router)
app.include_router(accounts_router.router)
app.include_router(transactions_router.router)
app.include_router(categories_router.router)
app.include_router(planning_router.router)
app.include_router(reports_router.router)
app.add_middleware(FrontendCacheMiddleware)
