"""JobMatch LLM module — re-exports for backward compatibility.

New code should import directly from submodules:
  from jobmatch.llm.client import get_client, LLMClient
  from jobmatch.llm.parsing import extract_json, parse_marker_lines, strip_preamble
"""

from jobmatch.llm.client import (
    get_client,
    LLMClient,
    ModelEntry,
    _maybe_prepend_no_think,
)

from jobmatch.llm.parsing import (
    extract_json,
    parse_marker_lines,
    strip_preamble,
)

__all__ = [
    "get_client",
    "LLMClient",
    "ModelEntry",
    "_maybe_prepend_no_think",
    "extract_json",
    "parse_marker_lines",
    "strip_preamble",
]
