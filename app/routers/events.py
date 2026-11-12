import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import asyncpg

from app import config, demo, dependencies


router = APIRouter()


@router.get("/api/events")
async def data_events(owner_id: str = Depends(dependencies.current_owner_id)) -> StreamingResponse:
    if owner_id.startswith(demo.PREFIX):
        async def demo_stream():
            revision = -1
            ticks = 0
            while True:
                try:
                    session = demo.store.get(owner_id)
                except HTTPException:
                    yield 'event: expired\ndata: {}\n\n'
                    return
                if session.revision != revision:
                    revision = session.revision
                    yield 'event: changed\ndata: {}\n\n'
                elif ticks % 15 == 0:
                    yield ': keepalive\n\n'
                ticks += 1
                await asyncio.sleep(1)
        return StreamingResponse(demo_stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })

    async def stream():
        connection = await asyncpg.connect(config.database_url.replace("postgresql+asyncpg://", "postgresql://"))
        changed = asyncio.Event()

        def notify(_connection, _pid, _channel, payload):
            if payload == owner_id:
                changed.set()

        def terminated(*_args):
            changed.set()

        try:
            await connection.add_listener("data_changed", notify)
            connection.add_termination_listener(terminated)
            # Subscribe before the snapshot, also on reconnect, to avoid gaps.
            yield 'event: changed\ndata: {}\n\n'
            while not connection.is_closed():
                try:
                    await asyncio.wait_for(changed.wait(), timeout=15)
                    changed.clear()
                    yield 'event: changed\ndata: {}\n\n'
                except TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            connection.terminate()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
