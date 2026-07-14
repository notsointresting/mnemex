"""Independent Phase 1 adversarial tests for the anchor + storage moat.

These probe the anchor core from an attacker's / edge-case angle without
touching production code or the existing suites. Loops are bounded and
seeded so every run is deterministic (no Hypothesis, no new dependency).
"""

import random
import sqlite3
from itertools import chain, combinations
from pathlib import Path

import pytest

from mnemex.anchors import (
    AmbiguousAnchorError,
    Anchor,
    AnchorNotFoundError,
    FreshnessReport,
    FreshnessStatus,
    check_freshness,
    remember,
    resolve_anchor,
)
from mnemex.storage import Node, Storage

ALL_SCOPES = ("agent-private", "project-shared", "user-global")


def _make_node(
    node_id: str,
    *,
    node_type: str = "function",
    name: str = "authenticate",
    file: str = "src/auth.py",
    line_start: int = 10,
    content_hash: str = "hash-1",
    language: str = "python",
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=name,
        file=file,
        line_start=line_start,
        content_hash=content_hash,
        language=language,
    )


def _rand_hash(rng: random.Random) -> str:
    length = rng.randint(6, 16)
    return "".join(rng.choice("0123456789abcdef") for _ in range(length))


def _rand_token(rng: random.Random, prefix: str) -> str:
    length = rng.randint(3, 8)
    body = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                    for _ in range(length))
    return prefix + body


def _other(rng: random.Random, choices: tuple[str, ...], exclude: str) -> str:
    return rng.choice([value for value in choices if value != exclude])


def _fts_hits(storage: Storage, token: str) -> int:
    return storage.connection.execute(
        "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH ?",
        (token,),
    ).fetchone()[0]


def _memory_count(storage: Storage) -> int:
    return storage.connection.execute(
        "SELECT count(*) FROM memories"
    ).fetchone()[0]


def _assert_fts_consistent(storage: Storage) -> None:
    # FTS5 'integrity-check' raises SQLITE_CORRUPT if the index has any row
    # that does not line up with the external content table (an orphan).
    storage.connection.execute(
        "INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')"
    )


# --- Scenario 1: property-style freshness determinism -----------------------
def test_freshness_is_deterministic_and_depends_only_on_hash_equality() -> None:
    rng = random.Random(20260714)
    types = ("function", "class", "method", "module")
    langs = ("python", "typescript", "go", "rust")
    saw_fresh = saw_stale = False
    fresh_with_mutation = stale_without_mutation = False

    with Storage() as storage:
        for i in range(60):
            node_id, memory_id = f"node-{i}", f"mem-{i}"
            stored = _rand_hash(rng)
            if i % 2 == 0:
                current = stored
            else:
                current = _rand_hash(rng)
                while current == stored:
                    current = _rand_hash(rng)
            mutate = i % 3 != 0

            name_a = _rand_token(rng, "sym-")
            file_a = _rand_token(rng, "src/") + ".py"
            type_a = rng.choice(types)
            lang_a = rng.choice(langs)
            line_a = rng.randint(1, 5000)

            storage.upsert_node(
                Node(
                    id=node_id,
                    type=type_a,
                    name=name_a,
                    file=file_a,
                    line_start=line_a,
                    content_hash=stored,
                    language=lang_a,
                )
            )
            memory = remember(
                storage, f"decision {i}", anchor=node_id, memory_id=memory_id
            )
            assert memory.anchor_hash == stored

            if mutate:
                current_node = Node(
                    id=node_id,
                    type=_other(rng, types, type_a),
                    name="renamed-" + name_a,
                    file="moved-" + file_a,
                    line_start=line_a + rng.randint(1, 999),
                    content_hash=current,
                    language=_other(rng, langs, lang_a),
                )
            else:
                current_node = Node(
                    id=node_id,
                    type=type_a,
                    name=name_a,
                    file=file_a,
                    line_start=line_a,
                    content_hash=current,
                    language=lang_a,
                )
            storage.upsert_node(current_node)

            expected_status = (
                FreshnessStatus.FRESH
                if stored == current
                else FreshnessStatus.STALE
            )
            expected = FreshnessReport(
                memory_id=memory_id,
                status=expected_status,
                anchor_node_id=node_id,
                stored_hash=stored,
                current_hash=current,
            )
            first = check_freshness(storage, memory_id=memory_id)
            second = check_freshness(storage, memory_id=memory_id)
            assert first == [expected]
            assert second == first

            if expected_status is FreshnessStatus.FRESH:
                saw_fresh = True
                fresh_with_mutation = fresh_with_mutation or mutate
            else:
                saw_stale = True
                stale_without_mutation = stale_without_mutation or not mutate

    assert saw_fresh and saw_stale
    assert fresh_with_mutation
    assert stale_without_mutation


