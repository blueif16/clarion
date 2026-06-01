#!/usr/bin/env python3
"""
copy_lint.py — Clarion copy linter.

Scans files (or a --string argument) for banned words that frame the user as
helpless. Exits non-zero and reports offending lines if any are found.

Usage:
    python3 scripts/copy_lint.py [file ...]
    python3 scripts/copy_lint.py --string "some copy text to check"
    python3 scripts/copy_lint.py --string "some copy" [file ...]

No external dependencies.
"""

import re
import sys

# ---------------------------------------------------------------------------
# Banned patterns (case-insensitive, word-boundary aware where meaningful)
# Each entry: (display_name, compiled_pattern)
# ---------------------------------------------------------------------------
BANNED: list[tuple[str, re.Pattern]] = [
    ("assistant",      re.compile(r'\bassistant\b',       re.IGNORECASE)),
    ("helper",         re.compile(r'\bhelper\b',          re.IGNORECASE)),
    ("help you",       re.compile(r'\bhelp\s+you\b',      re.IGNORECASE)),
    ("let me help",    re.compile(r'\blet\s+me\s+help\b', re.IGNORECASE)),
    ("I'll take care of it", re.compile(r"i'?ll\s+take\s+care\s+of\s+it", re.IGNORECASE)),
    ("don't worry",    re.compile(r"don'?t\s+worry",      re.IGNORECASE)),
    ("I've got you",   re.compile(r"i'?ve\s+got\s+you",   re.IGNORECASE)),
    ("assist",         re.compile(r'\bassist(?:ance|ed|ing)?\b', re.IGNORECASE)),
    ("make it easier for you", re.compile(r'\bmake\s+it\s+easier\s+for\s+you\b', re.IGNORECASE)),
    ("I can help with that",   re.compile(r"\bi\s+can\s+help\s+with\s+that\b",   re.IGNORECASE)),
]


def check_lines(source_name: str, lines: list[str]) -> list[str]:
    """Return a list of human-readable findings for the given source."""
    findings: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        for label, pattern in BANNED:
            if pattern.search(line):
                findings.append(
                    f"  {source_name}:{lineno}: [{label}] {line.rstrip()}"
                )
    return findings


def check_string(text: str, source_name: str = "<string>") -> list[str]:
    return check_lines(source_name, text.splitlines(keepends=True))


def check_file(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return check_lines(path, fh.readlines())
    except OSError as exc:
        return [f"  ERROR reading {path}: {exc}"]


def main() -> int:
    args = sys.argv[1:]
    inline_string: str | None = None
    file_paths: list[str] = []

    # Parse --string flag
    i = 0
    while i < len(args):
        if args[i] == "--string" and i + 1 < len(args):
            inline_string = args[i + 1]
            i += 2
        else:
            file_paths.append(args[i])
            i += 1

    if inline_string is None and not file_paths:
        print(
            "Usage: copy_lint.py [--string TEXT] [file ...]\n"
            "  Scans for banned helpless-framing words in Clarion copy.\n"
            "  Exits 0 if clean, 1 if violations found.",
            file=sys.stderr,
        )
        return 2

    all_findings: list[str] = []

    if inline_string is not None:
        all_findings.extend(check_string(inline_string, "<string>"))

    for path in file_paths:
        all_findings.extend(check_file(path))

    if all_findings:
        print("FAIL — banned copy found:")
        for f in all_findings:
            print(f)
        return 1

    print("PASS — no banned copy found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
