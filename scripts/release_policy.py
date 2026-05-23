"""Shared release policy helpers for blender-nozzle CI."""

from __future__ import annotations

import re

SEMVER_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
PLATFORMS = ("macos-arm64", "linux-x64", "windows-x64")


def is_exact_semver_tag(tag: str) -> bool:
    return SEMVER_RE.fullmatch(tag) is not None


def bool_value(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
