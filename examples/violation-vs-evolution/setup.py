"""Create the fixture brain: index payments.py and anchor the decision."""

from mnemex.anchors import Anchor, remember
from mnemex.indexer import index_file
from mnemex.storage import Storage

DB = "fixture.sqlite3"

with Storage(DB) as storage:
    index_file(storage, "src/payments.py")
    memory = remember(
        storage,
        "All payment writes must be idempotent; retries must not double-charge",
        rationale=(
            "Ledger writes are not reversible; duplicate charges cost "
            "refunds and trust"
        ),
        anchor=Anchor(file="src/payments.py", symbol="process_payment"),
        tags="architecture,payments",
    )
    print(f"anchored decision: {memory.id}")
