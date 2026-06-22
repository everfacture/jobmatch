"""Shared location accept/reject filtering."""


def load_location_accept_reject(search_cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Load location accept/reject lists from search config.

    Args:
        search_cfg: Search config dict. If None, loads from profile.

    Returns:
        (accept_patterns, reject_patterns)
    """
    if search_cfg is None:
        from jobmatch.config import load_search_config
        search_cfg = load_search_config()
    accept = search_cfg.get("location", {}).get("accept_patterns", [])
    reject = search_cfg.get("location", {}).get("reject_patterns", [])
    return accept, reject


def location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote/anywhere/WFH always passes. Then reject patterns exclude,
    accept patterns include. If neither matches, reject by default.
    """
    if not location:
        return True
    loc = location.lower()
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True
    for r in reject:
        if r.lower() in loc:
            return False
    for a in accept:
        if a.lower() in loc:
            return True
    return False
