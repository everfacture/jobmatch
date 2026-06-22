"""JobMatch LLM response parsing utilities.

Pure functions with zero side effects. Extract structured data from LLM
responses regardless of formatting, fences, or preamble.

Functions:
  extract_json(raw)        — robustly extract JSON from messy LLM output
  parse_marker_lines(raw)  — parse KEY: value marker lines
  strip_preamble(raw)      — remove LLM meta-commentary before known markers
"""

import json
import re


def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Tries in order:
    1. Direct json.loads
    2. Strip ```json ... ``` fences (tries all blocks)
    3. Find first '{' to last '}' via regex
    4. Shrink from end (LLMs sometimes append after JSON)

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    raw = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Markdown fences — try every fenced block
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except (json.JSONDecodeError, ValueError):
                continue

    # 3. Find outermost { ... }
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass

    # 4. Try shrinking from the end (LLMs sometimes append after JSON)
    for end in range(len(raw), 0, -1):
        candidate = raw[:end].strip()
        if candidate.endswith("}"):
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass

    raise ValueError(f"No valid JSON in LLM response (first 200 chars: {raw[:200]})")


def parse_marker_lines(raw: str, markers: dict[str, type]) -> dict:
    """Parse LLM response using line-prefix markers.

    Supports both single-line markers (``SCORE: 7``) and block markers where
    the value starts on following bullet lines::

        FIT:
        - reason one
        - reason two
        GAP: watch-out
    """
    results: dict = {"remainder": ""}
    remaining_lines: list[str] = []
    current_marker: str | None = None
    buffers: dict[str, list[str]] = {name: [] for name in markers}

    for line in raw.split("\n"):
        stripped = line.strip()
        matched_name: str | None = None
        matched_value = ""
        for name in markers:
            prefix = f"{name}:"
            if stripped.startswith(prefix):
                matched_name = name
                matched_value = stripped[len(prefix):].strip()
                break

        if matched_name is not None:
            current_marker = matched_name
            if matched_value:
                buffers[matched_name].append(matched_value)
            continue

        if current_marker and stripped:
            buffers[current_marker].append(stripped)
        else:
            remaining_lines.append(line)

    for name, typ in markers.items():
        value = "\n".join(buffers[name]).strip()
        if typ is int:
            m = re.search(r"\d+", value)
            results[name] = int(m.group()) if m else None
        elif typ is list:
            results[name] = [x.strip() for x in value.split(",") if x.strip()]
        else:
            results[name] = value

    results["remainder"] = "\n".join(remaining_lines).strip()
    return results


def strip_preamble(raw: str, marker: str = "dear") -> str:
    """Remove LLM preamble before a known marker string.

    Models often output meta-commentary before the actual content
    (e.g. "Here is the cover letter:"). This strips everything before
    the first occurrence of the marker (case-insensitive).

    Args:
        raw: Raw LLM response text.
        marker: The marker to search for (case-insensitive, default: "dear").

    Returns:
        Text starting from the marker, or the original text if not found.
    """
    idx = raw.lower().find(marker.lower())
    if idx > 0:
        return raw[idx:]
    return raw
