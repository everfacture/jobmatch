#!/usr/bin/env python3
"""Block personal data from reaching the public repo.

Scans tracked files, and commit messages of outgoing commits, for:
  - email addresses outside reserved example domains
  - phone numbers, Telegram chat ids, bot tokens, API key prefixes
  - a private denylist: identity fields read from every local profile.json
    (names, email, phone, profile URLs), chat/thread ids read from local .env
    files, plus extra terms in ~/.jobmatch/privacy-denylist.txt (one per line,
    e.g. home city, employer names). None of that ever lives in the repo.

Matched values are never printed, only file:line and the rule name.

    python scripts/privacy_check.py              # tracked files + outgoing commits
    python scripts/privacy_check.py --history    # also every commit message on HEAD

Exit 1 on any finding. CI runs it without the private denylist (patterns only);
the pre-push hook (scripts/install-hooks.sh) runs it with the denylist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EXAMPLE_EMAIL_DOMAINS = re.compile(
    r"@(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|example|test|invalid|localhost)\b", re.I
)
PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "telegram-chat-id": re.compile(r"(?<![\w-])-100\d{8,}\b"),
    "bot-token": re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}"),
    "api-key": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,})"),
    "phone": re.compile(r"(?<![\w.])(?:\+62|\+44|\+65|\+971|\+852|\b08)[\d -]{8,14}\d\b"),
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".lock", ".svg"}
IDENTITY_FIELDS = ("full_name", "preferred_name", "email", "phone", "linkedin_url", "github_url", "portfolio_url")
ENV_ID_KEYS = re.compile(r"^\s*(?:export\s+)?[A-Z_]*(?:CHAT_ID|THREAD_ID)\s*=\s*['\"]?(-?\d{5,})", re.M)
MIN_TERM = 4


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _runtime_dir() -> Path:
    return Path(os.environ.get("JOBMATCH_DIR") or Path.home() / ".jobmatch")


def _identity_terms(value: str, field: str) -> set[str]:
    value = value.strip()
    if not value:
        return set()
    if field.endswith("_url"):
        slug = value.rstrip("/").rsplit("/", 1)[-1]
        return {slug} if len(slug) >= MIN_TERM else set()
    if field == "phone":
        digits = re.sub(r"\D", "", value)
        return {digits[-8:]} if len(digits) >= 8 else set()
    terms = {value}
    if field in ("full_name", "preferred_name"):
        terms |= {w for w in re.split(r"\s+", value) if len(w) >= MIN_TERM}
    return terms


def load_denylist() -> tuple[set[str], int]:
    """Return (terms, source_count). Terms come only from local private files."""
    base = _runtime_dir()
    terms: set[str] = set()
    sources = 0
    for profile in [base / "profile.json", *sorted(base.glob("profiles/*/profile.json"))]:
        try:
            personal = json.loads(profile.read_text(encoding="utf-8")).get("personal") or {}
        except (OSError, ValueError):
            continue
        sources += 1
        for field in IDENTITY_FIELDS:
            terms |= _identity_terms(str(personal.get(field) or ""), field)
    for env in [base / ".env", *sorted(base.glob("profiles/*/.env"))]:
        try:
            text = env.read_text(encoding="utf-8")
        except OSError:
            continue
        sources += 1
        terms |= {m.lstrip("-") for m in ENV_ID_KEYS.findall(text)}
    extra = base / "privacy-denylist.txt"
    if extra.exists():
        sources += 1
        for line in extra.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if len(line) >= MIN_TERM:
                terms.add(line)
    return terms - _public_handles(), sources


def _public_handles() -> set[str]:
    """The repo owner's handle is in every clone URL; it is public by design."""
    try:
        url = _git("remote", "get-url", "origin").strip()
    except (subprocess.CalledProcessError, OSError):
        return set()
    match = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?$", url)
    return {match.group(1)} if match else set()


def _denylist_regex(terms: set[str]) -> re.Pattern | None:
    if not terms:
        return None
    parts = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![A-Za-z0-9])", re.I)


def scan_text(text: str, deny: re.Pattern | None) -> list[tuple[int, str]]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if rule == "email" and EXAMPLE_EMAIL_DOMAINS.search(match.group(0)):
                    continue
                hits.append((lineno, rule))
        if deny and deny.search(line):
            hits.append((lineno, "denylist"))
    return hits


def tracked_files() -> list[Path]:
    paths = [Path(p) for p in _git("ls-files", "-z").split("\0") if p]
    this = Path(__file__).resolve()
    return [p for p in paths if p.suffix.lower() not in SKIP_SUFFIXES and p.resolve() != this and p.is_file()]


def outgoing_range() -> str | None:
    try:
        upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip()
    except subprocess.CalledProcessError:
        return None
    return f"{upstream}..HEAD"


def commit_messages(rev_range: str) -> list[tuple[str, str]]:
    out = _git("log", "--format=%h%x00%B%x01", rev_range)
    msgs = []
    for chunk in out.split("\x01"):
        if "\0" in chunk:
            sha, body = chunk.strip("\n").split("\0", 1)
            msgs.append((sha, body))
    return msgs


def main() -> int:
    parser = argparse.ArgumentParser(description="Block personal data from reaching the public repo.")
    parser.add_argument("--history", action="store_true", help="scan every commit message reachable from HEAD")
    parser.add_argument("--no-denylist", action="store_true", help="patterns only (what CI runs)")
    args = parser.parse_args()

    terms, sources = (set(), 0) if args.no_denylist else load_denylist()
    deny = _denylist_regex(terms)
    findings: list[str] = []

    files = tracked_files()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings += [f"{path}:{line}: {rule}" for line, rule in scan_text(text, deny)]

    rev_range = "HEAD" if args.history else outgoing_range()
    messages = commit_messages(rev_range) if rev_range else []
    for sha, body in messages:
        findings += [f"commit {sha} message line {line}: {rule}" for line, rule in scan_text(body, deny)]

    print(
        f"privacy_check: {len(files)} files, {len(messages)} commit messages "
        f"({rev_range or 'no upstream'}), {len(terms)} private terms from {sources} local sources"
    )
    for finding in findings:
        print("  " + finding)
    if findings:
        print(f"privacy_check: FAIL ({len(findings)} findings). Values are not printed; open the lines locally.")
        return 1
    print("privacy_check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
