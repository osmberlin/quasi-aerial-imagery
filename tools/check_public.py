#!/usr/bin/env python3
"""Check that nothing private would be committed to this (public) repository.

Looks at every file git tracks or would add (tracked + untracked, minus .gitignore'd) and fails on
imagery and other data files, files over 5 MB, local absolute paths, account names, e-mail domains,
private tile servers, tokens and links into ignored internal documents.

    python3 tools/check_public.py          # exit code 1 and a list of problems if anything is found
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

DATA_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".vrt", ".ply", ".npz", ".pt", ".onnx", ".mp4", ".360",
                 ".36p", ".db", ".webp", ".heic", ".mov"}
# Synthetic test tiles of the viewer (gradients drawn by viewer/scripts/make-sample-data.ts), no imagery.
ALLOWED_DATA = re.compile(r"^viewer/public/data-sample/[^/]+/[^/]+/\d+_\d+\.png$")
MAX_BYTES = 5_000_000

FORBIDDEN = [
    (re.compile(r"/Users/|/home/[a-z]"), "absolute local path"),
    (re.compile(r"\btordans\b", re.I), "account name"),
    (re.compile(r"fixmycity\.de", re.I), "e-mail domain"),
    (re.compile(r"\btilda\b|tilda-geo", re.I), "private tile server"),
    (re.compile(r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}"), "credential"),
    (re.compile(r"password=admin"), "credential example"),
    (re.compile(r"\bTobias\b"), "personal name"),
    (re.compile(r"hotosm\.slack\.com|slack\.com/archives"), "link into a chat"),
    (re.compile(r"mapillary\.com/app/user/"), "link to a personal account"),
    (re.compile(r"\]\((?:\.\./)*(?:docs/)?internal/"), "link into docs/internal"),
    (re.compile(r"docs/history/chat-raw|agent-transcripts"), "link into chat logs"),
]
# (path, what) pairs that are fine: the rule itself has to name the patterns it looks for.
ALLOWED = {("tools/check_public.py", w) for _, w in FORBIDDEN}


def secret_values() -> list[str]:
    """Values from <workspace>/secrets.env (outside the repository), so a pasted token is caught without
    the check itself having to contain it."""
    env = REPO.parent / "secrets.env"
    if not env.exists():
        return []
    values = []
    for line in env.read_text().splitlines():
        _, _, v = line.partition("=")
        v = v.strip().strip("'\"")
        values += [v] + re.findall(r"[A-Za-z0-9]{8,}", v)
    return sorted({v for v in values if len(v) >= 8 and not v.startswith(("http", "EPSG"))}, key=len, reverse=True)


def candidate_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO,
                         check=True, capture_output=True).stdout.decode()
    return sorted(f for f in out.split("\0") if f and ((REPO / f).is_file() or (REPO / f).is_symlink()))


def check() -> list[str]:
    problems = []
    secrets = secret_values()
    for rel in candidate_files():
        f = REPO / rel
        if f.is_symlink():
            problems.append(f"{rel}: symbolic link (points to {f.readlink()})")
            continue
        if f.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: larger than 5 MB")
        if f.suffix.lower() in DATA_SUFFIXES and not ALLOWED_DATA.match(rel):
            problems.append(f"{rel}: imagery or data file")
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if any(v in text for v in secrets):
            problems.append(f"{rel}: contains a value from secrets.env")
        for pattern, what in FORBIDDEN:
            if (rel, what) in ALLOWED:
                continue
            for m in pattern.finditer(text):
                problems.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {what}")
    return problems


def main() -> int:
    problems = check()
    for p in problems:
        print("PRIVATE?", p)
    print(f"checked {len(candidate_files())} files:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
