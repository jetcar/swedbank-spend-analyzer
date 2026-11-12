from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from app import database
from app.models import AuthSession


class DatabaseAuthSessionStore:
    async def create(self, sid: str, owner_id: str, expires_at: int) -> None:
        async with database.control_session_factory() as db:
            await db.merge(AuthSession(sid=sid, owner_id=owner_id, expires_at=expires_at, revoked=False))
            await db.execute(delete(AuthSession).where(AuthSession.expires_at < int(datetime.now(timezone.utc).timestamp())))
            await db.commit()

    async def is_active(self, sid: str, owner_id: str, now: int) -> bool:
        async with database.control_session_factory() as db:
            return bool(
                await db.scalar(
                    select(AuthSession.sid).where(
                        AuthSession.sid == sid,
                        AuthSession.owner_id == owner_id,
                        AuthSession.revoked.is_(False),
                        AuthSession.expires_at > now,
                    )
                )
            )

    async def revoke(self, sid: str) -> None:
        async with database.control_session_factory() as db:
            await db.execute(update(AuthSession).where(AuthSession.sid == sid).values(revoked=True))
            await db.commit()
