#!/usr/bin/env python3
"""Build an installable blender-nozzle legacy add-on zip."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = "nozzle_blender"
REQUIRED_PY = ["__init__.py", "engine.py", "operators.py", "panels.py", "properties.py"]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def native_candidates(build_dir: Path) -> list[Path]:
    patterns = [
        "_nozzle_native*.so",
        "_nozzle_native*.pyd",
        "_nozzle_native*.dll",
        "_nozzle_native*.dylib",
        "Release/_nozzle_native*.pyd",
        "Release/_nozzle_native*.dll",
        "Release/_nozzle_native*.so",
    ]
    found: list[Path] = []
    for pattern in patterns:
        found.extend(p for p in build_dir.glob(pattern) if p.is_file())
    return sorted(set(found))


def infer_pyabi(native_name: str) -> str:
    match = re.search(r"cpython-(\d+)", native_name)
    if match:
        return f"py{match.group(1)}"
    match = re.search(r"cp(\d+)", native_name)
    if match:
        return f"py{match.group(1)}"
    return "pyunknown"


def zip_dir(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir.parent))


def verify_zip(zip_path: Path, native_name: str) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
    if not names:
        fail("zip is empty")
    bad_roots = sorted({name.split("/", 1)[0] for name in names if name and not name.startswith(f"{ADDON}/")})
    if bad_roots:
        fail(f"zip has non-addon roots: {', '.join(bad_roots)}")
    required = [f"{ADDON}/{name}" for name in REQUIRED_PY]
    required += [f"{ADDON}/{native_name}", f"{ADDON}/LICENSE", f"{ADDON}/README.md"]
    missing = [name for name in required if name not in names]
    if missing:
        fail(f"zip is missing required entries: {', '.join(missing)}")
    native_entries = [name for name in names if name.startswith(f"{ADDON}/_nozzle_native")]
    if native_entries != [f"{ADDON}/{native_name}"]:
        fail(f"zip must contain exactly one native extension, got: {native_entries}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--channel", required=True, choices=("latest", "versioned", "ci"))
    parser.add_argument("--short-sha", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--blender-version", required=True)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    build_dir = args.build_dir.resolve()
    output_dir = args.output_dir.resolve()
    candidates = native_candidates(build_dir)
    if len(candidates) != 1:
        fail(f"expected exactly one _nozzle_native artifact in {build_dir}, found {len(candidates)}")
    native = candidates[0]
    pyabi = infer_pyabi(native.name)

    if args.channel == "versioned":
        if not args.tag:
            fail("--tag is required for versioned packages")
        package_name = f"blender-nozzle-{args.tag}-blender{args.blender_version}-{args.platform}-{pyabi}"
    elif args.channel == "latest":
        package_name = f"blender-nozzle-latest-{args.short_sha}-blender{args.blender_version}-{args.platform}-{pyabi}"
    else:
        package_name = f"blender-nozzle-ci-{args.short_sha}-blender{args.blender_version}-{args.platform}-{pyabi}"

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{package_name}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        package_root = Path(tmp) / ADDON
        package_root.mkdir()
        source_addon = ROOT / ADDON
        for name in REQUIRED_PY:
            shutil.copy2(source_addon / name, package_root / name)
        shutil.copy2(native, package_root / native.name)
        shutil.copy2(ROOT / "LICENSE", package_root / "LICENSE")
        shutil.copy2(ROOT / "README.md", package_root / "README.md")
        zip_dir(package_root, zip_path)

    verify_zip(zip_path, native.name)
    print(f"package={zip_path}")
    print(f"package_name={zip_path.name}")
    print(f"native={native.name}")
    print(f"pyabi={pyabi}")


if __name__ == "__main__":
    main()
