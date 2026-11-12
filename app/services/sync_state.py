from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from app import database, demo
from app.models import BankSyncState


async def record_auto_sync_result(owner_id: str, *, error: str | None) -> None:
    completed_at = datetime.now(timezone.utc)
    if owner_id.startswith(demo.PREFIX):
        session = demo.store.get(owner_id)
        if error is None:
            session.last_success_at = completed_at
            session.revision += 1
        return
    async with database.control_session_factory() as db:
        await db.execute(
            postgresql_insert(BankSyncState)
            .values(owner_id=owner_id)
            .on_conflict_do_nothing(index_elements=[BankSyncState.owner_id])
        )
        values: dict[str, Any] = {"last_error": error[:2000] if error else None}
        if error is None:
            values["last_success_at"] = completed_at
        await db.execute(
            update(BankSyncState).where(BankSyncState.owner_id == owner_id).values(**values)
        )
        if error is None:
            await db.execute(text("SELECT pg_notify('data_changed', :owner)"), {"owner": owner_id})
        await db.commit()


async def get_sync_status(owner_id: str) -> dict[str, str | None]:
    if owner_id.startswith(demo.PREFIX):
        last_success = demo.store.get(owner_id).last_success_at
    else:
        async with database.control_session_factory() as db:
            last_success = await db.scalar(
                select(BankSyncState.last_success_at).where(BankSyncState.owner_id == owner_id)
            )
    return {"last_success_at": last_success.isoformat() if last_success else None}
