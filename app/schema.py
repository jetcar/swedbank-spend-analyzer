import re

from sqlalchemy import DDL, column, table, text, update
from sqlalchemy.dialects import postgresql


def _tenant_ddl(statement: str, schema: str, *, owner_id: str | None = None, **identifiers: str) -> DDL:
    """Render static DDL templates with validated schemas and SQL-quoted values.

    PostgreSQL DDL cannot bind identifiers or trigger arguments. Keep those
    separate from the template, quoting identifiers with the PostgreSQL compiler
    and values as explicit escape strings independent of connection settings.
    """
    if not re.fullmatch(r"tenant_[0-9a-f]{24}", schema):
        raise ValueError("Invalid tenant schema")
    dialect = postgresql.dialect(paramstyle="named")
    quote = dialect.identifier_preparer.quote_identifier
    context = {name: quote(value) for name, value in identifiers.items()}
    context["schema"] = quote(schema)
    if owner_id is not None:
        # An unconnected dialect cannot know standard_conforming_strings. E''
        # gives backslashes the same meaning with either server setting.
        escaped_owner = owner_id.replace("\\", "\\\\").replace("'", "''")
        context["owner"] = "E'" + escaped_owner + "'"
    return DDL(statement, context=context)


async def _public_table_exists(connection, table_name: str) -> bool:
    return bool(await connection.scalar(text("SELECT to_regclass(:name)"), {"name": f"public.{table_name}"}))


async def _migrate_public_tables(connection, tenant_schema: str) -> None:
    for table_name in ("auth_sessions", "pending_bank_authorizations"):
        if await _public_table_exists(connection, table_name):
            target_exists = await connection.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"control.{table_name}"}
            )
            if target_exists:
                raise RuntimeError(f"Unsafe duplicate tables: public.{table_name} and control.{table_name}")
            await connection.execute(_tenant_ddl(
                "ALTER TABLE public.%(table)s SET SCHEMA control", tenant_schema, table=table_name,
            ))
    for table_name in ("bank_sessions", "accounts", "categories", "transactions", "expense_splits"):
        if await _public_table_exists(connection, table_name):
            target_exists = await connection.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"{tenant_schema}.{table_name}"}
            )
            if target_exists:
                raise RuntimeError(f"Unsafe duplicate tables: public.{table_name} and {tenant_schema}.{table_name}")
            await connection.execute(_tenant_ddl(
                "ALTER TABLE public.%(table)s SET SCHEMA %(schema)s", tenant_schema, table=table_name,
            ))


