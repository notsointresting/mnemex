from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from mnemex.anchors import FreshnessStatus
from mnemex.bundles import export_bundle, import_bundle
from mnemex.storage import Memory, Node, Storage


_TIMESTAMP = "2026-07-15T09:00:00Z"


def _node(content_hash: str = "anchor-v1") -> Node:
    return Node(
        id="node-auth",
        type="function",
        name="authenticate",
        file="src/auth.py",
        line_start=10,
        content_hash=content_hash,
        language="python",
    )


def _memory(memory_id: str = "decision-auth", anchor_hash: str = "anchor-v1") -> Memory:
    return Memory(
        id=memory_id,
        type="decision",
        content="Use signed session cookies.",
        rationale="Keep request handling stateless.",
        anchor_node_id="node-auth",
        anchor_hash=anchor_hash,
        scope="project-shared",
        source="release-five",
        confidence=0.9,
        importance=0.8,
        created_at=_TIMESTAMP,
        last_accessed=_TIMESTAMP,
        last_verified=_TIMESTAMP,
        tags="auth,cookies",
    )


def _bundle(tmp_path: Path, source: Storage, memory_id: str = "decision-auth") -> Path:
    path = tmp_path / "project-brain.zip"
    export_bundle(
        source,
        path,
        [memory_id],
        agents_md="# Project instructions\nUse the shared auth flow.\n",
        source_commit="a1b2c3d4",
        exported_at=_TIMESTAMP,
    )
    return path


def _replace_records(path: Path, mutate) -> None:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("records.json"))
        agents_md = archive.read("AGENTS.md")
    mutate(records)
    records_bytes = json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    manifest["records_sha256"] = hashlib.sha256(records_bytes).hexdigest()
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("records.json", records_bytes)
        archive.writestr("AGENTS.md", agents_md)


def test_bundle_roundtrip_includes_manifest_context_metadata_and_audits(tmp_path: Path) -> None:
    with Storage() as source, Storage() as destination:
        source.upsert_node(_node())
        source.insert_memory(_memory())
        source.ensure_decision_metadata(
            "decision-auth",
            agent="codex",
            branch="codex/release-five",
            commit_hash="a1b2c3d4",
            source_request="bundle roundtrip",
            review_after="2026-08-01T00:00:00Z",
        )
        source.set_decision_status("decision-auth", "retired")
        source.record_recall("decision-auth", "2026-07-15T10:00:00Z")
        source.record_confirmation("decision-auth", "2026-07-15T11:00:00Z")
        source.connection.execute(
            """
            INSERT INTO redaction_audit(
                memory_id, timestamp, field, pattern_name, original_snippet,
                replacement
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("decision-auth", _TIMESTAMP, "content", "fixture", "safe...data", "[REDACTED]"),
        )
        path = _bundle(tmp_path, source)

        with zipfile.ZipFile(path) as archive:
            assert sorted(archive.namelist()) == ["AGENTS.md", "manifest.json", "records.json"]
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_commit"] == "a1b2c3d4"
        assert manifest["provenance"] == [{
            "anchor_hash": "anchor-v1",
            "anchor_node_id": "node-auth",
            "memory_id": "decision-auth",
            "source": "release-five",
        }]

        result = import_bundle(destination, path)

        assert result.memory_ids == ("decision-auth",)
        assert result.id_map == {"decision-auth": "decision-auth"}
        assert result.source_commit == "a1b2c3d4"
        assert result.agents_md == "# Project instructions\nUse the shared auth flow.\n"
        assert result.freshness[0].status is FreshnessStatus.FRESH
        assert destination.get_node("node-auth") == _node()
        assert destination.get_memory("decision-auth") == _memory()
        metadata = destination.get_decision_metadata("decision-auth")
        assert metadata is not None
        assert metadata.status == "retired"
        assert metadata.access_count == 1
        assert metadata.last_recalled_at == "2026-07-15T10:00:00Z"
        assert metadata.last_confirmed_at == "2026-07-15T11:00:00Z"
        assert destination.list_redactions("decision-auth") == [
            ("content", "fixture", "safe...data", "[REDACTED]")
        ]


def test_import_sanitizes_tampered_secret_content_and_keeps_audit(tmp_path: Path) -> None:
    with Storage() as source, Storage() as destination:
        source.upsert_node(_node())
        source.insert_memory(_memory())
        path = _bundle(tmp_path, source)
        secret = "ghp_" + "a" * 36

        def add_secret(records: dict) -> None:
            records["memories"][0]["content"] = f"Never persist {secret}."
            records["memories"][0]["rationale"] = f"token={secret}"

        _replace_records(path, add_secret)
        result = import_bundle(destination, path)
        stored = destination.get_memory(result.memory_ids[0])

        assert stored is not None
        assert secret not in stored.content
        assert secret not in stored.rationale
        assert "[REDACTED:github_token]" in stored.content
        assert destination.list_redactions(stored.id)


def test_import_remaps_conflicting_memory_id_without_clobbering(tmp_path: Path) -> None:
    with Storage() as source, Storage() as destination:
        source.insert_memory(_memory())
        path = _bundle(tmp_path, source)
        existing = _memory()
        destination.insert_memory(existing)

        result = import_bundle(destination, path)
        imported_id = result.memory_ids[0]

        assert imported_id != existing.id
        assert result.id_map == {existing.id: imported_id}
        assert destination.get_memory(existing.id) == existing
        imported = destination.get_memory(imported_id)
        assert imported is not None
        assert imported.content == "Use signed session cookies."
        assert result.freshness[0].status is FreshnessStatus.ORPHANED


def test_import_returns_freshness_against_existing_conflicting_node(tmp_path: Path) -> None:
    with Storage() as source, Storage() as destination:
        source.upsert_node(_node("anchor-v1"))
        source.insert_memory(_memory(anchor_hash="anchor-v1"))
        path = _bundle(tmp_path, source)
        destination.upsert_node(_node("anchor-v2"))

        result = import_bundle(destination, path)

        assert result.skipped_node_ids == ("node-auth",)
        assert result.freshness[0].memory_id == "decision-auth"
        assert result.freshness[0].status is FreshnessStatus.STALE
        assert result.freshness[0].stored_hash == "anchor-v1"
        assert result.freshness[0].current_hash == "anchor-v2"
