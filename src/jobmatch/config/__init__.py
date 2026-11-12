"""JobMatch configuration — re-exports from focused submodules.

All existing imports continue to work:
    from jobmatch.config import DB_PATH, load_profile, get_tier, ...
"""

from jobmatch.config.locations import (
    load_location_accept_reject,
    location_ok,
)
from jobmatch.config.paths import (
    APP_DIR,
    CONFIG_DIR,
    DB_PATH,
    ENV_PATH,
    LOG_DIR,
    PACKAGE_DIR,
    PREFERENCES_PATH,
    PROFILE_NAME,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)
from jobmatch.config.preferences import (
    load_preferences,
    scoring_preferences,
)
from jobmatch.config.profiles import (
    load_env,
    load_profile,
    load_search_config,
)
from jobmatch.config.sites import (
    load_base_urls,
    load_sites_config,
)
from jobmatch.config.tier import (
    TIER_COMMANDS,
    TIER_LABELS,
    check_tier,
    get_tier,
)

__all__ = [
    # Paths
    "APP_DIR", "DB_PATH", "PROFILE_PATH", "RESUME_PATH", "RESUME_PDF_PATH",
    "SEARCH_CONFIG_PATH", "PREFERENCES_PATH", "ENV_PATH", "LOG_DIR", "PACKAGE_DIR",
    "CONFIG_DIR", "PROFILE_NAME", "ensure_dirs",
    # Profiles
    "load_profile", "load_search_config", "load_env", "load_preferences", "scoring_preferences",
    # Sites
    "load_sites_config", "load_base_urls",
    # Tier
    "TIER_LABELS", "TIER_COMMANDS", "get_tier", "check_tier",
    # Location
    "load_location_accept_reject", "location_ok",
]
