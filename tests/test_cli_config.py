from __future__ import annotations

import json

from mnemex.__main__ import _demo, _init, _serve, _serve_config, _why
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
