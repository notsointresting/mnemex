"""Portable project-brain bundles built on the existing :mod:`mnemex.storage` API.

Bundles are ordinary ZIP files containing canonical JSON plus the caller-supplied
``AGENTS.md`` text. They deliberately carry no database file, so they remain
portable across sqlite-vec availability and storage schema migrations.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from mnemex.anchors import FreshnessReport, check_freshness
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import DecisionMetadata, Memory, Node, Storage

__all__ = [
    "BundleExport",
    "BundleImport",
    "export_bundle",
    "import_bundle",
]


_BUNDLE_VERSION = 1
_BUNDLE_NAMES = frozenset({"manifest.json", "records.json", "AGENTS.md"})
_MAX_BUNDLE_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_RECORDS = 10_000
_SCOPES = frozenset({"agent-private", "project-shared", "user-global"})
_DECISION_STATUSES = frozenset({"active", "superseded", "retired"})


@dataclass(frozen=True, slots=True)
class BundleExport:
    """Details of a bundle written by :func:`export_bundle`."""

    path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BundleImport:
    """Imported memory IDs, their current freshness, and retained bundle data."""

    memory_ids: tuple[str, ...]
    freshness: tuple[FreshnessReport, ...]
    id_map: dict[str, str]
    agents_md: str
    source_commit: str | None
    skipped_node_ids: tuple[str, ...]


def export_bundle(
    storage: Storage,
    destination: str | Path,
    memory_ids: Collection[str],
    *,
    agents_md: str,
    source_commit: str | None = None,
    exported_at: str | None = None,
) -> BundleExport:
    """Export selected stored memories and their portable project context.

    ``memory_ids`` is intentionally explicit, which prevents an accidental
    export of agent-private memories. Every requested ID must exist.
    """
    selected_ids = _selected_ids(memory_ids)
    memories = _selected_memories(storage, selected_ids)
    metadata = [storage.get_decision_metadata(memory.id) for memory in memories]
    nodes = _selected_nodes(storage, memories)
    records = {
        "memories": [_memory_payload(memory) for memory in memories],
        "nodes": [_node_payload(node) for node in nodes],
        "decision_metadata": [
            _metadata_payload(item) for item in metadata if item is not None
        ],
        "redaction_audit": _redaction_audits(storage, memories),
    }
    agents_text = _clean_agents_md(agents_md)
    records_bytes = _canonical_json(records)
    manifest = {
        "bundle_version": _BUNDLE_VERSION,
        "exported_at": _validated_timestamp(
            _utc_timestamp() if exported_at is None else exported_at,
            "exported_at",
        ),
        "source_commit": _clean_optional(source_commit, "source_commit"),
        "records_sha256": _sha256(records_bytes),
        "agents_md_sha256": _sha256(agents_text.encode("utf-8")),
        "record_counts": {name: len(values) for name, values in records.items()},
        "provenance": [
            {
                "memory_id": memory.id,
                "source": _clean_text(memory.source, "source"),
                "anchor_node_id": memory.anchor_node_id,
                "anchor_hash": memory.anchor_hash,
            }
            for memory in memories
        ],
    }
    path = Path(destination)
    _write_bundle(path, manifest, records_bytes, agents_text)
    return BundleExport(path=path, manifest=manifest)


def import_bundle(storage: Storage, source: str | Path) -> BundleImport:
    """Import a validated bundle without overwriting existing records.

    Memory ID collisions receive a deterministic UUID derived from the bundle
    payload hash. Nodes are never overwritten: an existing node remains the
    source of truth, and freshness is reported against that node immediately.
    """
    manifest, records, agents_md = _read_bundle(Path(source))
    bundle_hash = manifest["records_sha256"]
    nodes = [_node_from_record(item) for item in _record_list(records, "nodes")]
    raw_memories = _record_list(records, "memories")
    memories = [_memory_from_record(item) for item in raw_memories]
    if len(memories) != len({memory.id for memory in memories}):
        raise ValueError("Bundle contains duplicate memory IDs")

    node_id_map, skipped_nodes = _import_nodes(storage, nodes)
    id_map = _memory_id_map(storage, memories, bundle_hash)
    imported_ids: list[str] = []
    for record, memory in zip(raw_memories, memories, strict=True):
        imported = _remap_memory(memory, id_map[memory.id], node_id_map)
        redactions = RedactionLog()
        # Record bundle-input redactions even though validation has already
        # produced a clean Memory value for the storage write below.
        for field in ("content", "rationale", "tags"):
            sanitize(record[field], field_name=field, log=redactions)
        storage.insert_memory(imported, redactions=redactions)
        imported_ids.append(imported.id)

    _import_metadata(storage, _record_list(records, "decision_metadata"), id_map)
    _import_audits(storage, _record_list(records, "redaction_audit"), id_map)

    reports_by_id = {
        report.memory_id: report
        for report in check_freshness(
            storage,
            scopes=tuple(sorted(_SCOPES)),
        )
        if report.memory_id in set(imported_ids)
    }
    return BundleImport(
        memory_ids=tuple(imported_ids),
        freshness=tuple(reports_by_id[memory_id] for memory_id in imported_ids),
        id_map=id_map,
        agents_md=agents_md,
        source_commit=manifest["source_commit"],
        skipped_node_ids=tuple(skipped_nodes),
    )


def _selected_ids(memory_ids: Collection[str]) -> tuple[str, ...]:
    if isinstance(memory_ids, (str, bytes)):
        raise ValueError("memory_ids must be a non-empty collection")
    values = tuple(memory_ids)
    if not values:
        raise ValueError("memory_ids must be a non-empty collection")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("memory IDs must be non-empty strings")
    return tuple(sorted(set(values)))


def _selected_memories(storage: Storage, memory_ids: tuple[str, ...]) -> list[Memory]:
    memories: list[Memory] = []
    missing: list[str] = []
    for memory_id in memory_ids:
        memory = storage.get_memory(memory_id)
        if memory is None:
            missing.append(memory_id)
        else:
            memories.append(memory)
    if missing:
        raise ValueError(f"Unknown memory IDs: {', '.join(missing)}")
    return sorted(memories, key=lambda memory: (memory.created_at, memory.id))


def _selected_nodes(storage: Storage, memories: list[Memory]) -> list[Node]:
    nodes = {
        memory.anchor_node_id: storage.get_node(memory.anchor_node_id)
        for memory in memories
        if memory.anchor_node_id is not None
    }
    return sorted(
        (node for node in nodes.values() if node is not None),
        key=lambda node: node.id,
    )


def _redaction_audits(storage: Storage, memories: list[Memory]) -> list[dict[str, str]]:
    audits: list[dict[str, str]] = []
    for memory in memories:
        rows = storage.connection.execute(
            """
            SELECT memory_id, timestamp, field, pattern_name, original_snippet,
                   replacement
            FROM redaction_audit
            WHERE memory_id = ?
            ORDER BY id
            """,
            (memory.id,),
        ).fetchall()
        audits.extend(
            {
                "memory_id": row[0],
                "timestamp": row[1],
                "field": row[2],
                "pattern_name": row[3],
                "original_snippet": row[4],
                "replacement": row[5],
            }
            for row in rows
        )
    return audits


def _memory_payload(memory: Memory) -> dict[str, Any]:
    payload = asdict(memory)
    return {
        name: _clean_optional(value, name)
        if value is None or isinstance(value, str)
        else value
        for name, value in payload.items()
    }


def _node_payload(node: Node) -> dict[str, Any]:
    return {name: _clean_text(value, name) if isinstance(value, str) else value
            for name, value in asdict(node).items()}


def _metadata_payload(metadata: DecisionMetadata) -> dict[str, Any]:
    return {
        name: _clean_optional(value, name)
        if value is None or isinstance(value, str)
        else value
        for name, value in asdict(metadata).items()
    }


def _write_bundle(
    path: Path, manifest: dict[str, Any], records_bytes: bytes, agents_md: str
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_member(archive, "manifest.json", _canonical_json(manifest))
        _write_member(archive, "records.json", records_bytes)
        _write_member(archive, "AGENTS.md", agents_md.encode("utf-8"))


def _write_member(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)


def _read_bundle(path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != _BUNDLE_NAMES:
                raise ValueError("Bundle must contain exactly manifest.json, records.json, and AGENTS.md")
            if any(info.file_size > _MAX_BUNDLE_MEMBER_BYTES for info in infos):
                raise ValueError("Bundle member exceeds the size limit")
            manifest_bytes = archive.read("manifest.json")
            records_bytes = archive.read("records.json")
            agents_bytes = archive.read("AGENTS.md")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Invalid mnemex bundle") from error
    manifest = _json_object(manifest_bytes, "manifest.json")
    records = _json_object(records_bytes, "records.json")
    try:
        agents_md = agents_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("AGENTS.md must be UTF-8") from error
    _validate_manifest(manifest, records_bytes, agents_md)
    return manifest, records, _clean_agents_md(agents_md)


def _json_object(raw: bytes, name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} must contain a JSON object") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return decoded


def _validate_manifest(manifest: dict[str, Any], records_bytes: bytes, agents_md: str) -> None:
    if manifest.get("bundle_version") != _BUNDLE_VERSION:
        raise ValueError("Unsupported bundle version")
    if not isinstance(manifest.get("source_commit"), (str, type(None))):
        raise ValueError("Manifest source_commit must be a string or null")
    _validated_timestamp(_required_string(manifest, "exported_at"), "exported_at")
    if manifest.get("records_sha256") != _sha256(records_bytes):
        raise ValueError("Bundle records checksum does not match manifest")
    if manifest.get("agents_md_sha256") != _sha256(agents_md.encode("utf-8")):
        raise ValueError("AGENTS.md checksum does not match manifest")
    counts = manifest.get("record_counts")
    provenance = manifest.get("provenance")
    if not isinstance(counts, dict) or not isinstance(provenance, list):
        raise ValueError("Manifest record counts and provenance are required")


def _record_list(records: dict[str, Any], name: str) -> list[Any]:
    value = records.get(name)
    if not isinstance(value, list):
        raise ValueError(f"Bundle {name} must be a list")
    if len(value) > _MAX_RECORDS:
        raise ValueError(f"Bundle {name} exceeds the record limit")
    return value


def _node_from_record(record: Any) -> Node:
    values = _object_fields(record, {
        "id", "type", "name", "file", "line_start", "content_hash", "language"
    }, "node")
    line_start = values["line_start"]
    if isinstance(line_start, bool) or not isinstance(line_start, int) or line_start < 0:
        raise ValueError("node line_start must be a non-negative integer")
    return Node(
        id=_required_clean_text(values, "id", "node"),
        type=_required_clean_text(values, "type", "node"),
        name=_required_clean_text(values, "name", "node"),
        file=_required_clean_text(values, "file", "node").replace("\\", "/"),
        line_start=line_start,
        content_hash=_required_clean_text(values, "content_hash", "node"),
        language=_required_clean_text(values, "language", "node"),
    )


def _memory_from_record(record: Any) -> Memory:
    fields = {
        "id", "type", "content", "rationale", "anchor_node_id", "anchor_hash",
        "scope", "source", "confidence", "importance", "created_at",
        "last_accessed", "last_verified", "tags",
    }
    values = _object_fields(record, fields, "memory")
    confidence = _finite_number(values["confidence"], "memory confidence", minimum=0, maximum=1)
    importance = _finite_number(values["importance"], "memory importance", minimum=0)
    scope = _required_clean_text(values, "scope", "memory")
    if scope not in _SCOPES:
        raise ValueError(f"Invalid memory scope: {scope!r}")
    anchor_node_id = _optional_clean_text(values, "anchor_node_id", "memory")
    anchor_hash = _optional_clean_text(values, "anchor_hash", "memory")
    if (anchor_node_id is None) != (anchor_hash is None):
        raise ValueError("memory anchors require both node ID and hash, or neither")
    return Memory(
        id=_required_clean_text(values, "id", "memory"),
        type=_required_clean_text(values, "type", "memory"),
        content=_required_clean_text(values, "content", "memory"),
        rationale=_required_clean_text(values, "rationale", "memory", allow_empty=True),
        anchor_node_id=anchor_node_id,
        anchor_hash=anchor_hash,
        scope=scope,
        source=_required_clean_text(values, "source", "memory"),
        confidence=confidence,
        importance=importance,
        created_at=_validated_timestamp(_required_clean_text(values, "created_at", "memory"), "created_at"),
        last_accessed=_validated_timestamp(_required_clean_text(values, "last_accessed", "memory"), "last_accessed"),
        last_verified=_validated_timestamp(_required_clean_text(values, "last_verified", "memory"), "last_verified"),
        tags=_required_clean_text(values, "tags", "memory", allow_empty=True),
    )


def _import_nodes(storage: Storage, nodes: list[Node]) -> tuple[dict[str, str], list[str]]:
    node_id_map: dict[str, str] = {}
    skipped: list[str] = []
    for node in sorted(nodes, key=lambda item: item.id):
        if node.id in node_id_map:
            raise ValueError("Bundle contains duplicate node IDs")
        node_id_map[node.id] = node.id
        if storage.get_node(node.id) is not None:
            skipped.append(node.id)
            continue
        storage.upsert_node(node)
    return node_id_map, skipped


def _memory_id_map(storage: Storage, memories: list[Memory], bundle_hash: str) -> dict[str, str]:
    assigned: set[str] = set()
    result: dict[str, str] = {}
    for memory in memories:
        candidate = memory.id
        attempt = 0
        while candidate in assigned or storage.get_memory(candidate) is not None:
            candidate = str(uuid5(NAMESPACE_URL, f"mnemex:{bundle_hash}:{memory.id}:{attempt}"))
            attempt += 1
        assigned.add(candidate)
        result[memory.id] = candidate
    return result


def _remap_memory(memory: Memory, memory_id: str, node_id_map: dict[str, str]) -> Memory:
    anchor_node_id = memory.anchor_node_id
    if anchor_node_id is not None:
        anchor_node_id = node_id_map.get(anchor_node_id, anchor_node_id)
    return Memory(
        id=memory_id,
        type=memory.type,
        content=memory.content,
        rationale=memory.rationale,
        anchor_node_id=anchor_node_id,
        anchor_hash=memory.anchor_hash,
        scope=memory.scope,
        source=memory.source,
        confidence=memory.confidence,
        importance=memory.importance,
        created_at=memory.created_at,
        last_accessed=memory.last_accessed,
        last_verified=memory.last_verified,
        tags=memory.tags,
    )


def _import_metadata(storage: Storage, records: list[Any], id_map: dict[str, str]) -> None:
    seen: set[str] = set()
    for record in records:
        values = _object_fields(record, {
            "memory_id", "status", "supersedes_memory_id", "agent", "client",
            "session", "branch", "commit_hash", "source_request", "review_after",
            "access_count", "last_recalled_at", "last_confirmed_at",
        }, "decision metadata")
        source_id = _required_clean_text(values, "memory_id", "decision metadata")
        if source_id not in id_map:
            raise ValueError("decision metadata references a memory outside this bundle")
        if source_id in seen:
            raise ValueError("Bundle contains duplicate decision metadata")
        seen.add(source_id)
        status = _required_clean_text(values, "status", "decision metadata")
        if status not in _DECISION_STATUSES:
            raise ValueError(f"Invalid decision status: {status!r}")
        supersedes = _optional_clean_text(values, "supersedes_memory_id", "decision metadata")
        if supersedes is not None:
            supersedes = id_map.get(supersedes, supersedes)
            if storage.get_memory(supersedes) is None:
                supersedes = None
        metadata = storage.ensure_decision_metadata(
            id_map[source_id],
            status=status,
            supersedes_memory_id=supersedes,
            agent=_optional_clean_text(values, "agent", "decision metadata"),
            client=_optional_clean_text(values, "client", "decision metadata"),
            session=_optional_clean_text(values, "session", "decision metadata"),
            branch=_optional_clean_text(values, "branch", "decision metadata"),
            commit_hash=_optional_clean_text(values, "commit_hash", "decision metadata"),
            source_request=_optional_clean_text(values, "source_request", "decision metadata"),
            review_after=_optional_clean_text(values, "review_after", "decision metadata"),
        )
        if status != metadata.status:
            storage.set_decision_status(
                id_map[source_id], status, supersedes_memory_id=supersedes
            )
        access_count = values["access_count"]
        if isinstance(access_count, bool) or not isinstance(access_count, int) or access_count < 0:
            raise ValueError("decision metadata access_count must be non-negative")
        last_recalled = _optional_timestamp(values, "last_recalled_at", "decision metadata")
        last_confirmed = _optional_timestamp(values, "last_confirmed_at", "decision metadata")
        with storage.connection:
            storage.connection.execute(
                """
                UPDATE decision_metadata
                SET access_count = ?, last_recalled_at = ?, last_confirmed_at = ?
                WHERE memory_id = ?
                """,
                (access_count, last_recalled, last_confirmed, id_map[source_id]),
            )


def _import_audits(storage: Storage, records: list[Any], id_map: dict[str, str]) -> None:
    for record in records:
        values = _object_fields(record, {
            "memory_id", "timestamp", "field", "pattern_name", "original_snippet",
            "replacement",
        }, "redaction audit")
        source_id = _required_clean_text(values, "memory_id", "redaction audit")
        if source_id not in id_map:
            raise ValueError("redaction audit references a memory outside this bundle")
        timestamp = _validated_timestamp(
            _required_clean_text(values, "timestamp", "redaction audit"), "timestamp"
        )
        field = _required_clean_text(values, "field", "redaction audit")
        pattern_name = _required_clean_text(values, "pattern_name", "redaction audit")
        original_snippet = _required_clean_text(values, "original_snippet", "redaction audit", allow_empty=True)
        replacement = _required_clean_text(values, "replacement", "redaction audit", allow_empty=True)
        with storage.connection:
            storage.connection.execute(
                """
                INSERT INTO redaction_audit(
                    memory_id, timestamp, field, pattern_name, original_snippet,
                    replacement
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (id_map[source_id], timestamp, field, pattern_name, original_snippet, replacement),
            )


