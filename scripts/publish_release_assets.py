#!/usr/bin/env python3
"""Validate and publish blender-nozzle release assets."""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from release_policy import PLATFORMS, is_exact_semver_tag


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(args))
    result = subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def release_exists(repo: str, tag: str) -> bool:
    result = run(["gh", "release", "view", tag, "--repo", repo], check=False)
    if result.returncode == 0:
        return True
    combined = result.stdout + result.stderr
    if "release not found" in combined.lower() or "not found" in combined.lower():
        return False
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    raise SystemExit(result.returncode)


def release_assets(repo: str, tag: str) -> dict[str, str]:
    if not release_exists(repo, tag):
        return {}
    result = run([
        "gh",
        "release",
        "view",
        tag,
        "--repo",
        repo,
        "--json",
        "assets",
        "--jq",
        '.assets[] | [.name, (.id|tostring)] | @tsv',
    ])
    assets: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, asset_id = line.split("\t", 1)
        assets[name] = asset_id
    return assets


def validate_ref(args: argparse.Namespace) -> tuple[str, str]:
    if args.event_name == "push" and args.github_ref == "refs/heads/main":
        return "latest", "latest"
    if args.event_name == "push" and args.github_ref.startswith("refs/tags/v"):
        if not is_exact_semver_tag(args.github_ref_name):
            fail(f"invalid release tag {args.github_ref_name!r}; expected exact vX.Y.Z")
        return "versioned", args.github_ref_name
    if args.event_name == "workflow_dispatch" and args.dry_run:
        if args.channel not in {"latest", "versioned"}:
            fail("dry-run workflow_dispatch requires --channel latest or versioned")
        if args.channel == "versioned":
            if not is_exact_semver_tag(args.tag):
                fail(f"invalid dry-run tag {args.tag!r}; expected exact vX.Y.Z")
            return "versioned", args.tag
        return "latest", "latest"
    fail(f"unsupported release context: event={args.event_name} ref={args.github_ref}")


def expected_name(channel: str, tag: str, short_sha: str, blender_series: str, platform: str, pyabi: str) -> str:
    if channel == "latest":
        return f"blender-nozzle-latest-{short_sha}-blender{blender_series}-{platform}-{pyabi}.zip"
    return f"blender-nozzle-{tag}-blender{blender_series}-{platform}-{pyabi}.zip"


def validate_artifacts(artifacts_dir: Path, channel: str, tag: str, args: argparse.Namespace) -> list[Path]:
    zip_paths = sorted(path for path in artifacts_dir.rglob("*.zip") if path.is_file())
    if not zip_paths:
        fail(f"no zip artifacts found under {artifacts_dir}")

    by_name: dict[str, Path] = {}
    for path in zip_paths:
        name = path.name
        if name.startswith("blender-nozzle-ci-"):
            fail(f"CI artifact is not publishable: {name}")
        if name in by_name:
            fail(f"duplicate zip basename: {name}")
        by_name[name] = path

    expected = [
        expected_name(channel, tag, args.short_sha, args.blender_series, platform, args.pyabi)
        for platform in PLATFORMS
    ]
    extra = sorted(set(by_name) - set(expected))
    missing = sorted(set(expected) - set(by_name))
    if extra:
        fail("unexpected zip artifact(s): " + ", ".join(extra))
    if missing:
        fail("missing zip artifact(s): " + ", ".join(missing))

    return [by_name[name] for name in expected]


