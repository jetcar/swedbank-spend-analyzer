from app.models import Transaction


def transaction_counterparty_name(transaction: Transaction) -> str | None:
    raw = transaction.raw_data if isinstance(transaction.raw_data, dict) else {}
    party_key = {"DBIT": "creditor", "CRDT": "debtor"}.get(transaction.direction)
    party = raw.get(party_key) if party_key else None
    if not isinstance(party, dict):
        return None
    name = party.get("name")
    if not isinstance(name, str):
        return None
    return name.strip() or None
