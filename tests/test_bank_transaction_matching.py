import unittest

from app.services.bank_sync import can_merge_card_transactions, card_transaction_match_key


def transaction(remittance, *, status="PDNG", amount="55.14"):
    return {
        "status": status,
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "remittance_information": [remittance],
    }


class CardTransactionMatchingTest(unittest.TestCase):
    def test_matches_authorization_to_later_installment_record(self):
        authorization = transaction(
            "OST 547025******6630 06.11.26 19:25 55.14 EUR (076214) RAPINA KONSUM RAPINA LINN 000EE"
        )
        installment = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 06.11.26 RAPINA KONSUM 64505 RAPINA LINN"
        )

        self.assertEqual(card_transaction_match_key(authorization), card_transaction_match_key(installment))
        self.assertTrue(can_merge_card_transactions(authorization, installment))

    def test_primary_key_preserves_merchant_word_boundaries(self):
        authorization = transaction(
            "OST 547025******6630 08.11.26 12:02 14.20 EUR (555453) "
            "RAPINA COOP EHITUSKESKRAPINA LINN 000EE",
            amount="14.20",
        )
        installment = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 "
            "RAPINA COOP EHITUSKESK 64504 RAPINA LINN",
            amount="14.20",
        )

        self.assertNotEqual(card_transaction_match_key(authorization), card_transaction_match_key(installment))
        self.assertFalse(can_merge_card_transactions(authorization, installment))

    def test_ignores_standalone_country_code_in_merchant_text(self):
        authorization = transaction(
            "OST 547025******6630 09.11.26 15:13 5.18 EUR (717153) "
            "DEPO > TALLINN EE TALLINN EE",
            amount="5.18",
        )
        installment = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 09.11.26 "
            "DEPO > TALLINN EE 10138 TALLINN",
            amount="5.18",
        )

        self.assertEqual(card_transaction_match_key(authorization), card_transaction_match_key(installment))
        self.assertTrue(can_merge_card_transactions(authorization, installment))

    def test_keeps_country_code_when_it_is_part_of_merchant_name(self):
        named_with_ee = transaction(
            "OST 547025******6630 09.11.26 15:13 5.18 EUR (717153) EE SHOP TALLINN",
            amount="5.18",
        )
        different_merchant = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 09.11.26 SHOP TALLINN",
            amount="5.18",
        )

        self.assertNotEqual(card_transaction_match_key(named_with_ee), card_transaction_match_key(different_merchant))
        self.assertFalse(can_merge_card_transactions(named_with_ee, different_merchant))

    def test_matches_repeated_identical_pending_record(self):
        first = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 OLEREX RAPINA 64506 POLVAMAA, RA",
            amount="95.14",
        )
        repeated = transaction(
            "Intressivaba kuni 11.12.2026; 547025******6630 08.11.26 OLEREX RAPINA 64506 POLVAMAA, RA",
            amount="95.14",
        )

        self.assertTrue(can_merge_card_transactions(first, repeated))

    def test_matches_repeated_identical_booked_record(self):
        first = transaction(
            "547025******6630 26.10.26 Revolut**0821* D02 R296 Dublin",
            status="BOOK",
            amount="20.00",
        )
        first.update(
            entry_reference="2026111102351206-1",
            booking_date="2026-10-28",
            transaction_date="2026-10-26",
        )
        repeated = transaction(
            "547025******6630 26.10.26 Revolut**0821* D02 R296 Dublin",
            status="BOOK",
            amount="20.00",
        )
        repeated.update(
            entry_reference="2026111102355725-1",
            booking_date="2026-11-11",
            transaction_date="2026-10-26",
        )

        self.assertTrue(can_merge_card_transactions(first, repeated))

    def test_does_not_merge_distinct_booked_payments_from_same_day(self):
        first = transaction(
            "547025******6630 26.10.26 Revolut**0821* D02 R296 Dublin",
            status="BOOK",
            amount="20.00",
        )
        first.update(
            entry_reference="first-payment",
            booking_date="2026-10-28",
            transaction_date="2026-10-26",
        )
        second = {**first, "entry_reference": "second-payment"}
        first["value_date"] = "2026-10-28"
        second["value_date"] = "2026-10-29"

        self.assertFalse(can_merge_card_transactions(first, second))

    def test_matches_identical_pending_and_booked_variant(self):
        pending = transaction("547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN")
        booked = transaction("547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN", status="BOOK")

        self.assertTrue(can_merge_card_transactions(pending, booked))

    def test_does_not_merge_pending_with_non_booked_status(self):
        pending = transaction("547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN")
        rejected = transaction(
            "547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN",
            status="RJCT",
        )

        self.assertFalse(can_merge_card_transactions(pending, rejected))

    def test_matches_pending_cash_withdrawal_to_booked_record(self):
        pending = transaction(
            "SULARAHA 516737******4480 08.11.26 12:33 50.00 EUR (595992) Santex RAEPINA 65EE",
            amount="50.00",
        )
        booked = transaction(
            "516737******4480 08.11.26 Santex 64505 RAEPINA",
            status="BOOK",
            amount="50.00",
        )

        self.assertEqual(card_transaction_match_key(pending), card_transaction_match_key(booked))
        self.assertTrue(can_merge_card_transactions(pending, booked))

    def test_does_not_merge_same_variant_only_because_status_changed(self):
        pending = transaction(
            "SULARAHA 516737******4480 08.11.26 12:33 50.00 EUR (595992) Santex RAEPINA 65EE",
            amount="50.00",
        )
        other = transaction(
            "SULARAHA 516737******4480 08.11.26 20:10 50.00 EUR (111111) Santex RAEPINA 65EE",
            status="BOOK",
            amount="50.00",
        )

        self.assertEqual(card_transaction_match_key(pending), card_transaction_match_key(other))
        self.assertFalse(can_merge_card_transactions(pending, other))

    def test_does_not_merge_two_similar_authorizations(self):
        first = transaction(
            "OST 547025******6630 06.11.26 19:25 55.14 EUR (076214) RAPINA KONSUM RAPINA LINN 000EE"
        )
        second = transaction(
            "OST 547025******6630 06.11.26 20:10 55.14 EUR (991122) RAPINA KONSUM RAPINA LINN 000EE"
        )

        self.assertEqual(card_transaction_match_key(first), card_transaction_match_key(second))
        self.assertFalse(can_merge_card_transactions(first, second))

    def test_requires_same_amount_and_merchant(self):
        original = transaction("547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN")
        different_amount = transaction(
            "547025******6630 06.11.26 RAPINA KONSUM RAPINA LINN", amount="55.15"
        )
        different_merchant = transaction("547025******6630 06.11.26 OLEREX RAPINA")

        self.assertFalse(can_merge_card_transactions(original, different_amount))
        self.assertFalse(can_merge_card_transactions(original, different_merchant))


if __name__ == "__main__":
    unittest.main()
