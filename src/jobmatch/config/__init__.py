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
    APPLY_WORKER_DIR,
    CHROME_WORKER_DIR,
    CONFIG_DIR,
    COVER_LETTER_DIR,
    DB_PATH,
    DEFAULTS,
    ENV_PATH,
    LOG_DIR,
    PACKAGE_DIR,
    PREFERENCES_PATH,
    PROFILE_NAME,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    TAILORED_DIR,
    ensure_dirs,
    get_chrome_path,
    get_chrome_user_data,
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
    is_manual_ats,
    load_base_urls,
    load_blocked_sites,
    load_blocked_sso,
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
    "SEARCH_CONFIG_PATH", "PREFERENCES_PATH", "ENV_PATH", "TAILORED_DIR", "COVER_LETTER_DIR",
    "LOG_DIR", "CHROME_WORKER_DIR", "APPLY_WORKER_DIR", "PACKAGE_DIR",
    "CONFIG_DIR", "DEFAULTS", "PROFILE_NAME", "get_chrome_path", "get_chrome_user_data",
    "ensure_dirs",
    # Profiles
    "load_profile", "load_search_config", "load_env", "load_preferences", "scoring_preferences",
    # Sites
    "load_sites_config", "is_manual_ats", "load_blocked_sites",
    "load_blocked_sso", "load_base_urls",
    # Tier
    "TIER_LABELS", "TIER_COMMANDS", "get_tier", "check_tier",
    # Location
    "load_location_accept_reject", "location_ok",
]
