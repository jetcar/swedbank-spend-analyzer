from contextlib import asynccontextmanager
from hashlib import sha256
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema

from app import config, demo, schema as schema_ddl
from app.models import Base, TENANT_SCHEMA


engine = create_async_engine(config.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
control_session_factory = session_factory
_ready_tenant_schemas: set[str] = set()
_tenant_schema_lock = asyncio.Lock()


def tenant_schema_name(owner_id: str) -> str:
    normalized = owner_id.strip().lower()
    if not normalized:
        raise ValueError("Tenant owner id is required")
    return f"tenant_{sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def tenant_session_factory(owner_id: str, *, repeatable_read: bool = False):
    schema = tenant_schema_name(owner_id)
    options = {"schema_translate_map": {TENANT_SCHEMA: schema}}
    if repeatable_read:
        options["isolation_level"] = "REPEATABLE READ"
    tenant_engine = engine.execution_options(**options)
    return async_sessionmaker(tenant_engine, expire_on_commit=False)


@asynccontextmanager
async def tenant_session(owner_id: str, *, repeatable_read: bool = False):
    if owner_id.startswith(demo.PREFIX):
        async with demo.store.database(owner_id) as db:
            yield db
        return
    await ensure_tenant_schema(owner_id)
    schema = tenant_schema_name(owner_id)
    factory = tenant_session_factory(owner_id, repeatable_read=True) if repeatable_read else tenant_session_factory(owner_id)
    async with factory() as db:
        await db.execute(
            text("SELECT set_config('search_path', :path, true)"),
            {"path": f'"{schema}", pg_catalog'},
        )
        yield db


async def ensure_tenant_schema(owner_id: str) -> None:
    schema = tenant_schema_name(owner_id)
    if schema in _ready_tenant_schemas:
        return
    async with _tenant_schema_lock:
        if schema in _ready_tenant_schemas:
            return
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:schema))"), {"schema": schema})
            await connection.execute(CreateSchema(schema, if_not_exists=True))
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection.execution_options(schema_translate_map={TENANT_SCHEMA: schema})
                )
            )
            await schema_ddl._upgrade_tenant_schema(connection, schema, owner_id)
        _ready_tenant_schemas.add(schema)
