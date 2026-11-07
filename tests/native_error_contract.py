"""Check the built extension's exception metadata, not GPU timeout behavior."""

import argparse
import importlib.util
from pathlib import Path
import sysconfig
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise RuntimeError("build Python must provide EXT_SUFFIX")
    candidates = [
        directory / f"_nozzle_native{suffix}"
        for directory in (args.build_dir, args.build_dir / "Release")
        if (directory / f"_nozzle_native{suffix}").is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"expected exactly one built extension, found {candidates}")
    module_path = candidates[0].resolve()
    spec = importlib.util.spec_from_file_location("_nozzle_native", module_path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)

    check = unittest.TestCase()
    check.assertIs(type(native.ERROR_TIMEOUT), int)
    check.assertEqual(native.ERROR_TIMEOUT, 10)
    # Empty names are rejected by core validation before connection/GPU setup.
    # This exercises the same check_error helper used by sender publication.
    try:
        handle = native.create_receiver(name="")
    except RuntimeError as error:
        check.assertIs(type(error), RuntimeError)
        check.assertEqual(str(error), "invalid_argument")
        check.assertIs(type(error.code), int)
        check.assertEqual(error.code, 2)
    else:
        native.destroy_receiver(handle)
        check.fail("empty receiver name unexpectedly succeeded")
    print(f"PASS native exception metadata (no GPU contention): {module_path}")


if __name__ == "__main__":
    main()
