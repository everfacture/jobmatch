"""Shared location accept/reject filtering."""

_REMOTE_MARKERS = ("remote", "anywhere", "work from home", "wfh", "distributed")


def location_accepts_remote(search_cfg: dict | None) -> bool:
    """Return whether remote/anywhere locations bypass the accept list.

    Default is true so existing profiles keep the old behaviour. A profile that
    wants a real region, not nationwide remote, sets ``location.accept_remote``
    to false.
    """
    if not search_cfg:
        return True
    location_cfg = search_cfg.get("location") or {}
    if "accept_remote" in location_cfg:
        raw = location_cfg.get("accept_remote")
    elif "accept_remote" in search_cfg:
        raw = search_cfg.get("accept_remote")
    else:
        return True
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


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


def location_ok(
    location: str | None,
    accept: list[str],
    reject: list[str],
    *,
    accept_remote: bool = True,
) -> bool:
    """Check if a job location passes the user's location filter.

    Remote/anywhere/WFH passes only when ``accept_remote`` is true. Then reject
    patterns exclude, accept patterns include. If neither matches, reject.
    Blank location still passes so a geo-targeted search with a missing city
    can be judged by the scorer instead of dropped blindly.
    """
    if not location:
        return True
    loc = location.lower()
    if accept_remote and any(marker in loc for marker in _REMOTE_MARKERS):
        return True
    for r in reject:
        if r.lower() in loc:
            return False
    for a in accept:
        if a.lower() in loc:
            return True
    return False
