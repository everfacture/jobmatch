# Contributing to JobMatch

JobMatch is a self-hosted job discovery, scoring, and digest pipeline. Keep changes small, verified, and aligned with the local-first/manual-review workflow.

## Source of truth

- Runtime config/data lives outside the repo.
- Default runtime directory: `~/.jobmatch/`.
- Secrets stay in `.env`; never commit or print tokens.
- Repo ships examples only.

## Setup

```bash
git clone https://github.com/everfacture/jobmatch.git
cd jobmatch

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional:

```bash
pip install --no-deps python-jobspy
pip install pydantic tls-client requests markdownify regex
playwright install chromium
```

## Checks

```bash
PYTHONPATH=src python -m compileall -q src/jobmatch
PYTHONPATH=src python -m jobmatch.cli doctor
PYTHONPATH=src python -m pytest -q
```

## Architecture rules

- Default pipeline order: `discover -> enrich -> score -> dedup -> notify`.
- Cover letters, apply packs, and resume tailoring are explicit/manual stages.
- Runtime config belongs in `~/.jobmatch/`, not in the repo.
- Repo docs must not contain real credentials or absolute private machine paths.
- Prefer concrete tests before structural database splits.
- Preserve backwards compatibility for existing private installs during migration.

## Code style

- Names describe intent.
- Keep functions focused and testable.
- Prefer explicit SQL over hidden ORM behaviour.
- Configuration errors should tell the user the exact fix.
- Do not add speculative abstractions before a real second adapter exists.

## Useful commands

```bash
jobmatch init
jobmatch doctor
jobmatch run --dry-run
jobmatch run
jobmatch status
jobmatch prune --older-than 14 --yes
```
