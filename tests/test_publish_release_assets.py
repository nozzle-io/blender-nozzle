from __future__ import annotations

import contextlib
import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_release_assets  # noqa: E402


class PublishReleaseAssetsTests(unittest.TestCase):
    def exercise_latest(self, *, fault: str = "", dry_run: bool = False) -> list[list[str]]:
        target = "a" * 40
        old_ref = "b" * 40
        commands: list[list[str]] = []
        notes = ""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blender-nozzle-latest-aaaaaaa-blender4.3-macos-arm64-py311.zip"
            path.write_bytes(b"exact local package")
            args = argparse.Namespace(repo="nozzle-io/blender-nozzle", github_sha=target,
                                      blender_version="4.3.2", blender_series="4.3",
                                      pyabi="py311", dry_run=dry_run)

            def fake_run(command: list[str], *, check: bool = True):
                nonlocal notes
                commands.append(command)
                output = ""
                if command[:3] == ["gh", "release", "view"]:
                    if "body,assets" in command:
                        assets = [] if fault == "missing_asset" else [{"name": path.name}]
                        output = json.dumps({"body": "concurrent notes" if fault == "notes" else notes,
                                             "assets": assets})
                    elif "assets" in command:
                        output = ""
                elif command[:3] == ["gh", "release", "edit"]:
                    notes = Path(command[command.index("--notes-file") + 1]).read_text()
                elif command[:3] == ["gh", "release", "upload"]:
                    if fault == "upload":
                        raise SystemExit(9)
                elif command[:3] == ["gh", "release", "download"]:
                    data = b"wrong uploaded bytes" if fault == "bytes" else path.read_bytes()
                    (Path(command[command.index("--dir") + 1]) / path.name).write_bytes(data)
                elif command[:2] == ["gh", "api"]:
                    if command[2].endswith("/git/ref/tags/latest"):
                        output = json.dumps({"object": {"sha": old_ref}})
                    elif command[2].endswith("/commits/latest"):
                        output = json.dumps({"sha": old_ref if fault == "readback" else target})
                    elif command[2].endswith("/commits/main"):
                        output = json.dumps({"sha": old_ref if fault == "stale" else target})
                    else:
                        self.fail(f"Unexpected API request: {command}")
                elif command[:2] == ["git", "push"]:
                    self.assertIn(f"--force-with-lease=refs/tags/latest:{old_ref}", command)
                    self.assertEqual(command[-1], f"{target}:refs/tags/latest")
                    if fault == "lease":
                        raise SystemExit(1)
                else:
                    self.fail(f"Unexpected command: {command}")
                return subprocess.CompletedProcess(command, 0, output, "")

            with mock.patch.object(publish_release_assets, "run", side_effect=fake_run):
                if fault:
                    with self.assertRaises(SystemExit):
                        publish_release_assets.publish("latest", "latest", [path], args)
                else:
                    publish_release_assets.publish("latest", "latest", [path], args)
        return commands

    def test_latest_moves_only_after_uploaded_bytes_are_verified(self) -> None:
        commands = self.exercise_latest()
        upload = next(i for i, command in enumerate(commands) if command[:3] == ["gh", "release", "upload"])
        download = next(i for i, command in enumerate(commands) if command[:3] == ["gh", "release", "download"])
        push = next(i for i, command in enumerate(commands) if command[:2] == ["git", "push"])
        self.assertLess(upload, download)
        self.assertLess(download, push)
        self.assertEqual(commands[-1], ["gh", "api", "repos/nozzle-io/blender-nozzle/commits/latest"])

    def test_latest_does_not_move_on_upload_or_verification_failure(self) -> None:
        for fault in ["upload", "missing_asset", "notes", "bytes"]:
            with self.subTest(fault=fault):
                commands = self.exercise_latest(fault=fault)
                self.assertFalse(any(command[:2] == ["git", "push"] for command in commands))

    def test_latest_rejects_stale_run_before_mutating_release(self) -> None:
        commands = self.exercise_latest(fault="stale")
        self.assertEqual(commands, [["gh", "api", "repos/nozzle-io/blender-nozzle/commits/main"]])

    def test_latest_does_not_suppress_lease_or_readback_failure(self) -> None:
        for fault in ["lease", "readback"]:
            with self.subTest(fault=fault):
                commands = self.exercise_latest(fault=fault)
                self.assertEqual(sum(command[:2] == ["git", "push"] for command in commands), 1)

    def test_latest_dry_run_never_pushes_or_mutates_release(self) -> None:
        commands = self.exercise_latest(dry_run=True)
        self.assertTrue(commands)
        self.assertTrue(all(command[:3] == ["gh", "release", "view"] for command in commands))

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
