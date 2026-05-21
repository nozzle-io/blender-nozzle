"""Install and enable a blender-nozzle zip inside Blender."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def fail(message: str) -> None:
    print(f"NOZZLE_BLENDER_SMOKE_FAIL {message}", file=sys.stderr)
    raise SystemExit(1)


def addon_zip_from_argv() -> Path:
    if "--" not in sys.argv:
        fail("missing -- <zip> argument")
    rest = sys.argv[sys.argv.index("--") + 1 :]
    if len(rest) != 1:
        fail("expected exactly one zip path after --")
    path = Path(rest[0]).resolve()
    if not path.is_file():
        fail(f"zip does not exist: {path}")
    return path


def main() -> None:
    zip_path = addon_zip_from_argv()
    bpy.ops.preferences.addon_install(filepath=str(zip_path), overwrite=True)
    bpy.ops.preferences.addon_enable(module="nozzle_blender")
    if "nozzle_blender" not in bpy.context.preferences.addons:
        fail("nozzle_blender not present in enabled addons")

    import nozzle_blender
    from nozzle_blender import _nozzle_native

    constants = _nozzle_native.get_format_constants()
    if "RGBA8_UNORM" not in constants:
        fail("native format constants missing RGBA8_UNORM")

    print(f"NOZZLE_BLENDER_SMOKE_OK zip={zip_path}")
    print(f"NOZZLE_BLENDER_VERSION {bpy.app.version_string}")
    print(f"NOZZLE_PYTHON_VERSION {sys.version.split()[0]}")
    print(f"NOZZLE_ADDON_MODULE {nozzle_blender.__file__}")


if __name__ == "__main__":
    main()
