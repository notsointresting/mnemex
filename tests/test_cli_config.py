from __future__ import annotations

import json

import pytest

from mnemex.__main__ import _demo, _doctor, _init, _serve, _serve_config, _why
from mnemex.anchors import remember
from mnemex.storage import Node, Storage


def test_serve_flags_override_environment_config(monkeypatch) -> None:
    monkeypatch.setenv("MNEMEX_SEMANTIC_JUDGE_ENABLED", "false")
    monkeypatch.setenv("MNEMEX_MAX_EVIDENCE_TOKENS", "222")

    class Args:
        semantic_judge = True
        openai_model = "gpt-5.6"
        openai_timeout_seconds = 3.5
        max_evidence_tokens = 123

    config = _serve_config(Args())

    assert config.semantic_judge_enabled is True
    assert config.openai_model == "gpt-5.6"
    assert config.openai_timeout_seconds == 3.5
    assert config.max_evidence_tokens == 123


def test_serve_configures_streamable_http_without_starting_a_network_listener(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeMCP:
        class Settings:
            host = ""
            port = 0

        settings = Settings()

        def run(self, *, transport: str) -> None:
            observed["transport"] = transport
            observed["host"] = self.settings.host
            observed["port"] = self.settings.port

    class FakeServer:
        mcp = FakeMCP()

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr("mnemex.server.create_server", lambda *args, **kwargs: FakeServer())

    assert _serve(":memory:", transport="http", host="127.0.0.1", port=9876) == 0
    assert observed == {
        "transport": "streamable-http",
        "host": "127.0.0.1",
        "port": 9876,
        "closed": True,
    }


def test_offline_demo_is_a_deterministic_intervention(capsys) -> None:
    assert _demo(":memory:", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "offline"
    assert payload["guard"]["blocked"] is True
    assert payload["guard"]["verdict"] == "contradiction"
    assert payload["old_decision_freshness_after_symbol_change"] == "stale"
    assert payload["override"] is not None


def test_why_defaults_to_human_readable_output(tmp_path, capsys) -> None:
    database = tmp_path / "brain.sqlite3"
    with Storage(database) as storage:
        node = Node(
            "auth-node", "function", "authenticate", "src/auth.py", 1,
            "hash", "python",
        )
        storage.upsert_node(node)
        remember(
            storage,
            "Use signed short-lived tokens.",
            anchor=node.id,
            rationale="Authentication stays stateless.",
        )

    assert _why(str(database), "authenticate", "project-shared") == 0
    output = capsys.readouterr().out
    assert "WHY: authenticate" in output
    assert "CURRENT DECISION" in output
    assert "HEALTH" in output


def test_init_discovers_project_directory_and_creates_default_brain(tmp_path, capsys) -> None:
    (tmp_path / "service.py").write_text("def ready():\n    return True\n")

    assert _init(str(tmp_path), no_index=False) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["database"].replace("\\", "/").endswith(".mnemex/mnemex.sqlite3")
    assert payload["index"]["nodes_upserted"] >= 1
    assert payload["fts5_ready"] is True
    assert payload["redaction_probe_passed"] is True


def test_check_show_payload_prints_sanitized_evidence(tmp_path, capsys) -> None:
    from mnemex.__main__ import main

    db = str(tmp_path / "cli-check.sqlite3")
    secret_value = "hunter2" + "cliprobe"
    code = main(
        [
            "check",
            "src/auth.py",
            f"use password={secret_value} for sessions",
            "--db",
            db,
            "--show-payload",
        ]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code in (0, 2)
    assert "payload" in payload
    assert secret_value not in out
    assert payload["payload_summary"]["redaction_count"] >= 1
    assert payload["payload_sent_to_provider"] is False



_STABLE_VEC_STATUSES = {
    "available",
    "disabled-by-environment",
    "package-not-installed",
    "extension-loading-unsupported",
    "extension-load-failed",
}


def test_doctor_reports_ready_in_core_no_vec_mode(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    monkeypatch.delenv("MNEMEX_SEMANTIC_JUDGE_ENABLED", raising=False)

    assert _doctor(":memory:") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "ready"
    assert payload["retrieval_mode"] == "bm25-only"
    assert payload["sqlite_vec_available"] is False
    assert payload["sqlite_vec_status"] == "disabled-by-environment"
    assert payload["fts5_ready"] is True
    assert payload["redaction_probe_passed"] is True
    assert payload["mcp_tools_registered"] >= 1
    assert payload["semantic_judge_enabled"] is False
    assert payload["transport_default"] == "stdio"
    assert payload["network_listener_started"] is False


def test_doctor_vector_status_is_always_a_stable_string(capsys) -> None:
    # Whatever the host supports, the reported status is one stable token and
    # never a raw exception message or a filesystem path.
    exit_code = _doctor(":memory:")
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0  # missing vector support is not a doctor failure
    assert payload["sqlite_vec_status"] in _STABLE_VEC_STATUSES
    assert ":\\" not in payload["sqlite_vec_status"]
    assert "Error" not in payload["sqlite_vec_status"]


def test_doctor_reports_hybrid_when_vector_available(capsys) -> None:
    with Storage() as probe:
        available = probe.vec_available
    if not available:
        pytest.skip("sqlite-vec extension unavailable (no-ML mode)")

    assert _doctor(":memory:") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["retrieval_mode"] == "hybrid"
    assert payload["sqlite_vec_available"] is True
    assert payload["sqlite_vec_status"] == "available"


def test_doctor_reports_missing_database(tmp_path, capsys) -> None:
    missing = tmp_path / "does-not-exist.sqlite3"
    assert _doctor(str(missing)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "missing"


def test_offline_demo_is_blocked_in_core_no_vec_mode(monkeypatch, capsys) -> None:
    # The deterministic block must be identical in core (no-vec) mode as it is
    # in the vector-enabled default.
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")

    assert _demo(":memory:", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "offline"
    assert payload["guard"]["blocked"] is True
    assert payload["guard"]["verdict"] == "contradiction"
    assert payload["old_decision_freshness_after_symbol_change"] == "stale"