def _object_fields(record: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError(f"Invalid {label} record fields")
    return record


def _required_clean_text(values: Mapping[str, Any], name: str, label: str, *, allow_empty: bool = False) -> str:
    value = values[name]
    if not isinstance(value, str):
        raise ValueError(f"{label} {name} must be a string")
    cleaned = _clean_text(value, name)
    if not allow_empty and not cleaned.strip():
        raise ValueError(f"{label} {name} must not be empty")
    return cleaned


def _optional_clean_text(values: Mapping[str, Any], name: str, label: str) -> str | None:
    value = values[name]
    if value is None:
        return None
    return _required_clean_text(values, name, label)


def _optional_timestamp(values: Mapping[str, Any], name: str, label: str) -> str | None:
    value = values[name]
    if value is None:
        return None
    return _validated_timestamp(_required_clean_text(values, name, label), name)


def _finite_number(value: Any, label: str, *, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"{label} is outside the allowed range")
    return number


def _required_string(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str):
        raise ValueError(f"Manifest {name} must be a string")
    return value


def _clean_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return sanitize(value, field_name=field)


def _clean_agents_md(value: str) -> str:
    """Sanitize AGENTS.md without changing harmless leading/trailing space."""
    cleaned = _clean_text(value, "agents_md")
    return value if cleaned == value.strip() else cleaned


def _clean_optional(value: str | None, field: str) -> str | None:
    return None if value is None else _clean_text(value, field)


def _validated_timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
