import os
import unittest
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateSchema, DropSchema

from app import database, schema as schema_ddl
from app.models import Category


class TenantDDLTest(unittest.TestCase):
    def test_schema_names_are_restricted_to_generated_tenant_identifiers(self):
        for schema in ('public', 'tenant_short', 'tenant_' + 'a' * 24 + '"; SELECT 1; --'):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                schema_ddl._tenant_ddl('DROP SCHEMA %(schema)s', schema)

    def test_ddl_context_quotes_identifiers_and_does_not_reformat_percent_signs(self):
        statement = schema_ddl._tenant_ddl(
            'COMMENT ON TABLE %(schema)s.%(table)s IS %(owner)s',
            'tenant_' + 'a' * 24, table='a"b', owner_id="o'hara %s %(schema)s",
        )
        sql = str(statement.compile(dialect=postgresql.dialect(paramstyle='named')))
        self.assertIn('"a""b"', sql)
        self.assertIn("'o''hara %s %(schema)s'", sql)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'A disposable PostgreSQL database is required')
class TenantSQLIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await database.engine.dispose()

    async def test_category_customization_column_upgrade_preserves_existing_rows(self):
        owner = f"upgrade-category-{uuid4()}@example.invalid"
        schema = database.tenant_schema_name(owner)
        try:
            async with database.tenant_session(owner) as db:
                db.add(Category(owner_id=owner, name="Existing", month="2098-06", budget=42))
                await db.commit()
            async with database.engine.begin() as connection:
                await connection.execute(schema_ddl._tenant_ddl(
                    "ALTER TABLE %(schema)s.categories DROP COLUMN name_customized", schema,
                ))
                await schema_ddl._upgrade_tenant_schema(connection, schema, owner)
            async with database.tenant_session(owner) as db:
                category = await db.scalar(select(Category))
                self.assertEqual((category.name, float(category.budget), category.name_customized), ("Existing", 42, False))
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            database._ready_tenant_schemas.discard(schema)

    async def test_owner_punctuation_stays_data_in_trigger_arguments_and_queries(self):
        owner = f"o'hara%; -- \\ $$ %(schema)s {uuid4()}@example.invalid" + r" \' \n \x41 \u0041" + "\nÕ"
        schema = database.tenant_schema_name(owner)
        try:
            async with database.tenant_session(owner) as db:
                db.add(Category(owner_id=owner, name='Quoted owner', month='2098-06'))
                await db.commit()
            async with database.engine.begin() as connection:
                for standard_strings in ('on', 'off'):
                    with self.subTest(standard_conforming_strings=standard_strings):
                        await connection.execute(text(
                            "SELECT set_config('standard_conforming_strings', :value, true)"
                        ), {'value': standard_strings})
                        await schema_ddl._upgrade_tenant_schema(connection, schema, owner)
                        arguments = await connection.scalar(text('''
                            SELECT t.tgargs FROM pg_trigger t
                            JOIN pg_class c ON c.oid = t.tgrelid
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = :schema AND c.relname = 'categories'
                            AND t.tgname = 'data_changed'
                        '''), {'schema': schema})
                        self.assertEqual(arguments, owner.encode() + b'\x00')
            async with database.tenant_session(owner) as db:
                self.assertEqual(await db.scalar(select(Category.owner_id)), owner)
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            database._ready_tenant_schemas.discard(schema)

    async def test_legacy_table_migration_preserves_rows_and_rejects_duplicates(self):
        schema = database.tenant_schema_name(str(uuid4()))
        async with database.engine.connect() as connection:
            transaction = await connection.begin()
            try:
                self.assertFalse(await schema_ddl._public_table_exists(connection, 'bank_sessions'))
                await connection.execute(CreateSchema(schema))
                await connection.execute(text('CREATE TABLE public.bank_sessions (id integer)'))
                await connection.execute(text('INSERT INTO public.bank_sessions VALUES (7)'))
                await schema_ddl._migrate_public_tables(connection, schema)
                self.assertFalse(await schema_ddl._public_table_exists(connection, 'bank_sessions'))
                from sqlalchemy import column, table
                migrated = table('bank_sessions', column('id'), schema=schema)
                self.assertEqual(await connection.scalar(select(migrated.c.id)), 7)
                await connection.execute(text('CREATE TABLE public.bank_sessions (id integer)'))
                with self.assertRaisesRegex(RuntimeError, 'Unsafe duplicate tables'):
                    await schema_ddl._migrate_public_tables(connection, schema)
            finally:
                await transaction.rollback()
