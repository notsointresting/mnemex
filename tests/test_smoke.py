import sqlite3

import mnemex
import sqlite_vec


def test_runtime_smoke() -> None:
    assert mnemex.__version__ == "0.1.0"

    connection = sqlite3.connect(":memory:")
    toggle_extension_loading = getattr(connection, "enable_load_extension", None)
    try:
        if toggle_extension_loading is not None:
            toggle_extension_loading(True)
        try:
            sqlite_vec.load(connection)
        finally:
            if toggle_extension_loading is not None:
                toggle_extension_loading(False)

        vec_version = connection.execute("SELECT vec_version()").fetchone()
        assert vec_version is not None and vec_version[0]

        connection.execute("CREATE VIRTUAL TABLE smoke_fts USING fts5(content)")
        connection.execute(
            "INSERT INTO smoke_fts(content) VALUES (?)", ("anchored memory",)
        )
        result = connection.execute(
            "SELECT content FROM smoke_fts WHERE smoke_fts MATCH ?", ("anchored",)
        ).fetchone()
        assert result == ("anchored memory",)
    finally:
        connection.close()
