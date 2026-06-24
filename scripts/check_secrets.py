#!/usr/bin/env python3
"""Secret scanner — guard against committing real credentials.

This is the automated enforcement for ``docs/runbooks/secrets_management.md``. It is
intentionally dependency-free (standard library only) so it runs in pre-commit hooks and
CI without setup.

Usage::

    python scripts/check_secrets.py            # scan staged files (pre-commit mode)
    python scripts/check_secrets.py --all      # scan all git-tracked files (CI mode)
    python scripts/check_secrets.py PATH ...   # scan specific files/paths

Exit code is non-zero when a likely real secret is found.

The scanner is conservative about real secrets and lenient about obvious placeholders.
Mark an intentional dummy/test value with an inline ``# pragma: allowlist secret``
comment to suppress a finding on that line.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# Inline marker to suppress a single line (detect-secrets compatible).
ALLOWLIST_MARKER = "pragma: allowlist secret"

# Files/dirs that never contain real secrets, or legitimately contain example patterns.
SKIP_PATH_PARTS = {
    ".git",
    "node_modules",
    "dist",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
SKIP_PATH_SUBSTRINGS = (
    "docs/latex/build/",
    "docs/runbooks/secrets_management.md",  # documents the patterns themselves
    "scripts/check_secrets.py",  # this file
)
SKIP_SUFFIXES = (
    ".lock",
    ".min.js",
    ".map",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".aux",
    ".bbl",
    ".fls",
    ".fdb_latexmk",
    ".toc",
    ".out",
)
SKIP_NAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "pdm.lock"}

# Config-style files where an unquoted value (KEY=value) is a real literal secret.
CONFIG_SUFFIXES = (".env", ".yaml", ".yml", ".ini", ".toml", ".cfg", ".conf", ".properties")

# A file is treated as an example/template if its name contains one of these.
EXAMPLE_MARKERS = (".example", ".sample", ".template", "example.")

# Substrings (case-insensitive) that mark a value as a non-secret placeholder.
PLACEHOLDER_TOKENS = (
    "example",
    "placeholder",
    "change_me",
    "changeme",
    "your_",
    "your-",
    "yourpassword",
    "dummy",
    "fake",
    "sample",
    "test",
    "dev",
    "local",
    "redacted",
    "todo",
    "none",
    "null",
    "xxxx",
    "secret_key_env",
    "notarealsecret",
)
# Indicators the value is code/indirection, not a literal secret.
INDIRECTION_TOKENS = (
    "os.environ",
    "os.getenv",
    "getenv",
    "getpass",
    "field(",
    "settings.",
    "config.",
    "env(",
    "_env",
    "${",
    "{{",
    "<",
    ">",
    "(",
)

# High-confidence provider/key patterns. A match here is reported regardless of
# placeholder heuristics (but still honors the inline allowlist marker).
HIGH_CONFIDENCE = [
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?!test)[A-Za-z0-9]{32,}\b")),
    ("Generic bearer JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]

# Generic "secret-like assignment": KEY <sep> VALUE. The value is validated against the
# placeholder/indirection heuristics before being reported.
KEY_NAMES = (
    r"pass(?:wd|word)?|secret|api[_-]?key|access[_-]?key|secret[_-]?key|"
    r"auth[_-]?token|client[_-]?secret|private[_-]?key|bearer"
)
# An optional snake_case prefix lets `DB_PASSWORD`, `access_token`, `aws_secret_key`,
# etc. match (the key core must still be immediately followed by `:`/`=`, so
# `password_hash =` / `tokenizer =` do NOT match).
ASSIGNMENT_RE = re.compile(
    rf"(?i)\b(?:[a-z0-9]+_)*({KEY_NAMES})\s*[:=]\s*"
    r"""(?P<q>['"]?)(?P<val>[^'"\s,;)#]{6,})(?P=q)"""
)


def is_placeholder(value: str) -> bool:
    """Return True if ``value`` looks like a deliberate placeholder/dummy/indirection."""
    low = value.lower()
    if any(tok in low for tok in PLACEHOLDER_TOKENS):
        return True
    if any(tok in low for tok in INDIRECTION_TOKENS):
        return True
    if len(set(value)) <= 2:  # e.g. "xxxxxx", "******"
        return True
    return False


def should_skip(path: Path) -> bool:
    text = path.as_posix()
    if any(part in SKIP_PATH_PARTS for part in path.parts):
        return True
    if any(sub in text for sub in SKIP_PATH_SUBSTRINGS):
        return True
    if path.name in SKIP_NAMES:
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def is_example_file(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in EXAMPLE_MARKERS)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return a list of (line_no, kind, snippet) findings for ``path``."""
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw:  # binary
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []

    example = is_example_file(path)
    # In config files (env/yaml/ini/...), unquoted literal values are real secrets.
    # In source files, a hardcoded secret is a *quoted* string literal; an unquoted
    # value (e.g. `password=payload.password`, `token=os.environ[...]`) is a code
    # reference, not a literal — so only flag quoted values there.
    is_config = path.suffix.lower() in CONFIG_SUFFIXES or path.name.startswith(".env")
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOWLIST_MARKER in line:
            continue
        for kind, pattern in HIGH_CONFIDENCE:
            if pattern.search(line):
                findings.append((lineno, kind, line.strip()[:160]))
        for m in ASSIGNMENT_RE.finditer(line):
            value = m.group("val")
            if is_placeholder(value):
                continue
            # In example/template files, unquoted simple values are expected placeholders.
            if example:
                continue
            # Source files: only a quoted string literal can be a hardcoded secret.
            if not is_config and not m.group("q"):
                continue
            findings.append((lineno, f"hardcoded {m.group(1).lower()}", line.strip()[:160]))
    return findings


def git_files(staged: bool, all_tracked: bool) -> list[Path]:
    if staged:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    else:
        cmd = ["git", "ls-files"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [Path(p) for p in out.splitlines() if p]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for committed secrets.")
    parser.add_argument("--all", action="store_true", help="scan all git-tracked files")
    parser.add_argument("paths", nargs="*", help="specific files/paths to scan")
    args = parser.parse_args()

    if args.paths:
        candidates = [Path(p) for p in args.paths]
    elif args.all:
        candidates = git_files(staged=False, all_tracked=True)
    else:
        candidates = git_files(staged=True, all_tracked=False)
        if not candidates:  # nothing staged → fall back to all tracked files
            candidates = git_files(staged=False, all_tracked=True)

    findings: list[tuple[Path, int, str, str]] = []
    for path in candidates:
        if not path.is_file() or should_skip(path):
            continue
        for lineno, kind, snippet in scan_file(path):
            findings.append((path, lineno, kind, snippet))

    if findings:
        print("✖ Potential secrets detected — see docs/runbooks/secrets_management.md\n")
        for path, lineno, kind, snippet in findings:
            print(f"  {path}:{lineno}: {kind}")
            print(f"      {snippet}")
        print(
            "\nIf a finding is a deliberate placeholder/test value, make it clearly fake "
            "or add '# pragma: allowlist secret' to that line.\n"
            "If it is real: remove it, route it through .env / the environment, and rotate it."
        )
        return 1

    print("✓ No secrets detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
