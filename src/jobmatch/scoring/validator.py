"""Validation: banned words, fabrication checks, JSON validation.

Shared with cover_letter.py where applicable, but tailored resume validation
has its own rules (field-level checks, fabrication watchlist).
"""

import re


BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
]

# Words that indicate the LLM fabricated a project or metric
FABRICATION_WATCHLIST: list[str] = [
    "built openclaw", "created openclaw", "founded openclaw", "developed openclaw",
    "my openclaw", "our openclaw",
    "invented", "pioneered", "created from scratch",
]


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")
    text = text.replace("\u2013", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    return text.strip()


def validate_json_fields(data: dict, profile: dict, mode: str = "normal") -> dict:
    """Validate the LLM's structured JSON resume output.

    Checks that required fields exist, no banned words, no fabrication.
    Returns: {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    required = ["title", "summary", "skills", "experience", "education"]
    for field in required:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # Check fabrication
    full_text = json_dumps_shallow(data).lower()
    found_fabrications = [w for w in FABRICATION_WATCHLIST if w in full_text]
    if found_fabrications:
        errors.append(f"Fabrication detected: {', '.join(found_fabrications[:3])}")

    # Check banned words
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", full_text)]
    if found_banned:
        msg = f"Banned words: {', '.join(found_banned[:5])}"
        if mode == "strict":
            errors.append(msg)
        else:
            warnings.append(msg)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


def validate_tailored_resume(text: str, mode: str = "normal") -> dict:
    """Validate the final rendered tailored resume text."""
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found:
        msg = f"Banned words: {', '.join(found[:5])}"
        if mode == "strict":
            errors.append(msg)
        else:
            warnings.append(msg)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


def json_dumps_shallow(obj: dict) -> str:
    """Quick stringification of dict values for text search."""
    parts = []
    for v in obj.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    parts.extend(str(x) for x in item.values())
                else:
                    parts.append(str(item))
        else:
            parts.append(str(v))
    return " ".join(parts)
