"""Build a portable source bundle and emit SHA-256 checksums.

Produces exactly three kinds of release artifact and nothing else:

* the Python wheel already built by ``python -m build --wheel``;
* a portable source zip built here;
* a ``SHA256SUMS.txt`` covering the wheel(s) and the source zip.

Standard library only: no SBOM dependency and no packed single-file executable.
An unsigned standalone executable in ``dist/`` is a hard error, because the
final-sprint release contract ships wheel and source only.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
INCLUDE = ("src", "npm", "README.md", "pyproject.toml", "LICENSE")
SOURCE_ZIP_NAME = "mnemex-0.1.0-source.zip"
CHECKSUM_NAME = "SHA256SUMS.txt"
# Extensions that must never appear in a release directory for this sprint.
FORBIDDEN_SUFFIXES = (".exe", ".msi", ".appimage", ".dmg", ".bin")
CHECKSUM_SUFFIXES = (".whl", ".zip", ".tar.gz")


def build_source_zip() -> Path:
    """Zip the portable source tree, skipping compiled caches."""
    archive_path = DIST / SOURCE_ZIP_NAME
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in INCLUDE:
            path = ROOT / item
            if not path.exists():
                continue
            if path.is_file():
                archive.write(path, path.name)
            else:
                for file in sorted(path.rglob("*")):
                    if file.is_file() and "__pycache__" not in file.parts:
                        archive.write(file, file.relative_to(ROOT))
    return archive_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_no_unsigned_executable() -> None:
    offenders = sorted(
        item.name
        for item in DIST.iterdir()
        if item.is_file() and item.suffix.lower() in FORBIDDEN_SUFFIXES
    )
    if offenders:
        raise SystemExit(
            "refusing to publish an unsigned standalone executable: "
            + ", ".join(offenders)
        )


def write_checksums() -> Path:
    """Write ``SHA256SUMS.txt`` in coreutils format for release artifacts."""
    artifacts = sorted(
        item
        for item in DIST.iterdir()
        if item.is_file()
        and item.name != CHECKSUM_NAME
        and item.name.lower().endswith(CHECKSUM_SUFFIXES)
    )
    checksum_path = DIST / CHECKSUM_NAME
    lines = [f"{_sha256(item)}  {item.name}" for item in artifacts]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def main() -> int:
    DIST.mkdir(exist_ok=True)
    _assert_no_unsigned_executable()
    source_zip = build_source_zip()
    checksums = write_checksums()
    print(source_zip)
    print(checksums)
    print(checksums.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