# --- Scenario 2: state-transition sequence on one immutable memory ----------
def test_state_transitions_never_mutate_the_stamped_memory_row() -> None:
    h0, h1, h2 = "hash-v0", "hash-v1", "hash-v2"
    node_id, memory_id = "sym-node", "state-memory"

    with Storage() as storage:
        storage.upsert_node(_make_node(node_id, content_hash=h0))
        remembered = remember(
            storage, "Stateful decision.", anchor=node_id, memory_id=memory_id
        )
        original = storage.get_memory(memory_id)
        assert remembered == original
        assert original.anchor_hash == h0
        assert original.anchor_node_id == node_id

        def expect(status: FreshnessStatus, current: str | None) -> None:
            report = check_freshness(storage, memory_id=memory_id)
            assert report == [
                FreshnessReport(
                    memory_id=memory_id,
                    status=status,
                    anchor_node_id=node_id,
                    stored_hash=h0,
                    current_hash=current,
                )
            ]
            persisted = storage.get_memory(memory_id)
            assert persisted == original
            assert persisted.anchor_hash == h0

        # FRESH at write time.
        expect(FreshnessStatus.FRESH, h0)

        # Hash edit (plus unrelated field churn) -> STALE.
        storage.upsert_node(
            _make_node(
                node_id,
                content_hash=h1,
                name="renamed",
                file="src/other.py",
                line_start=999,
            )
        )
        expect(FreshnessStatus.STALE, h1)

        # Exact hash restoration -> FRESH.
        storage.upsert_node(_make_node(node_id, content_hash=h0))
        expect(FreshnessStatus.FRESH, h0)

        # Node deletion -> ORPHANED (current hash unknown).
        storage.delete_node(node_id)
        assert check_freshness(storage, memory_id=memory_id) == [
            FreshnessReport(
                memory_id=memory_id,
                status=FreshnessStatus.ORPHANED,
                anchor_node_id=node_id,
                stored_hash=h0,
                current_hash=None,
            )
        ]
        assert storage.get_memory(memory_id) == original

        # Same node id recreated with different content -> STALE, not blindly
        # fresh just because the id exists again.
        storage.upsert_node(
            _make_node(node_id, content_hash=h2, name="resurrected")
        )
        expect(FreshnessStatus.STALE, h2)

        # Recreated with the exact original hash -> FRESH once more.
        storage.upsert_node(_make_node(node_id, content_hash=h0))
        expect(FreshnessStatus.FRESH, h0)


# --- Scenario 3: adversarial scope isolation over every subset --------------
def test_scope_isolation_over_every_non_empty_subset() -> None:
    scope_to_id = {scope: f"mem-{scope}" for scope in ALL_SCOPES}

    with Storage() as storage:
        for scope, memory_id in scope_to_id.items():
            remember(
                storage,
                f"note for {scope}",
                memory_id=memory_id,
                scope=scope,
            )

        subsets = chain.from_iterable(
            combinations(ALL_SCOPES, size)
            for size in range(1, len(ALL_SCOPES) + 1)
        )
        for subset in subsets:
            reports = check_freshness(storage, scopes=subset)
            returned = {report.memory_id for report in reports}
            assert returned == {scope_to_id[scope] for scope in subset}

            for scope, memory_id in scope_to_id.items():
                filtered = check_freshness(
                    storage, scopes=subset, memory_id=memory_id
                )
                if scope in subset:
                    assert [r.memory_id for r in filtered] == [memory_id]
                else:
                    assert filtered == []

        # Default scope excludes both agent-private and user-global.
        default_ids = {r.memory_id for r in check_freshness(storage)}
        assert default_ids == {scope_to_id["project-shared"]}

        # Empty / invalid / non-collection scopes must raise before any query.
        with pytest.raises(ValueError, match="non-empty collection"):
            check_freshness(storage, scopes=())
        with pytest.raises(ValueError, match="non-empty collection"):
            check_freshness(storage, scopes="project-shared")
        with pytest.raises(ValueError, match="Invalid memory scope"):
            check_freshness(storage, scopes=("bogus-scope",))


