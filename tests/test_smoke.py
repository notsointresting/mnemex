import json
import sqlite3
import subprocess
import sys

import mnemex

try:
    import sqlite_vec
except (ImportError, OSError):
    sqlite_vec = None


def test_runtime_smoke() -> None:
    assert mnemex.__version__ == "0.1.0"

    connection = sqlite3.connect(":memory:")
    try:
        # FTS5 is compiled into SQLite and must always work — it is the no-ML
        # (BM25) retrieval baseline and requires no loadable extension.
        connection.execute("CREATE VIRTUAL TABLE smoke_fts USING fts5(content)")
        connection.execute(
            "INSERT INTO smoke_fts(content) VALUES (?)", ("anchored memory",)
        )
        result = connection.execute(
            "SELECT content FROM smoke_fts WHERE smoke_fts MATCH ?",
            ("anchored",),
        ).fetchone()
        assert result == ("anchored memory",)

        # sqlite-vec is an OPTIONAL extension. Some Python builds (notably on
        # macOS) ship a sqlite3 compiled without loadable-extension support, in
        # which case mnemex runs in no-ML mode. Only assert vec behavior where
        # the platform can load it.
        toggle_extension_loading = getattr(
            connection, "enable_load_extension", None
        )
        if toggle_extension_loading is not None and sqlite_vec is not None:
            loaded = False
            try:
                toggle_extension_loading(True)
                sqlite_vec.load(connection)
                loaded = True
            except Exception:
                loaded = False
            finally:
                try:
                    toggle_extension_loading(False)
                except Exception:
                    pass

            if loaded:
                vec_version = connection.execute(
                    "SELECT vec_version()"
                ).fetchone()
                assert vec_version is not None and vec_version[0]
    finally:
        connection.close()



# --------------------------------------------------------------------------- #
# The core package must import when the optional sqlite-vec extension is
# absent or its native payload fails to load. Each case runs in a fresh
# interpreter so the top-level guard is exercised for real, not monkeypatched.
# --------------------------------------------------------------------------- #

_IMPORT_BLOCKED = (
    "import sys, json\n"
    "sys.modules['sqlite_vec'] = None\n"  # -> ImportError on `import sqlite_vec`
    "from mnemex.storage import Storage\n"
    "st = Storage()\n"
    "print(json.dumps({'vec': st.vec_available, 'status': st.vec_status, "
    "'mod_none': st.vec_serialize is None}))\n"
    "st.close()\n"
)

_IMPORT_OSERROR = (
    "import sys, json, importlib.abc\n"
    "sys.modules.pop('sqlite_vec', None)\n"
    "class F(importlib.abc.MetaPathFinder):\n"
    "    def find_spec(self, name, path, target=None):\n"
    "        if name == 'sqlite_vec':\n"
    "            raise OSError('simulated native failure')\n"
    "        return None\n"
    "sys.meta_path.insert(0, F())\n"
    "from mnemex.storage import Storage\n"
    "st = Storage()\n"
    "print(json.dumps({'vec': st.vec_available, 'status': st.vec_status}))\n"
    "st.close()\n"
)


def _run_isolated(script: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"import guard failed (rc={result.returncode}):\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_package_imports_when_sqlite_vec_missing() -> None:
    data = _run_isolated(_IMPORT_BLOCKED)
    assert data["mod_none"] is True
    assert data["vec"] is False
    assert data["status"] == "package-not-installed"


def test_package_imports_when_sqlite_vec_native_load_raises_oserror() -> None:
    data = _run_isolated(_IMPORT_OSERROR)
    assert data["vec"] is False
    assert data["status"] == "extension-load-failed"
