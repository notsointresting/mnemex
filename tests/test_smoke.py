import sqlite3

import mnemex
import sqlite_vec


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
        if toggle_extension_loading is not None:
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