# --- Scenario 4: exact, parameterized anchor resolution ---------------------
def test_anchor_resolution_is_exact_and_parameterized() -> None:
    with Storage() as storage:
        # SQL metacharacters are treated as a literal symbol, never as SQL.
        vault = _make_node(
            "vault", name="secret", file="src/vault.py", content_hash="h"
        )
        storage.upsert_node(vault)
        injection = "x' OR '1'='1"
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(
                storage, Anchor(file="src/vault.py", symbol=injection)
            )
        assert storage.get_node("vault") == vault  # not leaked, not dropped

        literal = _make_node(
            "literal-inj",
            name=injection,
            file="src/vault.py",
            content_hash="h2",
        )
        storage.upsert_node(literal)
        assert (
            resolve_anchor(
                storage, Anchor(file="src/vault.py", symbol=injection)
            )
            == literal
        )

        # LIKE wildcards are literal, not fuzzy.
        storage.upsert_node(
            _make_node("auth", name="auth", file="src/like.py", content_hash="h")
        )
        storage.upsert_node(
            _make_node("apct", name="a%", file="src/like.py", content_hash="h")
        )
        matched = resolve_anchor(
            storage, Anchor(file="src/like.py", symbol="a%")
        )
        assert matched == storage.get_node("apct")
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(storage, Anchor(file="src/like.py", symbol="au_h"))

        # Windows backslash paths match exactly; no separator normalization.
        win_file = r"src\auth\login.py"
        storage.upsert_node(
            _make_node("win", name="login", file=win_file, content_hash="h")
        )
        assert (
            resolve_anchor(
                storage, Anchor(file=win_file, symbol="login")
            ).id
            == "win"
        )
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(
                storage, Anchor(file="src/auth/login.py", symbol="login")
            )

        # No case-insensitive fallback on symbol or file.
        storage.upsert_node(
            _make_node(
                "cap", name="Authenticate", file="src/case.py", content_hash="h"
            )
        )
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(
                storage, Anchor(file="src/case.py", symbol="authenticate")
            )
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(
                storage, Anchor(file="SRC/CASE.PY", symbol="Authenticate")
            )

        # Duplicate symbol (with metacharacters) is ambiguous, not guessed.
        dup_name = "we;ird--name"
        storage.upsert_node(
            _make_node(
                "dup-a",
                name=dup_name,
                file="src/dup.py",
                line_start=1,
                content_hash="h",
            )
        )
        storage.upsert_node(
            _make_node(
                "dup-b",
                name=dup_name,
                file="src/dup.py",
                line_start=2,
                content_hash="h",
            )
        )
        with pytest.raises(AmbiguousAnchorError, match="matches=2"):
            resolve_anchor(storage, Anchor(file="src/dup.py", symbol=dup_name))

        # The node_id resolution path is parameterized too.
        count_before = storage.connection.execute(
            "SELECT count(*) FROM nodes"
        ).fetchone()[0]
        evil_id = "n'); DROP TABLE nodes;--"
        storage.upsert_node(
            _make_node(evil_id, name="x", file="src/evil.py", content_hash="h")
        )
        assert resolve_anchor(storage, evil_id).id == evil_id
        count_after = storage.connection.execute(
            "SELECT count(*) FROM nodes"
        ).fetchone()[0]
        assert count_after == count_before + 1
        with pytest.raises(AnchorNotFoundError):
            resolve_anchor(storage, "no-such-id")


