"""JobMatch configuration — re-exports from focused submodules.

All existing imports continue to work:
    from jobmatch.config import DB_PATH, load_profile, get_tier, ...
"""

# Paths & constants
from jobmatch.config.paths import (
    APP_DIR,
    DB_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    PREFERENCES_PATH,
    ENV_PATH,
    TAILORED_DIR,
    COVER_LETTER_DIR,
    LOG_DIR,
    CHROME_WORKER_DIR,
    APPLY_WORKER_DIR,
    PACKAGE_DIR,
    CONFIG_DIR,
    DEFAULTS,
    PROFILE_NAME,
    get_chrome_path,
    get_chrome_user_data,
    ensure_dirs,
)

# Profiles
from jobmatch.config.profiles import (
    load_profile,
    load_search_config,
    load_env,
)
from jobmatch.config.preferences import (
    load_preferences,
    scoring_preferences,
)

# Sites registry
from jobmatch.config.sites import (
    load_sites_config,
    is_manual_ats,
    load_blocked_sites,
    load_blocked_sso,
    load_base_urls,
)

# Tier system
from jobmatch.config.tier import (
    TIER_LABELS,
    TIER_COMMANDS,
    get_tier,
    check_tier,
)

# Location filtering
from jobmatch.config.locations import (
    load_location_accept_reject,
    location_ok,
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
