from sqlalchemy import select, text
from sqlalchemy.schema import CreateSchema

from app import config, database, dates, schema as schema_ddl
from app.models import Category, ControlBase
from app.services import accounts as accounts_service


async def create_tables() -> None:
    legacy_schema = database.tenant_schema_name(config.legacy_owner_id)
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("control", if_not_exists=True))
        await connection.execute(CreateSchema(legacy_schema, if_not_exists=True))
        await schema_ddl._migrate_public_tables(connection, legacy_schema)
        await connection.run_sync(ControlBase.metadata.create_all)
        await connection.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    await database.ensure_tenant_schema(config.legacy_owner_id)
    async with database.tenant_session(config.legacy_owner_id) as db:
        current = dates.current_month()
        if await db.scalar(select(Category.id).where(Category.owner_id == config.legacy_owner_id).limit(1)) is None:
            db.add_all([
                Category(owner_id=config.legacy_owner_id, name=name, icon=icon, month=current)
                for name, icon in (("Еда", "🍽️"), ("Транспорт", "🚗"), ("Дом", "🏠"), ("Подписки", "📱"), ("Прочее", "🧾"))
            ])
        await accounts_service.merge_duplicate_accounts(db)
        await db.commit()
