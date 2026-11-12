import unittest
from types import SimpleNamespace

from app.transaction_details import transaction_counterparty_name


def transaction(direction, raw_data):
    return SimpleNamespace(direction=direction, raw_data=raw_data)


class TransactionCounterpartyTest(unittest.TestCase):
    def test_uses_creditor_for_outgoing_transaction(self):
        row = transaction("DBIT", {
            "creditor": {"name": "  Transport Company  "},
            "debtor": {"name": "Account Owner"},
        })

        self.assertEqual(transaction_counterparty_name(row), "Transport Company")

    def test_uses_debtor_for_incoming_transaction(self):
        row = transaction("CRDT", {
            "creditor": {"name": "Account Owner"},
            "debtor": {"name": "Employer"},
        })

        self.assertEqual(transaction_counterparty_name(row), "Employer")

    def test_does_not_expose_the_account_owner_as_fallback(self):
        row = transaction("DBIT", {"debtor": {"name": "Account Owner"}})

        self.assertIsNone(transaction_counterparty_name(row))

    def test_ignores_malformed_party_data(self):
        self.assertIsNone(transaction_counterparty_name(transaction("DBIT", {"creditor": "Merchant"})))
        self.assertIsNone(transaction_counterparty_name(transaction("DBIT", {"creditor": {"name": " "}})))


if __name__ == "__main__":
    unittest.main()
