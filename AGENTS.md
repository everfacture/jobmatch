# JobMatch Agent Guide

This file is for AI coding agents helping a user who has cloned this repo. The human-facing guide is `README.md`; keep that as the product contract.

## Product in one pass

JobMatch is a local-first CLI that finds jobs, enriches postings, scores them against the user's profile/resume/preferences, deduplicates results, and sends a digest.

Default pipeline:

```text
discover -> enrich -> score -> dedup -> notify
```

Optional/manual stages:

```text
tailor
cover
apply
```

Cover letters, resume tailoring, and apply packs are manual helpers. Do not make them part of the default pipeline without an explicit product decision.

## Runtime data and secrets

User data lives outside the repo by default:

```text
~/.jobmatch/
```

Private runtime files include:

- `.env`
- `profile.json`
- `resume.txt` / `resume.pdf`
- `preferences.yaml`
- `searches.yaml`
- `jobmatch.db`
- generated digests, cover letters, apply packs, and tailored resumes

Never commit real credentials, resumes, profiles, databases, generated application files, browser profiles, or local runtime directories.

Repo files should be examples only:

- `.env.example`
- `src/jobmatch/config/profile.example.json`
- `src/jobmatch/config/preferences.example.yaml`
- `src/jobmatch/config/searches.example.yaml`
- `src/jobmatch/config/providers.example.yaml`
- `src/jobmatch/config/notifications.example.yaml`

## Install for development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

Or with `uv`:

```bash
uv venv
. .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
```

First-run smoke path:

```bash
jobmatch init
jobmatch doctor
jobmatch run --dry-run
```

`jobmatch init` is interactive. Use a throwaway resume/profile when testing, not a real user's private data.

## AI provider model

The first-class provider path is any OpenAI-compatible `/chat/completions` endpoint.

Canonical env vars:

```env
JOBMATCH_LLM_BASE_URL=
JOBMATCH_LLM_API_KEY=
JOBMATCH_LLM_MODEL=
```

Do not print API keys. Do not add provider-specific secrets to docs, tests, examples, or commits.

## Commands agents should use

Before changing code:

```bash
git status --short
```

After Python changes:

```bash
PYTHONPATH=src .venv/bin/python -m compileall -q src/jobmatch
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check src tests
```

Useful smoke checks:

```bash
JOBMATCH_DIR=$(mktemp -d) JOBMATCH_NOTIFY=0 jobmatch doctor
JOBMATCH_DIR=$(mktemp -d) JOBMATCH_NOTIFY=0 jobmatch run --dry-run
```

Private-file guard before commit/push:

```bash
git ls-files | grep -Ei '(^|/)(\.env$|jobmatch\.db|resume\.(txt|pdf)$|profile\.json$|cover_letters/|tailored_resumes/|apply_logs/|src/jobmatch/config/searches\.yaml$)' || true
```

Expected output: empty.

## Development rules

- Keep the default pipeline simple and digest-first.
- Keep JobMatch local-first; do not add hosted/SaaS assumptions.
- Do not auto-apply by default.
- Do not hardcode one person's name, career lane, location, resume facts, target employers, or provider.
- Scoring must come from `profile.json`, `resume.txt`, `preferences.yaml`, and the job posting.
- Keep examples generic and reusable.
- Prefer tests around database/query behaviour before restructuring `database.py`.
- Keep docs usable for non-technical users.

## Optional cover letters

Cover letters are available but manual:

```bash
jobmatch run cover --min-score 8
```

Strict validation:

```bash
jobmatch run cover --min-score 8 --validation strict
```

Cover letters use extra LLM calls and must be reviewed by a human before sending.

## Upstream attribution

JobMatch is based on/forked from the open-source ApplyPilot project by Pickle-Pixel:

- https://github.com/Pickle-Pixel/ApplyPilot

Keep attribution visible in the README and preserve license requirements.