def write_notes(path: Path, channel: str, tag: str, args: argparse.Namespace, asset_names: list[str]) -> None:
    title = "blender-nozzle latest" if channel == "latest" else f"blender-nozzle {tag}"
    lines = [
        title,
        "",
        f"Commit: `{args.github_sha}`",
        f"Blender version: `{args.blender_version}`",
        f"Blender series: `blender{args.blender_series}`",
        f"Python ABI: `{args.pyabi}`",
        "Platforms:",
    ]
    lines.extend(f"- `{platform}`" for platform in PLATFORMS)
    lines.append("Assets:")
    lines.extend(f"- `{name}`" for name in asset_names)
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def delete_latest_assets(repo: str, dry_run: bool) -> None:
    assets = release_assets(repo, "latest")
    targets = {name: asset_id for name, asset_id in assets.items() if fnmatch.fnmatch(name, "blender-nozzle-latest-*.zip")}
    for name, asset_id in sorted(targets.items()):
        if dry_run:
            print(f"dry-run: would delete latest asset {name} id={asset_id}")
        else:
            run(["gh", "api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}"])


def ensure_release(repo: str, tag: str, title: str, notes_file: Path, prerelease: bool, dry_run: bool) -> None:
    exists = release_exists(repo, tag)
    if dry_run:
        action = "edit" if exists else "create"
        print(f"dry-run: would {action} release {tag} title={title!r} prerelease={prerelease}")
        return
    if exists:
        cmd = ["gh", "release", "edit", tag, "--repo", repo, "--title", title, "--notes-file", str(notes_file)]
        if prerelease:
            cmd.append("--prerelease")
        run(cmd)
    else:
        cmd = ["gh", "release", "create", tag, "--repo", repo, "--title", title, "--notes-file", str(notes_file)]
        if prerelease:
            cmd.append("--prerelease")
        run(cmd)


def upload_assets(repo: str, tag: str, paths: list[Path], dry_run: bool) -> None:
    if dry_run:
        for path in paths:
            print(f"dry-run: would upload {path.name} to {tag}")
        return
    run(["gh", "release", "upload", tag, "--repo", repo, *[str(path) for path in paths]])


def publish(channel: str, tag: str, paths: list[Path], args: argparse.Namespace) -> None:
    asset_names = [path.name for path in paths]
    with tempfile.TemporaryDirectory() as tmp:
        notes_file = Path(tmp) / "release-notes.md"
        write_notes(notes_file, channel, tag, args, asset_names)
        print(notes_file.read_text(encoding="utf-8"))

        if channel == "latest":
            ensure_release(args.repo, "latest", "blender-nozzle latest", notes_file, True, args.dry_run)
            delete_latest_assets(args.repo, args.dry_run)
            upload_assets(args.repo, "latest", paths, args.dry_run)
            return

        existing = release_assets(args.repo, tag)
        conflicts = sorted(name for name in asset_names if name in existing)
        if conflicts:
            fail("target release already has asset(s): " + ", ".join(conflicts))
        ensure_release(args.repo, tag, f"blender-nozzle {tag}", notes_file, False, args.dry_run)
        upload_assets(args.repo, tag, paths, args.dry_run)


def normalize(paths: list[Path]) -> list[Path]:
    staging = Path("release-staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    normalized: list[Path] = []
    for path in paths:
        target = staging / path.name
        shutil.copy2(path, target)
        normalized.append(target)
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--github-ref", required=True)
    parser.add_argument("--github-ref-name", required=True)
    parser.add_argument("--github-sha", required=True)
    parser.add_argument("--short-sha", required=True)
    parser.add_argument("--blender-version", required=True)
    parser.add_argument("--blender-series", required=True)
    parser.add_argument("--pyabi", required=True)
    parser.add_argument("--channel", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-context-only", action="store_true")
    args = parser.parse_args()

    channel, tag = validate_ref(args)
    print(f"release_channel={channel}")
    print(f"release_tag={tag}")
    if args.validate_context_only:
        return
    if args.artifacts_dir is None:
        fail("--artifacts-dir is required unless --validate-context-only is used")
    paths = validate_artifacts(args.artifacts_dir, channel, tag, args)
    normalized = normalize(paths)
    print("validated assets:")
    for path in normalized:
        print(f"- {path.name}")
    publish(channel, tag, normalized, args)


if __name__ == "__main__":
    main()
