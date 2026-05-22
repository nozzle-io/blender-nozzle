#!/usr/bin/env python3
"""Run a command with a timeout and preserve its output/exit status."""

from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("timeout_seconds", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("error: missing command", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = subprocess.run(command, timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired:
        print(f"error: command timed out after {args.timeout_seconds:g}s: {command}", file=sys.stderr)
        raise SystemExit(124)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
