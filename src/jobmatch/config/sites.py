"""Site base URLs used by enrichment to resolve relative links."""

import yaml

from jobmatch.config.paths import CONFIG_DIR


def load_sites_config() -> dict:
    """Load sites.yaml."""
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_base_urls() -> dict[str, str | None]:
    """Site name -> base URL, for resolving relative job/apply links."""
    return load_sites_config().get("base_urls", {})
