# Changelog

All notable public changes to JobMatch.

## 0.3.0 — Initial public release

- Renamed and cleaned the project as **JobMatch**.
- Default pipeline is now `discover -> enrich -> score -> dedup -> notify`.
- Added local-first runtime config under `~/.jobmatch/`.
- Added generic example config files for profile, preferences, searches, providers, and notifications.
- Added OpenAI-compatible bring-your-own-AI provider configuration.
- Added deterministic rules-first scoring before LLM scoring.
- Kept cover letters, resume tailoring, and apply packs as explicit manual stages only.
- Added public README, quickstart, privacy notes, provider guide, scoring guide, example run, tests, and CI.
- Preserved upstream attribution to [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot).
