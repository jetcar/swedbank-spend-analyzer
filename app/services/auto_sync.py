from datetime import datetime, timedelta, timezone
import asyncio
import logging
import os

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from app import database
from app.services import sync_state
from app.models import BankSyncState, BankSession, TENANT_SCHEMA
from app.services import bank_sync as bank_sync_service


logger = logging.getLogger(__name__)
AUTO_SYNC_MIN_INTERVAL_SECONDS = 6 * 60 * 60
AUTO_SYNC_POLL_SECONDS = 60


def auto_sync_enabled() -> bool:
    return os.getenv("AUTO_SYNC_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def auto_sync_interval_seconds() -> int:
    try:
        configured = int(os.getenv("AUTO_SYNC_INTERVAL_SECONDS", str(AUTO_SYNC_MIN_INTERVAL_SECONDS)))
    except ValueError:
        configured = AUTO_SYNC_MIN_INTERVAL_SECONDS
    return max(configured, AUTO_SYNC_MIN_INTERVAL_SECONDS)


async def discover_bank_owners() -> list[str]:
    async with database.engine.connect() as connection:
        schemas = list(
            (
                await connection.execute(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname ~ '^tenant_[0-9a-f]{24}$' ORDER BY nspname"
                    )
                )
            ).scalars()
        )
        owners: set[str] = set()
        for schema in schemas:
            rows = (
                await connection.execute(
                    select(BankSession.owner_id).distinct(),
                    execution_options={"schema_translate_map": {TENANT_SCHEMA: schema}},
                )
            ).scalars()
            for owner_id in rows:
                normalized = str(owner_id).strip().lower()
                if normalized and database.tenant_schema_name(normalized) == schema:
                    owners.add(normalized)
    return sorted(owners)


async def claim_auto_sync(owner_id: str, now: datetime | None = None) -> bool:
    attempted_at = now or datetime.now(timezone.utc)
    async with database.control_session_factory() as db:
        await db.execute(
            postgresql_insert(BankSyncState)
            .values(owner_id=owner_id)
            .on_conflict_do_nothing(index_elements=[BankSyncState.owner_id])
        )
        state = await db.scalar(
            select(BankSyncState).where(BankSyncState.owner_id == owner_id).with_for_update()
        )
        if state is None:
            raise RuntimeError("Could not initialize automatic sync state")
        if state.last_attempt_at is not None:
            last_attempt = state.last_attempt_at
            if last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            if attempted_at - last_attempt < timedelta(seconds=auto_sync_interval_seconds()):
                await db.commit()
                return False
        state.last_attempt_at = attempted_at
        await db.commit()
    return True


async def run_due_auto_sync() -> dict[str, int]:
    owners = await discover_bank_owners()
    synced = 0
    failed = 0
    for owner_id in owners:
        if not await claim_auto_sync(owner_id):
            continue
        try:
            await bank_sync_service.sync_owner_transactions(owner_id)
        except Exception as error:
            failed += 1
            logger.exception("Automatic bank sync failed for owner %s", owner_id)
            await sync_state.record_auto_sync_result(owner_id, error=str(error) or type(error).__name__)
        else:
            synced += 1
            await sync_state.record_auto_sync_result(owner_id, error=None)
    return {"owners": len(owners), "synced": synced, "failed": failed}


async def auto_sync_loop() -> None:
    while True:
        try:
            await run_due_auto_sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automatic bank sync cycle failed")
        await asyncio.sleep(AUTO_SYNC_POLL_SECONDS)
