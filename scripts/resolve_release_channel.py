#!/usr/bin/env python3
"""Resolve blender-nozzle packaging channel from GitHub Actions context."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from release_policy import bool_value, is_exact_semver_tag


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def append_env(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def resolve(args: argparse.Namespace) -> dict[str, str]:
    event_name = args.event_name
    ref = args.ref
    ref_name = args.ref_name
    release_dry_run = bool_value(args.release_dry_run)

    channel = "ci"
    tag = ""

    if event_name == "push" and ref == "refs/heads/main":
        channel = "latest"
    elif event_name == "push" and ref.startswith("refs/tags/v"):
        if is_exact_semver_tag(ref_name):
            channel = "versioned"
            tag = ref_name
        else:
            channel = "ci"
    elif event_name == "workflow_dispatch" and release_dry_run:
        requested = args.dry_run_channel
        if requested not in {"latest", "versioned"}:
            fail("workflow_dispatch dry-run requires --dry-run-channel latest or versioned")
        channel = requested
        if channel == "versioned":
            if not is_exact_semver_tag(args.dry_run_tag):
                fail("workflow_dispatch versioned dry-run requires exact semver --dry-run-tag like v1.2.3")
            tag = args.dry_run_tag

    return {
        "NOZZLE_RELEASE_CHANNEL": channel,
        "NOZZLE_RELEASE_TAG": tag,
        "NOZZLE_RELEASE_DRY_RUN": "true" if release_dry_run else "false",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--release-dry-run", default="false")
    parser.add_argument("--dry-run-channel", default="")
    parser.add_argument("--dry-run-tag", default="")
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args()

    values = resolve(args)
    if args.github_env:
        append_env(args.github_env, values)
    for key, value in values.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
