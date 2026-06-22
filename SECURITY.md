# Security Policy

JobMatch is a local-first CLI. It can handle private job-search data, resumes, profile facts, and API keys. Treat those as sensitive.

## Do not publish

Never commit or paste these into issues, pull requests, screenshots, or logs:

- real `.env` files
- API keys, Telegram bot tokens, provider tokens, proxy credentials
- resumes/CVs
- `profile.json`
- `preferences.yaml` if it contains private career or personal details
- `searches.yaml` if it exposes private target markets
- `jobmatch.db`
- generated cover letters, apply packs, tailored resumes, or application notes

Use the example files in the repo as templates only.

## Reporting a vulnerability

Open a GitHub issue only if the report does not contain secrets or personal data.

If your report requires private details, redact the sensitive values first. Replace real tokens with placeholders like:

```text
JOBMATCH_LLM_API_KEY=<redacted>
JOBMATCH_TELEGRAM_BOT_TOKEN=<redacted>
```

## AI provider privacy

When scoring or optional AI stages run, JobMatch may send relevant job/profile/resume text to the AI provider you configured. Review your provider's privacy policy before using real personal data.
