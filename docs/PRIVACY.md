# Privacy

JobMatch is designed as a local-first tool.

## What stays local

- `.env`
- `profile.json`
- `preferences.yaml`
- `searches.yaml`
- `resume.txt`
- `jobmatch.db`
- generated outputs such as cover letters and digests

The repo ships examples only. Real runtime data should never be committed.

## What may leave the machine

When AI scoring, cover-letter generation, or other LLM-backed stages run, the relevant job/profile/resume text may be sent to the configured AI provider.

That means:

- your configured base URL receives the request
- your configured API key is used
- the provider processes whatever job/profile/resume text is included in the prompt

## What the tool does not do by default

- it does **not** run hosted auth or shared multi-user accounts
- it does **not** auto-apply unless explicitly enabled
- it does **not** guarantee that scraped job postings stay active, accurate, or legally compliant

## Recommendation

- keep real credentials in `.env`
- commit only example configs
- keep generated application artefacts out of the repo
- avoid sending sensitive data to providers you do not trust
