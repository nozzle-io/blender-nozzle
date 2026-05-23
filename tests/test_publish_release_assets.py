from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_release_assets  # noqa: E402


class PublishReleaseAssetsTests(unittest.TestCase):
    def run_main(self, args: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                publish_release_assets.main(args)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                return code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def make_versioned_artifacts(self, artifacts_dir: Path, tag: str = "v1.2.3") -> None:
        for platform in publish_release_assets.PLATFORMS:
            subdir = artifacts_dir / platform
            subdir.mkdir(parents=True)
            name = f"blender-nozzle-{tag}-blender4.3-{platform}-py311.zip"
            (subdir / name).write_bytes(b"fixture")

    def base_args(self, artifacts_dir: Path, tag: str = "v1.2.3") -> list[str]:
        return [
            "--artifacts-dir",
            str(artifacts_dir),
            "--repo",
            "nozzle-io/blender-nozzle",
            "--event-name",
            "workflow_dispatch",
            "--github-ref",
            "refs/heads/main",
            "--github-ref-name",
            "main",
            "--github-sha",
            "0123456789abcdef",
            "--short-sha",
            "0123456",
            "--blender-version",
            "4.3.2",
            "--blender-series",
            "4.3",
            "--pyabi",
            "py311",
            "--channel",
            "versioned",
            "--tag",
            tag,
            "--dry-run",
        ]

    def test_versioned_existing_asset_conflict_fails_before_release_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "work"
            artifacts_dir = Path(tmp) / "artifacts"
            work_dir.mkdir()
            artifacts_dir.mkdir()
            self.make_versioned_artifacts(artifacts_dir)
            conflict = "blender-nozzle-v1.2.3-blender4.3-macos-arm64-py311.zip"

            def unexpected_mutation(*_args, **_kwargs):
                raise AssertionError("release mutation must not run after an asset conflict")

            with mock.patch.object(publish_release_assets, "release_assets", return_value={conflict: "asset-id"}) as assets:
                with mock.patch.object(publish_release_assets, "ensure_release", side_effect=unexpected_mutation):
                    with mock.patch.object(publish_release_assets, "upload_assets", side_effect=unexpected_mutation):
                        with mock.patch.object(Path, "cwd", return_value=work_dir):
                            code, stdout, stderr = self.run_main(self.base_args(artifacts_dir))

            self.assertEqual(code, 1)
            self.assertIn("validated assets:", stdout)
            self.assertIn("target release already has asset(s): " + conflict, stderr)
            assets.assert_called_once_with("nozzle-io/blender-nozzle", "v1.2.3")

    def test_invalid_versioned_dry_run_tag_fails_before_artifact_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp) / "missing-artifacts"
            code, stdout, stderr = self.run_main(self.base_args(artifacts_dir, tag="v1.2.3-test"))

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("invalid dry-run tag 'v1.2.3-test'; expected exact vX.Y.Z", stderr)


if __name__ == "__main__":
    unittest.main()
