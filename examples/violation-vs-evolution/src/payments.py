"""Demo payment module for the violation-vs-evolution fixture."""


def process_payment(order_id: str, amount: int, idempotency_key: str) -> dict:
    """Charge an order exactly once.

    Retries must never double-charge: the idempotency key is checked
    before any write reaches the ledger.
    """
    if not idempotency_key:
        raise ValueError("idempotency key is required")
    if _already_charged(idempotency_key):
        return {"status": "duplicate", "order_id": order_id}
    _write_ledger(order_id, amount, idempotency_key)
    return {"status": "charged", "order_id": order_id, "amount": amount}


def _already_charged(idempotency_key: str) -> bool:
    return False


def _write_ledger(order_id: str, amount: int, idempotency_key: str) -> None:
    pass
