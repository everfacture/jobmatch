"""Config-driven deterministic first-pass scoring rules.

These rules are intentionally conservative. They replace LLM calls only when the
job is a clear yes/no from the user's preferences. Ambiguous roles still go to
the LLM scorer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from jobmatch.config.preferences import scoring_preferences


@dataclass(frozen=True)
class RuleScore:
    """Structured deterministic scoring result."""

    score: int
    fit: str = ""
    gap: str = ""
    keywords: str = ""
    rule: str = ""

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "fit": self.fit,
            "gap": self.gap,
            "keywords": self.keywords,
            "error": None,
            "scoring_method": f"rule:{self.rule}" if self.rule else "rule",
        }


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE) is not None


def _matched_phrases(text: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if _contains_phrase(text, phrase)]


def _role_match(title: str, roles: list[str]) -> str | None:
    for role in roles:
        if _contains_phrase(title, role):
            return role
    return None


def _hard_caps(prefs: dict[str, Any]) -> list[dict[str, Any]]:
    caps = prefs.get("hard_caps")
    return caps if isinstance(caps, list) else []


def apply_configured_hard_caps(result: dict, job: dict, preferences: dict[str, Any] | None) -> dict:
    """Apply configured max-score caps to an LLM/rule result."""
    score = result.get("score")
    if score is None:
        return result

    prefs = scoring_preferences(preferences)
    if not prefs:
        return result

    text = _job_text(job)
    for cap in _hard_caps(prefs):
        if not isinstance(cap, dict):
            continue
        patterns = _items(cap.get("patterns"))
        matched = _matched_phrases(text, patterns)
        if not matched:
            continue
        try:
            max_score = int(cap.get("max_score", score))
        except (TypeError, ValueError):
            continue
        if score > max_score:
            capped = dict(result)
            capped["score"] = max(1, min(10, max_score))
            cap_name = cap.get("name") or ", ".join(matched[:2])
            note = f"hard capped by preference '{cap_name}'"
            capped["gap"] = (capped.get("gap", "") + f" [{note}]").strip()
            return capped
    return result


def _job_text(job: dict) -> str:
    return _clean(" ".join([
        str(job.get("title") or ""),
        str(job.get("company") or ""),
        str(job.get("location") or ""),
        str(job.get("description") or ""),
        str(job.get("full_description") or ""),
    ]))


def score_by_rules(job: dict, preferences: dict[str, Any] | None = None) -> dict | None:
    """Return a deterministic score for obvious configured cases.

    Rule policy:
    - Reject configured reject roles/dealbreakers.
    - Apply low hard caps when matching hard-cap patterns are obvious.
    - Auto-score configured target/adjacent role matches only when backed by
      configured positive signals. Leave everything else to the LLM.
    """
    prefs = scoring_preferences(preferences)
    if not prefs:
        return None

    title = _clean(job.get("title"))
    company = _clean(job.get("company")) or "unknown company"
    text = _job_text(job)

    reject_roles = _items(prefs.get("reject_roles"))
    dealbreakers = _items(prefs.get("dealbreakers"))
    positive_signals = _items(prefs.get("positive_signals"))
    negative_signals = _items(prefs.get("negative_signals"))
    target_roles = _items(prefs.get("target_roles"))
    adjacent_roles = _items(prefs.get("adjacent_roles"))

    rejected_role = _role_match(title, reject_roles)
    if rejected_role:
        return RuleScore(
            score=2,
            gap=f"Filtered by preferences: title matches rejected role '{rejected_role}'.",
            rule="reject_role",
        ).as_dict()

    matched_dealbreakers = _matched_phrases(text, dealbreakers)
    if matched_dealbreakers:
        keywords = ", ".join(matched_dealbreakers[:5])
        return RuleScore(
            score=2,
            gap=f"Filtered by preferences: dealbreaker matched ({keywords}).",
            keywords=keywords,
            rule="dealbreaker",
        ).as_dict()

    for cap in _hard_caps(prefs):
        if not isinstance(cap, dict):
            continue
        patterns = _items(cap.get("patterns"))
        matched = _matched_phrases(text, patterns)
        if not matched:
            continue
        try:
            max_score = int(cap.get("max_score", 10))
        except (TypeError, ValueError):
            continue
        if max_score <= 4:
            keywords = ", ".join(matched[:5])
            return RuleScore(
                score=max(1, min(10, max_score)),
                gap=f"Hard cap by preferences: {cap.get('name') or keywords}.",
                keywords=keywords,
                rule=f"hard_cap:{cap.get('name') or 'configured'}",
            ).as_dict()

    target_role = _role_match(title, target_roles)
    adjacent_role = _role_match(title, adjacent_roles)
    matched_positive = _matched_phrases(text, positive_signals)
    matched_negative = _matched_phrases(text, negative_signals)

    # Direct title hits are useful, but require configured evidence unless the
    # user has not provided positive_signals. That avoids regex religion while
    # still saving tokens on obvious matches.
    if target_role and (not positive_signals or len(matched_positive) >= 2):
        score = 9 if len(matched_positive) >= 3 and not matched_negative else 8
        keywords = ", ".join((matched_positive + [target_role])[:8])
        return RuleScore(
            score=score,
            fit=f"Rule match: '{title}' at {company} matches target role '{target_role}'.",
            gap="Rule-scored obvious configured target match; use LLM if the posting is unusually narrow.",
            keywords=keywords,
            rule="target_role_match",
        ).as_dict()

    if adjacent_role and len(matched_positive) >= 2 and not matched_negative:
        keywords = ", ".join((matched_positive + [adjacent_role])[:8])
        return RuleScore(
            score=7,
            fit=f"Rule match: '{title}' at {company} matches adjacent role '{adjacent_role}'.",
            gap="Adjacent configured role; confirm fit against resume details.",
            keywords=keywords,
            rule="adjacent_role_match",
        ).as_dict()

    return None
