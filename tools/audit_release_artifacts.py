"""Audit release artifacts: inventory members, forbid native payloads.

Standard library only. Reads every wheel and source zip in the given
directory (default ``dist``), then:

* records each member name and SHA-256;
* fails if the mnemex wheel contains ``.exe``, ``.dll``, ``.pyd``, ``.so``,
  ``.dylib``, ``.msi``, or a nested archive;
* fails if the core wheel is not tagged ``py3-none-any``;
* fails if wheel metadata acquires ``sqlite-vec``, an embedding model, or an
  OpenAI dependency outside an extra;
* writes ``build/release-audit.json`` as CI evidence (not a public artifact).

Exit code 0 means clean; 1 means at least one violation.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "build" / "release-audit.json"

NATIVE_SUFFIXES = (".exe", ".dll", ".pyd", ".so", ".dylib", ".msi")
NESTED_ARCHIVE_SUFFIXES = (".zip", ".whl", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z")
# Dependencies the CORE wheel must never require outside an extra.
FORBIDDEN_CORE_REQUIRES = ("sqlite-vec", "openai", "sentence-transformers", "torch")
REQUIRED_WHEEL_TAG = "py3-none-any"
# A core dependency is permitted only when its complete environment marker is
# the unambiguous PEP 508 extra equality used by optional dependencies.
EXTRA_MARKER_RE = re.compile(
    r";\s*extra == (?:\"[^\"]+\"|'[^']+')\s*$",
    re.IGNORECASE,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _audit_wheel(wheel: Path) -> tuple[dict, list[str]]:
    violations: list[str] = []
    members: dict[str, str] = {}
    requires: list[str] = []
    tag = wheel.stem.split("-", 2)[-1]

    if tag != REQUIRED_WHEEL_TAG:
        violations.append(
            f"{wheel.name}: wheel tag is {tag!r}, expected {REQUIRED_WHEEL_TAG!r}"
        )

    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            data = archive.read(info.filename)
            members[info.filename] = _sha256_bytes(data)
            lower = info.filename.lower()
            if lower.endswith(NATIVE_SUFFIXES):
                violations.append(
                    f"{wheel.name}: native/executable member {info.filename}"
                )
            if lower.endswith(NESTED_ARCHIVE_SUFFIXES):
                violations.append(
                    f"{wheel.name}: nested archive member {info.filename}"
                )
            if lower.endswith(".dist-info/metadata"):
                for line in data.decode("utf-8", "replace").splitlines():
                    if line.startswith("Requires-Dist:"):
                        requires.append(line.removeprefix("Requires-Dist:").strip())

    for req in requires:
        name = re.split(r"[\s;<>=!\[(]", req, maxsplit=1)[0].lower()
        if name in FORBIDDEN_CORE_REQUIRES and not EXTRA_MARKER_RE.search(req):
            violations.append(
                f"{wheel.name}: core requires forbidden dependency {req!r}"
            )

    return (
        {
            "wheel": wheel.name,
            "tag": tag,
            "members": members,
            "requires_dist": requires,
        },
        violations,
    )


def _audit_source_zip(source: Path) -> tuple[dict, list[str]]:
    violations: list[str] = []
    members: dict[str, str] = {}
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            data = archive.read(info.filename)
            members[info.filename] = _sha256_bytes(data)
            if info.filename.lower().endswith(NATIVE_SUFFIXES):
                violations.append(
                    f"{source.name}: native/executable member {info.filename}"
                )
            if info.filename.lower().endswith(NESTED_ARCHIVE_SUFFIXES):
                violations.append(
                    f"{source.name}: nested archive member {info.filename}"
                )
    return {"archive": source.name, "members": members}, violations


def main(argv: list[str]) -> int:
    dist = Path(argv[1]) if len(argv) > 1 else ROOT / "dist"
    if not dist.is_dir():
        print(f"no such directory: {dist}", file=sys.stderr)
        return 1

    wheels = sorted(dist.glob("*.whl"))
    zips = sorted(p for p in dist.glob("*.zip") if "source" in p.name.lower())
    if not wheels:
        print(f"no wheel found in {dist}", file=sys.stderr)
        return 1

    reports: list[dict] = []
    violations: list[str] = []
    for wheel in wheels:
        report, found = _audit_wheel(wheel)
        reports.append(report)
        violations.extend(found)
    for source in zips:
        report, found = _audit_source_zip(source)
        reports.append(report)
        violations.extend(found)

    AUDIT_PATH.parent.mkdir(exist_ok=True)
    AUDIT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": reports,
                "violations": violations,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(AUDIT_PATH)
    for violation in violations:
        print(f"VIOLATION: {violation}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