# --- Scenario 5: failed remember leaves no memory and no orphan FTS row -----
def test_failed_remember_leaves_no_memory_or_orphan_fts_row() -> None:
    with Storage() as storage:
        # Missing anchor.
        with pytest.raises(AnchorNotFoundError):
            remember(storage, "zzmissing body", anchor="ghost-node")

        # Ambiguous anchor.
        storage.upsert_node(
            _make_node("amb-a", name="dup", file="src/amb.py", line_start=1)
        )
        storage.upsert_node(
            _make_node("amb-b", name="dup", file="src/amb.py", line_start=2)
        )
        with pytest.raises(AmbiguousAnchorError):
            remember(
                storage,
                "zzambiguous body",
                anchor=Anchor(file="src/amb.py", symbol="dup"),
            )

        # Invalid scope (rejected before the INSERT).
        with pytest.raises(ValueError, match="Invalid memory scope"):
            remember(storage, "zzinvalidscope body", scope="bogus")

        assert _memory_count(storage) == 0
        for token in ("zzmissing", "zzambiguous", "zzinvalidscope"):
            assert _fts_hits(storage, token) == 0
        _assert_fts_consistent(storage)

        # Duplicate memory id: first write succeeds, second must fully roll
        # back including its FTS trigger row.
        remember(storage, "zzduporiginal body", memory_id="dup-id")
        assert _memory_count(storage) == 1
        with pytest.raises(sqlite3.IntegrityError):
            remember(storage, "zzdupsecond body", memory_id="dup-id")

        assert _memory_count(storage) == 1
        assert _fts_hits(storage, "zzdupsecond") == 0
        assert _fts_hits(storage, "zzduporiginal") == 1
        _assert_fts_consistent(storage)

        survivors = storage.list_memories(ALL_SCOPES)
        assert [memory.content for memory in survivors] == ["zzduporiginal body"]


# --- Scenario 6: persisted reopen preserves hashes and freshness verdicts ---
def test_file_database_reopen_preserves_hashes_and_verdicts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mnemex.sqlite3"
    ids = ("fresh", "stale", "orphan", "unanchored")

    with Storage(database) as storage:
        storage.upsert_node(_make_node("fresh-node", content_hash="H-fresh"))
        remember(storage, "fresh decision", anchor="fresh-node",
                 memory_id="fresh")

        storage.upsert_node(_make_node("stale-node", content_hash="H-stale-old"))
        remember(storage, "stale decision", anchor="stale-node",
                 memory_id="stale")
        storage.upsert_node(_make_node("stale-node", content_hash="H-stale-new"))

        storage.upsert_node(_make_node("orphan-node", content_hash="H-orphan"))
        remember(storage, "orphan decision", anchor="orphan-node",
                 memory_id="orphan")
        storage.delete_node("orphan-node")

        remember(storage, "unanchored decision", memory_id="unanchored")

        before_reports = check_freshness(storage)
        before_memories = {mid: storage.get_memory(mid) for mid in ids}

    with Storage(database) as reopened:
        after_reports = check_freshness(reopened)
        assert after_reports == before_reports
        for mid in ids:
            assert reopened.get_memory(mid) == before_memories[mid]

        by_id = {report.memory_id: report for report in after_reports}
        assert by_id["fresh"].status is FreshnessStatus.FRESH
        assert by_id["fresh"].stored_hash == "H-fresh"
        assert by_id["stale"].status is FreshnessStatus.STALE
        assert by_id["stale"].stored_hash == "H-stale-old"
        assert by_id["stale"].current_hash == "H-stale-new"
        assert by_id["orphan"].status is FreshnessStatus.ORPHANED
        assert by_id["orphan"].stored_hash == "H-orphan"
        assert by_id["orphan"].current_hash is None
        assert by_id["unanchored"].status is FreshnessStatus.UNANCHORED
        assert by_id["unanchored"].stored_hash is None
