"""A2 gate: release auditor rejects native payloads and forbidden deps."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "audit_release_artifacts", ROOT / "tools" / "audit_release_artifacts.py"
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def _make_wheel(
    path: Path,
    *,
    members: dict[str, bytes] | None = None,
    requires: list[str] | None = None,
) -> Path:
    metadata = "Metadata-Version: 2.1\nName: mnemex\nVersion: 0.1.0\n"
    for req in requires or []:
        metadata += f"Requires-Dist: {req}\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mnemex/__init__.py", "")
        archive.writestr("mnemex-0.1.0.dist-info/METADATA", metadata)
        for name, data in (members or {}).items():
            archive.writestr(name, data)
    return path


def _make_source_zip(path: Path, *, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _run(tmp_path: Path, wheel_name: str, **kwargs) -> tuple[int, dict]:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist / wheel_name, **kwargs)
    audit.AUDIT_PATH = tmp_path / "release-audit.json"
    code = audit.main(["audit", str(dist)])
    report = json.loads(audit.AUDIT_PATH.read_text(encoding="utf-8"))
    return code, report


def test_clean_core_wheel_passes(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        requires=["fastmcp==3.4.4", 'sqlite-vec>=0.1.6; extra == "vector"'],
    )
    assert code == 0
    assert report["violations"] == []
    assert report["artifacts"][0]["tag"] == "py3-none-any"
    assert report["artifacts"][0]["members"]


def test_native_member_fails(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        members={"mnemex/vec0.dll": b"MZ"},
    )
    assert code == 1
    assert any("vec0.dll" in violation for violation in report["violations"])


def test_non_pure_tag_fails(tmp_path: Path) -> None:
    code, report = _run(tmp_path, "mnemex-0.1.0-cp312-cp312-win_amd64.whl")
    assert code == 1
    assert any("wheel tag" in violation for violation in report["violations"])


def test_forbidden_core_dependency_fails(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        requires=["sqlite-vec>=0.1.6"],
    )
    assert code == 1
    assert any("sqlite-vec" in violation for violation in report["violations"])


def test_extra_gated_dependency_allowed(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        requires=['openai>=1; extra == "openai"'],
    )
    assert code == 0
    assert report["violations"] == []


def test_compact_or_forged_extra_marker_does_not_allow_dependency(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        requires=['openai>=1; extra=="openai"'],
    )
    assert code == 1
    assert any("openai" in violation for violation in report["violations"])


def test_extra_text_inside_another_marker_does_not_allow_dependency(
    tmp_path: Path,
) -> None:
    code, report = _run(
        tmp_path,
        "mnemex-0.1.0-py3-none-any.whl",
        requires=['openai>=1; platform_system == "extra == \'openai\'"'],
    )
    assert code == 1
    assert any("openai" in violation for violation in report["violations"])


def test_nested_archive_in_source_zip_fails(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _make_wheel(dist / "mnemex-0.1.0-py3-none-any.whl")
    _make_source_zip(dist / "mnemex-source.zip", members={"mnemex/data.7z": b"7z"})
    audit.AUDIT_PATH = tmp_path / "release-audit.json"

    code = audit.main(["audit", str(dist)])
    report = json.loads(audit.AUDIT_PATH.read_text(encoding="utf-8"))

    assert code == 1
    assert any("data.7z" in violation for violation in report["violations"])


def test_missing_wheel_is_error(tmp_path: Path) -> None:
    empty = tmp_path / "dist"
    empty.mkdir()
    assert audit.main(["audit", str(empty)]) == 1
