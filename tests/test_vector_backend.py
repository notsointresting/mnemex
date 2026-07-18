"""A1 gate: core mode must never attempt a sqlite_vec import."""

from __future__ import annotations

import importlib
import sys

import pytest

from mnemex import vector_backend
from mnemex.storage import Storage


class _Sentinel:
    """Meta-path finder that fails the test if sqlite_vec import is attempted."""

    def __init__(self) -> None:
        self.attempts = 0

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "sqlite_vec" or fullname.startswith("sqlite_vec."):
            self.attempts += 1
            raise AssertionError("sqlite_vec import attempted in core mode")
        return None


@pytest.fixture()
def sentinel(monkeypatch):
    guard = _Sentinel()
    monkeypatch.delitem(sys.modules, "sqlite_vec", raising=False)
    sys.meta_path.insert(0, guard)
    importlib.invalidate_caches()
    try:
        yield guard
    finally:
        sys.meta_path.remove(guard)


def test_no_vec_env_never_imports(monkeypatch, sentinel):
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    module, status = vector_backend.load_module()
    assert module is None
    assert status == "disabled-by-environment"
    assert sentinel.attempts == 0


def test_storage_core_mode_never_imports(monkeypatch, sentinel):
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    with Storage(":memory:") as storage:
        assert storage.vec_available is False
        assert storage.vec_status == "disabled-by-environment"
        assert storage.vec_serialize is None
    assert sentinel.attempts == 0
    assert "sqlite_vec" not in sys.modules


def test_missing_package_status(monkeypatch):
    monkeypatch.delenv("MNEMEX_NO_VEC", raising=False)
    monkeypatch.delitem(sys.modules, "sqlite_vec", raising=False)

    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "sqlite_vec":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(vector_backend.importlib, "import_module", fake_import)
    module, status = vector_backend.load_module()
    assert module is None
    assert status == "package-not-installed"


def test_oserror_maps_to_load_failed(monkeypatch):
    monkeypatch.delenv("MNEMEX_NO_VEC", raising=False)

    def fake_import(name, *args, **kwargs):
        raise OSError("quarantined")

    monkeypatch.setattr(vector_backend.importlib, "import_module", fake_import)
    module, status = vector_backend.load_module()
    assert module is None
    assert status == "extension-load-failed"


def test_public_modules_import_clean_in_core_mode(monkeypatch, sentinel):
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    for name in (
        "mnemex.storage",
        "mnemex.retrieval",
        "mnemex.evidence",
        "mnemex.decision_guard",
        "mnemex.constraints",
    ):
        importlib.import_module(name)
    assert sentinel.attempts == 0
    assert "sqlite_vec" not in sys.modules
