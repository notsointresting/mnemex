"""Build a portable source bundle alongside Python wheel artifacts."""

from __future__ import annotations

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
INCLUDE = ("src", "npm", "README.md", "pyproject.toml", "LICENSE")


def main() -> int:
    DIST.mkdir(exist_ok=True)
    archive_path = DIST / "mnemex-0.1.0-source.zip"
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
    print(archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
