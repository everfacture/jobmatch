"""Shared title/phrase matching helpers.

Both the JobSpy path and the smart-extract API path must apply the same
`exclude_titles` rules. They used to live in `jobspy.py`, which meant every
smart-extract source (Telegram, Dealls, HHRMA) silently bypassed them and
stored supervisor/intern/field-sales rows that the profile had explicitly
excluded. Keep the implementation here so there is exactly one matcher.
"""

import re


def normalise_phrase(text: str | None) -> str:
    """Normalise text for phrase-level matching.

    Lowercases and collapses every non-alphanumeric run to a single space, so
    `entry-level` and `entry level` compare equal.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def title_excluded(title: str | None, exclude_titles: list[str] | None) -> bool:
    """Return true when a title contains a configured excluded phrase.

    Matching is on whole words, so `intern` does not match `international`
    while `site supervisor` still matches a bare `supervisor`.
    """
    if not title or not exclude_titles:
        return False

    title_norm = f" {normalise_phrase(title)} "
    for phrase in exclude_titles:
        phrase_norm = normalise_phrase(phrase)
        if phrase_norm and f" {phrase_norm} " in title_norm:
            return True
    return False