async def _upgrade_tenant_schema(connection, schema: str, owner_id: str) -> None:
    for table_name in ("bank_sessions", "categories"):
        await connection.execute(_tenant_ddl(
            "ALTER TABLE %(schema)s.%(table)s ADD COLUMN IF NOT EXISTS owner_id VARCHAR(320)",
            schema, table=table_name,
        ))
        target = table(table_name, column("owner_id"), schema=schema)
        await connection.execute(update(target).where(target.c.owner_id.is_(None)).values(owner_id=owner_id))
        await connection.execute(_tenant_ddl(
            "ALTER TABLE %(schema)s.%(table)s ALTER COLUMN owner_id SET NOT NULL", schema, table=table_name,
        ))
        await connection.execute(_tenant_ddl(
            "CREATE INDEX IF NOT EXISTS %(index)s ON %(schema)s.%(table)s (owner_id)",
            schema, table=table_name, index=f"ix_{table_name}_owner_id",
        ))
    for statement in (
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS month VARCHAR(7)",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS budget NUMERIC(18, 2) NOT NULL DEFAULT 0",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS name_customized BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'EUR'",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS forecast_mode VARCHAR(10) NOT NULL DEFAULT 'budget'",
        "ALTER TABLE %(schema)s.categories ADD COLUMN IF NOT EXISTS payment_status VARCHAR(10) NOT NULL DEFAULT 'auto'",
        "ALTER TABLE %(schema)s.accounts ADD COLUMN IF NOT EXISTS monitored BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE %(schema)s.accounts ADD COLUMN IF NOT EXISTS balance_adjustment NUMERIC(24, 2) NOT NULL DEFAULT 0",
        "ALTER TABLE %(schema)s.transactions ADD COLUMN IF NOT EXISTS value_date TIMESTAMPTZ",
        "ALTER TABLE %(schema)s.transactions ADD COLUMN IF NOT EXISTS category_id INTEGER",
        "ALTER TABLE %(schema)s.transactions ADD COLUMN IF NOT EXISTS excluded_from_statistics BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE %(schema)s.categories DROP CONSTRAINT IF EXISTS categories_name_key",
        "ALTER TABLE %(schema)s.categories DROP CONSTRAINT IF EXISTS uq_category_name_month",
        "DROP INDEX IF EXISTS %(schema)s.uq_category_name_month",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_category_owner_name_month ON %(schema)s.categories (owner_id, name, month)",
    ):
        await connection.execute(_tenant_ddl(statement, schema))
    await connection.execute(_tenant_ddl("""
        CREATE OR REPLACE FUNCTION %(schema)s.enforce_transaction_category_owner() RETURNS trigger AS $$
        DECLARE transaction_owner VARCHAR(320); category_owner VARCHAR(320);
        BEGIN
            IF NEW.category_id IS NULL THEN RETURN NEW; END IF;
            SELECT bs.owner_id INTO transaction_owner FROM %(schema)s.accounts a
            JOIN %(schema)s.bank_sessions bs ON bs.id = a.session_id WHERE a.id = NEW.account_id;
            SELECT owner_id INTO category_owner FROM %(schema)s.categories WHERE id = NEW.category_id;
            IF transaction_owner IS DISTINCT FROM category_owner THEN
                RAISE EXCEPTION 'Transaction and category owners must match';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog
    """, schema))
    await connection.execute(_tenant_ddl("DROP TRIGGER IF EXISTS enforce_category_owner ON %(schema)s.transactions", schema))
    await connection.execute(_tenant_ddl("""
        CREATE TRIGGER enforce_category_owner BEFORE INSERT OR UPDATE OF account_id, category_id
        ON %(schema)s.transactions FOR EACH ROW EXECUTE FUNCTION %(schema)s.enforce_transaction_category_owner()
    """, schema))
    await connection.execute(_tenant_ddl("""
        CREATE OR REPLACE FUNCTION %(schema)s.enforce_split_category_owner() RETURNS trigger AS $$
        DECLARE transaction_owner VARCHAR(320); category_owner VARCHAR(320);
        BEGIN
            SELECT bs.owner_id INTO transaction_owner FROM %(schema)s.transactions t
            JOIN %(schema)s.accounts a ON a.id = t.account_id
            JOIN %(schema)s.bank_sessions bs ON bs.id = a.session_id WHERE t.id = NEW.transaction_id;
            SELECT owner_id INTO category_owner FROM %(schema)s.categories WHERE id = NEW.category_id;
            IF transaction_owner IS DISTINCT FROM category_owner THEN
                RAISE EXCEPTION 'Expense split and category owners must match';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog
    """, schema))
    await connection.execute(_tenant_ddl("DROP TRIGGER IF EXISTS enforce_category_owner ON %(schema)s.expense_splits", schema))
    await connection.execute(_tenant_ddl("""
        CREATE TRIGGER enforce_category_owner BEFORE INSERT OR UPDATE OF transaction_id, category_id
        ON %(schema)s.expense_splits FOR EACH ROW EXECUTE FUNCTION %(schema)s.enforce_split_category_owner()
    """, schema))
    await connection.execute(_tenant_ddl("""
        CREATE OR REPLACE FUNCTION %(schema)s.enforce_plan_transaction_owner() RETURNS trigger AS $$
        DECLARE transaction_owner VARCHAR(320); item_owner VARCHAR(320);
        BEGIN
            SELECT bs.owner_id INTO transaction_owner FROM %(schema)s.transactions t
            JOIN %(schema)s.accounts a ON a.id = t.account_id
            JOIN %(schema)s.bank_sessions bs ON bs.id = a.session_id WHERE t.id = NEW.transaction_id;
            SELECT owner_id INTO item_owner FROM %(schema)s.plan_items WHERE id = NEW.item_id;
            IF transaction_owner IS DISTINCT FROM item_owner THEN
                RAISE EXCEPTION 'Plan item and transaction owners must match';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog
    """, schema))
    await connection.execute(_tenant_ddl("DROP TRIGGER IF EXISTS enforce_plan_owner ON %(schema)s.plan_item_transactions", schema))
    await connection.execute(_tenant_ddl("""
        CREATE TRIGGER enforce_plan_owner BEFORE INSERT OR UPDATE OF transaction_id, item_id
        ON %(schema)s.plan_item_transactions FOR EACH ROW EXECUTE FUNCTION %(schema)s.enforce_plan_transaction_owner()
    """, schema))
    await connection.execute(_tenant_ddl("""
        CREATE OR REPLACE FUNCTION %(schema)s.notify_data_changed() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify('data_changed', TG_ARGV[0]);
            RETURN NULL;
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog
    """, schema))
    for table_name in ("accounts", "transactions", "categories", "expense_splits", "plan_items", "plan_item_transactions"):
        await connection.execute(_tenant_ddl("DROP TRIGGER IF EXISTS data_changed ON %(schema)s.%(table)s", schema, table=table_name))
        await connection.execute(_tenant_ddl("""
            CREATE TRIGGER data_changed AFTER INSERT OR UPDATE OR DELETE ON %(schema)s.%(table)s
            FOR EACH ROW EXECUTE FUNCTION %(schema)s.notify_data_changed(%(owner)s)
        """, schema, table=table_name, owner_id=owner_id))
