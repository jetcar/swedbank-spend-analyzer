from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import unittest

from sqlalchemy import select, text

from app import bank_client, database, dates
from app.models import Account, BankSession, Category, ExpenseSplit, PlanItem, PlanItemTransaction, Transaction
from app.services import accounts as accounts_service, bank_sync, planning


def card_transaction(remittance, value_date, *, status="PDNG"):
    return {
        "entry_reference": None,
        "transaction_id": None,
        "status": status,
        "booking_date": None,
        "transaction_date": None,
        "value_date": value_date,
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": "55.14", "currency": "EUR"},
        "remittance_information": [remittance],
    }


FIRST = card_transaction(
    "OST 547025******6630 06.11.26 19:25 55.14 EUR (076214) RAPINA KONSUM RAPINA LINN 000EE",
    "2026-11-06",
)
LATER = card_transaction(
    "Intressivaba kuni 11.12.2026; 547025******6630 06.11.26 RAPINA KONSUM 64505 RAPINA LINN",
    "2026-11-09",
)

CASH_PENDING = {
    "entry_reference": None,
    "transaction_id": None,
    "status": "PDNG",
    "booking_date": None,
    "transaction_date": None,
    "value_date": "2026-11-08",
    "credit_debit_indicator": "DBIT",
    "transaction_amount": {"amount": "50.00", "currency": "EUR"},
    "remittance_information": [
        "SULARAHA 516737******4480 08.11.26 12:33 50.00 EUR (595992) Santex RAEPINA 65EE"
    ],
}
CASH_BOOKED = {
    "entry_reference": "2026111002478109-1",
    "transaction_id": None,
    "status": "BOOK",
    "booking_date": "2026-11-10",
    "transaction_date": "2026-11-08",
    "value_date": "2026-11-10",
    "credit_debit_indicator": "DBIT",
    "transaction_amount": {"amount": "50.00", "currency": "EUR"},
    "remittance_information": ["516737******4480 08.11.26 Santex 64505 RAEPINA"],
}
REPEATED_PENDING_EARLY = {
    **card_transaction(
        "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 OLEREX RAPINA 64506 POLVAMAA, RA",
        "2026-11-08",
    ),
    "transaction_amount": {"amount": "95.14", "currency": "EUR"},
}
REPEATED_PENDING_LATE = {
    **REPEATED_PENDING_EARLY,
    "value_date": "2026-11-10",
}
REPEATED_PENDING_NEWEST = {
    **REPEATED_PENDING_EARLY,
    "value_date": "2026-11-11",
}
REPEATED_BOOKED = {
    **REPEATED_PENDING_EARLY,
    "entry_reference": "repeated-pending-booked",
    "status": "BOOK",
    "booking_date": "2026-11-11",
    "transaction_date": "2026-11-08",
    "value_date": "2026-11-11",
}
CONCATENATED_MERCHANT_PENDING = {
    **card_transaction(
        "OST 547025******6630 08.11.26 12:02 14.20 EUR (555453) "
        "RAPINA COOP EHITUSKESKRAPINA LINN 000EE",
        "2026-11-08",
    ),
    "transaction_amount": {"amount": "14.20", "currency": "EUR"},
}
SEPARATED_MERCHANT_PENDING = {
    **card_transaction(
        "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 "
        "RAPINA COOP EHITUSKESK 64504 RAPINA LINN",
        "2026-11-10",
    ),
    "transaction_amount": {"amount": "14.20", "currency": "EUR"},
}
SEPARATED_MERCHANT_PENDING_NEW_DATE = {
    **SEPARATED_MERCHANT_PENDING,
    "value_date": "2026-11-11",
}
COUNTRY_NOISE_PENDING_FIRST = {
    **card_transaction(
        "OST 547025******6630 09.11.26 15:13 5.18 EUR (717153) "
        "DEPO > TALLINN EE TALLINN EE",
        "2026-11-09",
    ),
    "transaction_amount": {"amount": "5.18", "currency": "EUR"},
}
COUNTRY_NOISE_PENDING_LATER = {
    **card_transaction(
        "Intressivaba kuni 11.12.2026; 547025******6630 09.11.26 "
        "DEPO > TALLINN EE 10138 TALLINN",
        "2026-11-11",
    ),
    "transaction_amount": {"amount": "5.18", "currency": "EUR"},
}
COUNTRY_NOISE_PENDING_NEW_DATE = {
    **COUNTRY_NOISE_PENDING_LATER,
    "value_date": "2026-11-12",
}
REPEATED_BOOKED_FIRST = {
    "entry_reference": "2026111102351206-1",
    "transaction_id": None,
    "status": "BOOK",
    "booking_date": "2026-10-28",
    "transaction_date": "2026-10-26",
    "value_date": "2026-10-28",
    "credit_debit_indicator": "DBIT",
    "transaction_amount": {"amount": "20.00", "currency": "EUR"},
    "remittance_information": ["547025******6630 26.10.26 Revolut**0821* D02 R296 Dublin"],
}
REPEATED_BOOKED_LATER = {
    **REPEATED_BOOKED_FIRST,
    "entry_reference": "2026111102355725-1",
    "booking_date": "2026-11-11",
    "value_date": "2026-11-11",
}
REPEATED_BOOKED_DISTINCT = {
    **REPEATED_BOOKED_FIRST,
    "entry_reference": "different-payment-on-same-day",
}


class BankTransactionMergeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = f"transaction-merge-{uuid4()}@example.com"
        async with database.tenant_session(self.owner) as db:
            session = BankSession(
                owner_id=self.owner,
                enable_session_id=str(uuid4()),
                bank_name="TEST",
                country="EE",
                raw_data={},
            )
            db.add(session)
            await db.flush()
            self.account = Account(session_id=session.id, bank_account_id="test", currency="EUR", raw_data={})
            self.category = Category(owner_id=self.owner, name="деревня", month="2026-11")
            db.add_all([self.account, self.category])
            await db.commit()

    async def asyncTearDown(self):
        schema = database.tenant_schema_name(self.owner)
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
        await database.engine.dispose()

    async def import_transactions(self, *transactions):
        async def pages(*_args):
            yield {"transactions": list(transactions)}

        with (
            patch.object(bank_client, "bank_api_headers", AsyncMock(return_value={})),
            patch.object(bank_client, "transaction_pages", pages),
        ):
            return await bank_sync.fetch_transactions(self.account, self.owner)

    async def rows(self):
        async with database.tenant_session(self.owner) as db:
            return list((await db.scalars(select(Transaction).order_by(Transaction.id))).all())

    async def balance(self):
        async with database.tenant_session(self.owner) as db:
            return await db.scalar(
                select(accounts_service.account_balance_expression()).where(Account.id == self.account.id)
            )

    async def test_later_variant_updates_first_row_without_changing_date_or_category(self):
        self.assertEqual(await self.import_transactions(FIRST), 1)
        async with database.tenant_session(self.owner) as db:
            row = await db.scalar(select(Transaction))
            first_id = row.id
            row.category_id = self.category.id
            db.add(ExpenseSplit(transaction_id=row.id, category_id=self.category.id, amount="55.14"))
            await db.commit()

        self.assertEqual(await self.import_transactions(LATER), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].category_id, self.category.id)
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 6, tzinfo=timezone.utc))
        self.assertEqual(rows[0].raw_data["value_date"], "2026-11-06")
        self.assertIn("Intressivaba", rows[0].description)
        async with database.tenant_session(self.owner) as db:
            split = await db.scalar(select(ExpenseSplit))
            self.assertEqual(split.transaction_id, first_id)

    async def test_existing_duplicate_is_collapsed_into_earliest_row(self):
        async with database.tenant_session(self.owner) as db:
            first = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(FIRST),
                value_date=datetime(2026, 11, 6, tzinfo=timezone.utc),
                amount="55.14",
                currency="EUR",
                direction="DBIT",
                category_id=self.category.id,
                raw_data=FIRST,
            )
            duplicate = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(LATER),
                value_date=datetime(2026, 11, 9, tzinfo=timezone.utc),
                amount="55.14",
                currency="EUR",
                direction="DBIT",
                raw_data=LATER,
            )
            db.add_all([first, duplicate])
            await db.flush()
            first_id = first.id
            db.add(ExpenseSplit(transaction_id=first.id, category_id=self.category.id, amount="55.14"))
            await db.commit()

        balance_before_merge = await self.balance()

        self.assertEqual(await self.import_transactions(FIRST, LATER), 0)

        rows = await self.rows()
        self.assertEqual([row.id for row in rows], [first_id])
        self.assertEqual(rows[0].category_id, self.category.id)
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 6, tzinfo=timezone.utc))
        self.assertEqual(rows[0].raw_data["value_date"], "2026-11-06")
        self.assertIn("Intressivaba", rows[0].description)
        self.assertEqual(await self.balance(), balance_before_merge)

    async def assert_duplicate_plan_links(self, survivor_link):
        async with database.tenant_session(self.owner) as db:
            first = Transaction(
                account_id=self.account.id, external_id=bank_sync.transaction_identifier(FIRST),
                value_date=datetime(2026, 11, 6, tzinfo=timezone.utc), amount="55.14",
                currency="EUR", direction="DBIT", raw_data=FIRST,
            )
            duplicate = Transaction(
                account_id=self.account.id, external_id=bank_sync.transaction_identifier(LATER),
                value_date=datetime(2026, 11, 9, tzinfo=timezone.utc), amount="55.14",
                currency="EUR", direction="DBIT", raw_data=LATER,
            )
            duplicate_item = PlanItem(
                owner_id=self.owner, month="2026-11", name="Duplicate plan", direction="DBIT",
                amount="100.00", currency="EUR", payment_status="open",
            )
            db.add_all([first, duplicate, duplicate_item])
            await db.flush()
            survivor_item = duplicate_item
            if survivor_link == "different":
                survivor_item = PlanItem(
                    owner_id=self.owner, month="2026-11", name="Survivor plan", direction="DBIT",
                    amount="100.00", currency="EUR", payment_status="open",
                )
                db.add(survivor_item)
                await db.flush()
            if survivor_link:
                db.add(PlanItemTransaction(transaction_id=first.id, item_id=survivor_item.id))
            db.add(PlanItemTransaction(transaction_id=duplicate.id, item_id=duplicate_item.id))
            await db.commit()
            first_id, item_id, duplicate_item_id = first.id, survivor_item.id, duplicate_item.id

        balance_before = await self.balance()
        self.assertEqual(await self.import_transactions(FIRST, LATER), 0)
        self.assertEqual([row.id for row in await self.rows()], [first_id])
        self.assertEqual(await self.balance(), balance_before)
        async with database.tenant_session(self.owner) as db:
            links = (await db.execute(select(PlanItemTransaction.transaction_id, PlanItemTransaction.item_id))).all()
            self.assertEqual([tuple(link) for link in links], [(first_id, item_id)])
        report = await planning.list_plan(month="2026-11", language="en", owner_id=self.owner)
        items = {item["id"]: item for item in report["items"]}
        self.assertEqual(items[item_id]["transaction_ids"], [first_id])
        self.assertEqual(items[item_id]["actual"], "55.14")
        self.assertEqual(items[item_id]["remaining"], "44.86")
        if survivor_link == "different":
            self.assertEqual(items[duplicate_item_id]["transaction_ids"], [])
            self.assertEqual(items[duplicate_item_id]["actual"], "0.00")
            self.assertEqual(items[duplicate_item_id]["remaining"], "100.00")

    async def test_duplicate_plan_link_moves_to_unlinked_survivor(self):
        await self.assert_duplicate_plan_links(None)

    async def test_duplicate_links_to_same_plan_count_one_transaction(self):
        await self.assert_duplicate_plan_links("same")

    async def test_survivor_plan_takes_precedence_over_duplicate_plan(self):
        await self.assert_duplicate_plan_links("different")

    async def test_pending_cash_withdrawal_becoming_booked_does_not_change_balance(self):
        self.assertEqual(await self.import_transactions(CASH_PENDING), 1)
        rows = await self.rows()
        first_id = rows[0].id
        balance_while_pending = await self.balance()
        self.assertEqual(balance_while_pending, -50)

        self.assertEqual(await self.import_transactions(CASH_BOOKED), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].external_id, CASH_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance_while_pending)

        self.assertEqual(await self.import_transactions(CASH_BOOKED), 0)
        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, CASH_BOOKED["entry_reference"])
        self.assertEqual(await self.balance(), balance_while_pending)

    async def test_existing_pending_and_booked_rows_collapse_to_one_balance_contribution(self):
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(CASH_PENDING),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=CASH_PENDING,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(CASH_BOOKED),
                    booking_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=CASH_BOOKED,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.balance(), -100)
        self.assertEqual(await self.import_transactions(CASH_BOOKED), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, CASH_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(await self.balance(), -50)

        self.assertEqual(await self.import_transactions(CASH_BOOKED), 0)
        self.assertEqual(len(await self.rows()), 1)
        self.assertEqual(await self.balance(), -50)

    async def test_pending_booked_merge_does_not_use_earlier_transaction_date(self):
        pending = {
            **CASH_PENDING,
            "transaction_date": "2026-11-06",
        }
        booked = {
            **CASH_BOOKED,
            "entry_reference": "booked-with-earlier-transaction-date",
            "transaction_date": "2026-11-06",
        }
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(pending),
                    booking_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=pending,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(booked),
                    booking_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=booked,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.import_transactions(booked), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))

    async def test_existing_booked_state_wins_when_pending_record_arrives(self):
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(CASH_PENDING),
                    booking_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=CASH_PENDING,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(CASH_BOOKED),
                    booking_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    amount="50.00",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=CASH_BOOKED,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.import_transactions(CASH_PENDING), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, CASH_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), -50)

    async def test_booked_record_is_not_reverted_by_pending_record_in_same_page(self):
        self.assertEqual(await self.import_transactions(CASH_BOOKED, CASH_PENDING), 1)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, CASH_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(await self.balance(), -50)

    async def test_reimporting_latest_variant_remains_idempotent(self):
        self.assertEqual(await self.import_transactions(FIRST), 1)
        self.assertEqual(await self.import_transactions(LATER), 0)
        self.assertEqual(await self.import_transactions(LATER), 0)

        self.assertEqual(len(await self.rows()), 1)
        self.assertEqual(await self.balance(), Decimal("-55.14"))

    async def test_reimport_does_not_replace_booking_date_with_value_date(self):
        booked = {
            **CASH_BOOKED,
            "entry_reference": "booked-with-earlier-value-date",
            "booking_date": "2026-12-01",
            "transaction_date": "2026-11-30",
            "value_date": "2026-11-30",
            "remittance_information": ["TRANSFER WITH SEPARATE VALUE DATE"],
        }

        self.assertEqual(await self.import_transactions(booked), 1)
        self.assertEqual(await self.import_transactions(booked), 0)

        rows = await self.rows()
        self.assertEqual(rows[0].booking_date, datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 30, tzinfo=timezone.utc))

    async def test_pending_date_remains_display_date_when_booking_crosses_month(self):
        pending = {
            **CASH_PENDING,
            "value_date": "2026-11-30",
            "remittance_information": [
                "SULARAHA 516737******4480 30.11.26 23:50 50.00 EUR (595992) Santex RAEPINA 65EE"
            ],
        }
        booked = {
            **CASH_BOOKED,
            "entry_reference": "cross-month-booking",
            "booking_date": "2026-12-01",
            "transaction_date": "2026-11-30",
            "value_date": "2026-12-01",
            "remittance_information": ["516737******4480 30.11.26 Santex 64505 RAEPINA"],
        }

        self.assertEqual(await self.import_transactions(pending), 1)
        self.assertEqual(await self.import_transactions(booked), 0)

        row = (await self.rows())[0]
        details = dates.transaction_date_details(row)
        self.assertEqual(details["date"], datetime(2026, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(row.raw_data["booking_date"], "2026-11-30")

    async def test_repeated_pending_record_keeps_first_row_date_and_balance(self):
        self.assertEqual(await self.import_transactions(REPEATED_PENDING_EARLY), 1)
        first_id = (await self.rows())[0].id
        balance = await self.balance()

        self.assertEqual(await self.import_transactions(REPEATED_PENDING_LATE), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance)

    async def test_provider_removed_word_separator_keeps_first_date(self):
        self.assertEqual(await self.import_transactions(CONCATENATED_MERCHANT_PENDING), 1)
        first_id = (await self.rows())[0].id
        balance = await self.balance()

        self.assertEqual(await self.import_transactions(SEPARATED_MERCHANT_PENDING), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance)

    async def test_exact_merchant_match_wins_over_compact_collision(self):
        exact = card_transaction(
            "OST 547025******6630 08.11.26 12:02 14.20 EUR (111111) AB C TALLINN",
            "2026-11-08",
        )
        collision = card_transaction(
            "OST 547025******6630 08.11.26 13:02 14.20 EUR (222222) A BC TALLINN",
            "2026-11-08",
        )
        installment = card_transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 AB C TALLINN",
            "2026-11-10",
        )
        for transaction in (exact, collision, installment):
            transaction["transaction_amount"] = {"amount": "14.20", "currency": "EUR"}

        self.assertEqual(await self.import_transactions(exact), 1)
        exact_id = (await self.rows())[0].id
        self.assertEqual(await self.import_transactions(collision), 1)
        balance = await self.balance()

        self.assertEqual(await self.import_transactions(installment), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 2)
        merged = next(row for row in rows if row.id == exact_id)
        self.assertIn("Intressivaba", merged.description)
        self.assertEqual(merged.value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance)

        self.assertEqual(await self.import_transactions(installment), 0)
        self.assertEqual(len(await self.rows()), 2)
        self.assertEqual(await self.balance(), balance)

    async def test_replay_collapses_existing_word_separator_variants(self):
        async with database.tenant_session(self.owner) as db:
            first = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(CONCATENATED_MERCHANT_PENDING),
                value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                amount="14.20",
                currency="EUR",
                direction="DBIT",
                raw_data=CONCATENATED_MERCHANT_PENDING,
            )
            duplicate = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(SEPARATED_MERCHANT_PENDING),
                booking_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                amount="14.20",
                currency="EUR",
                direction="DBIT",
                raw_data=SEPARATED_MERCHANT_PENDING,
            )
            db.add_all([first, duplicate])
            await db.flush()
            first_id = first.id
            await db.commit()

        balance = await self.balance()
        self.assertEqual(await self.import_transactions(SEPARATED_MERCHANT_PENDING_NEW_DATE), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance)

    async def test_replay_collapses_existing_country_code_noise_variant(self):
        async with database.tenant_session(self.owner) as db:
            first = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(COUNTRY_NOISE_PENDING_FIRST),
                value_date=datetime(2026, 11, 9, tzinfo=timezone.utc),
                amount="5.18",
                currency="EUR",
                direction="DBIT",
                raw_data=COUNTRY_NOISE_PENDING_FIRST,
            )
            duplicate = Transaction(
                account_id=self.account.id,
                external_id=bank_sync.transaction_identifier(COUNTRY_NOISE_PENDING_LATER),
                booking_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                value_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                amount="5.18",
                currency="EUR",
                direction="DBIT",
                raw_data=COUNTRY_NOISE_PENDING_LATER,
            )
            db.add_all([first, duplicate])
            await db.flush()
            first_id = first.id
            await db.commit()

        balance = await self.balance()
        self.assertEqual(await self.import_transactions(COUNTRY_NOISE_PENDING_NEW_DATE), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 9, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), balance)

    async def test_repeated_booked_record_keeps_transaction_date_and_balance(self):
        async with database.tenant_session(self.owner) as db:
            first = Transaction(
                account_id=self.account.id,
                external_id=REPEATED_BOOKED_FIRST["entry_reference"],
                booking_date=datetime(2026, 10, 28, tzinfo=timezone.utc),
                value_date=datetime(2026, 10, 28, tzinfo=timezone.utc),
                amount="20.00",
                currency="EUR",
                direction="DBIT",
                raw_data=REPEATED_BOOKED_FIRST,
            )
            duplicate = Transaction(
                account_id=self.account.id,
                external_id=REPEATED_BOOKED_LATER["entry_reference"],
                booking_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                value_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                amount="20.00",
                currency="EUR",
                direction="DBIT",
                raw_data=REPEATED_BOOKED_LATER,
            )
            db.add_all([first, duplicate])
            await db.flush()
            first_id = first.id
            await db.commit()

        balance = await self.balance()
        self.assertEqual(await self.import_transactions(REPEATED_BOOKED_LATER), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, first_id)
        self.assertEqual(rows[0].booking_date, datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.assertEqual(rows[0].raw_data["booking_date"], "2026-10-26")
        self.assertEqual(rows[0].raw_data["transaction_date"], "2026-10-26")
        self.assertEqual(await self.balance(), balance)

        for repeated in (
            REPEATED_BOOKED_LATER,
            REPEATED_BOOKED_LATER,
            REPEATED_BOOKED_FIRST,
            REPEATED_BOOKED_FIRST,
        ):
            self.assertEqual(await self.import_transactions(repeated), 0)
            rows = await self.rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].id, first_id)
            self.assertEqual(rows[0].booking_date, datetime(2026, 10, 26, tzinfo=timezone.utc))
            self.assertEqual(await self.balance(), balance)

        self.assertEqual(await self.import_transactions(REPEATED_BOOKED_DISTINCT), 1)
        self.assertEqual(len(await self.rows()), 2)
        distinct_balance = balance - Decimal("20.00")
        self.assertEqual(await self.balance(), distinct_balance)

        for repeated in (
            REPEATED_BOOKED_FIRST,
            REPEATED_BOOKED_LATER,
            REPEATED_BOOKED_LATER,
            REPEATED_BOOKED_FIRST,
        ):
            self.assertEqual(await self.import_transactions(repeated), 0)
            self.assertEqual(len(await self.rows()), 2)
            self.assertEqual(await self.balance(), distinct_balance)

    async def test_existing_repeated_pending_rows_collapse_to_one_contribution(self):
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_PENDING_EARLY),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_PENDING_EARLY,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_PENDING_LATE),
                    value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_PENDING_LATE,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.balance(), Decimal("-190.28"))
        self.assertEqual(await self.import_transactions(REPEATED_PENDING_NEWEST), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), Decimal("-95.14"))

    async def test_existing_repeated_pending_rows_collapse_when_booked(self):
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_PENDING_EARLY),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_PENDING_EARLY,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_PENDING_LATE),
                    value_date=datetime(2026, 11, 10, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_PENDING_LATE,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.balance(), Decimal("-190.28"))
        self.assertEqual(await self.import_transactions(REPEATED_BOOKED), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, REPEATED_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), Decimal("-95.14"))

    async def test_pending_replay_collapses_existing_pending_and_booked_rows(self):
        async with database.tenant_session(self.owner) as db:
            db.add_all([
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_PENDING_EARLY),
                    value_date=datetime(2026, 11, 8, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_PENDING_EARLY,
                ),
                Transaction(
                    account_id=self.account.id,
                    external_id=bank_sync.transaction_identifier(REPEATED_BOOKED),
                    booking_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                    value_date=datetime(2026, 11, 11, tzinfo=timezone.utc),
                    amount="95.14",
                    currency="EUR",
                    direction="DBIT",
                    raw_data=REPEATED_BOOKED,
                ),
            ])
            await db.commit()

        self.assertEqual(await self.balance(), Decimal("-190.28"))
        self.assertEqual(await self.import_transactions(REPEATED_PENDING_NEWEST), 0)

        rows = await self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].external_id, REPEATED_BOOKED["entry_reference"])
        self.assertEqual(rows[0].raw_data["status"], "BOOK")
        self.assertEqual(rows[0].booking_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(rows[0].value_date, datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.assertEqual(await self.balance(), Decimal("-95.14"))


if __name__ == "__main__":
    unittest.main()
