# Changelog

All notable public changes to JobMatch.

## Unreleased

- Removed the Workday employer crawl and the split-discovery (`JOBMATCH_DISCOVERY_PARTS`) mode. One sequential run per profile replaces them; the run lock already prevents overlap.
- `jobmatch prune --dry-run` no longer expires old active rows as a side effect; a dry run now changes nothing. A real prune now VACUUMs, so the file shrinks (372 MB -> 134 MB on a 60k-row database).
- Scoring writes each result to the database as it lands instead of buffering every result to the end of the stage. A run killed mid-stage (cron timeout, SIGTERM) now keeps the scores it already paid for; before, a 58-minute run that was killed at the write step lost 1,361 scored jobs.
- Removed the resume-tailoring, cover-letter and apply-pack stages (`run tailor|cover|apply`, `--with-resume`, `--validation`), their validator, 12 unused `jobs` columns, and the Chrome/CapSolver plumbing they needed. They had never produced a row in a live database. Existing databases keep the old columns; new ones are not created.
- Removed the Dealls extractor (its job pages 404 on enrichment: 319 of 374 rows ever) and the Apify-backed Glints/Bayt/Naukri Gulf/GulfTalent/JobStreet stubs. Live custom extractors are Telegram Bali and HHRMA Bali.
- `sites.yaml` now holds only `base_urls`; the unused site/blocklist registry is gone.
- A pipeline run left in `running` by a killed process is closed as `aborted` when the next run starts, so the run table no longer accumulates runs that look alive.
- Discovery opens a LinkedIn job page only for results that are new and pass the location and title filters. Before, every result's page was fetched first, and on a mature profile 99.7% of those were already known or filtered. Live: about 12 s per search instead of about 40 s.
- Missing values from JobSpy are stored as NULL instead of the text "None". Enrichment had been re-checking every such row on each run; enrich on a 60k-row database went from 637 s to 3.6 s.
- One pipeline run per profile at a time. A scheduled run, a manual run and the app's button share a lock; a second run exits and says why.
- `jobmatch schedule HH:MM` and a **Run every day** button install one daily run (crontab, macOS LaunchAgent, Windows Task Scheduler). Installing again replaces it.
- The app connects Telegram from a bot token alone: it reads the chat id from the bot's updates and sends a test message before saving.
- `.env` files holding keys are written readable by the owner only.
- The app's setup now accepts the location it was given. Before, it wrote no accept list, so the location filter dropped every non-remote result and a new user got zero jobs.

- Added an HHRMA Bali discovery extractor. It reads the public sitemap and job pages. The WordPress REST API is disallowed by robots.txt, so it is not used.

- Default `jobmatch init` is a short civilian path: resume, a few facts, job titles, then paste one scoring key.
- Detect local Codex/ChatGPT CLI login for doctor/setup status. Scoring still uses a DeepSeek, OpenAI, OpenRouter, or Groq API key — Codex login is not an official third-party API.
- After an interactive `jobmatch run`, open the local HTML shortlist in the browser. Cron/non-TTY stays quiet. Override with `JOBMATCH_OPEN_DASHBOARD=0/1`.
- New setups default the notifier to console, not Telegram.
- Long form remains as `jobmatch init --advanced`.
- Added a local browser app (`jobmatch app`) and double-click launchers (`Start JobMatch.bat` / `Start JobMatch.command`) that install Python, dependencies, and Chromium on first run.

- Added durable notification history to suppress reposted digest jobs across changed URLs.
- Split notifier reporting into explicit counters for cards sent, duplicate/history suppression, closed postings, and failures.
- Added request-count accounting for request-billed LLM routes instead of fake token-cost estimates.
- Added optional config-driven location boosts for already-strong scoring matches.
- Suppressed noisy HTTP transport logs that can expose full provider or Telegram request URLs.

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
