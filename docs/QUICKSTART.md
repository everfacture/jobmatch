# JobMatch Quickstart

This is the no-drama setup path. It assumes you can open a terminal and copy/paste commands.

## What you need

- Python 3.11, 3.12, or 3.13
- Git
- One AI provider with an OpenAI-compatible API endpoint if you want scoring
- Optional: Telegram bot token/chat ID if you want phone notifications

Third-party Python packages are installed by `pip install -e ".[dev]"` or `uv pip install -e ".[dev]"`.

Browser-backed enrichment uses Playwright. Install the Chromium browser once:

```bash
playwright install chromium
```

If you do not want Telegram yet, use the default console notifier first.

## What the output looks like

Telegram/card output is the point. You are trying to get this instead of a browser full of tabs:

```text
🧠 JobMatch Digest — 3 jobs score ≥8
Top score: 9

🔥 [9] Vendor Management & Sourcing Specialist
Example Company · Remote
Why it matched: sourcing, vendor management, supplier performance,
and logistics ownership all match your configured preferences.
```

Console and file output use the same shortlist idea without Telegram setup.

---

## 1. Install

```bash
git clone https://github.com/everfacture/jobmatch.git
cd jobmatch
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

If you use `uv`:

```bash
git clone https://github.com/everfacture/jobmatch.git
cd jobmatch
uv venv
. .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
```

---

## 2. Create your config

`jobmatch init` is interactive. Have a `.txt` or `.pdf` resume file ready before you start. For a first smoke test, press Enter for defaults and leave optional AI/manual-apply helpers off. You can add provider keys later in `~/.jobmatch/.env`.

Run:

```bash
jobmatch init
```

This creates runtime files under:

```text
~/.jobmatch/
```

Important files:

| File | What to put there |
|---|---|
| `~/.jobmatch/.env` | AI provider key/base URL, notification settings |
| `~/.jobmatch/profile.json` | name, location, skills, experience, facts |
| `~/.jobmatch/resume.txt` | plain text CV/resume |
| `~/.jobmatch/preferences.yaml` | roles you want/hate, dealbreakers, scoring rules |
| `~/.jobmatch/searches.yaml` | search terms, locations, job boards |

The repo itself should only contain example files. Do not put real private config in the repo.

---

## 3. Add an AI provider

Open:

```text
~/.jobmatch/.env
```

Minimal OpenAI-compatible config:

```env
JOBMATCH_LLM_BASE_URL=https://api.openai.com/v1
JOBMATCH_LLM_API_KEY=your_api_key_here
JOBMATCH_LLM_MODEL=gpt-4o-mini
JOBMATCH_NOTIFIER=console
```

Other common base URLs:

| Provider | Base URL |
|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Gemini OpenAI-compatible | `https://generativelanguage.googleapis.com/v1beta/openai` |
| LM Studio | `http://localhost:1234/v1` |
| Ollama | `http://localhost:11434/v1` |

Local providers like LM Studio/Ollama may not need an API key.

---

## 4. Tell it what jobs you want

Open:

```text
~/.jobmatch/preferences.yaml
```

`jobmatch init` creates a starter version. Fill in rejects/dealbreakers before AI scoring; otherwise obvious bad matches may still burn paid LLM calls.

Simple version:

```yaml
candidate:
  headline: "Operations manager with logistics and procurement experience"

scoring:
  min_score: 7

  target_roles:
    - "Operations Manager"
    - "Procurement Manager"
    - "Supply Chain Manager"

  adjacent_roles:
    - "Project Manager"
    - "Vendor Manager"

  reject_roles:
    - "Sales Development Representative"
    - "Graphic Designer"

  dealbreakers:
    - "unpaid"
    - "commission only"

  positive_signals:
    - "remote"
    - "procurement"
    - "supplier management"
    - "logistics"

  negative_signals:
    - "cold calling"
    - "door to door"
```

Plain English:

- `target_roles` = jobs you actually want
- `adjacent_roles` = jobs you might accept
- `reject_roles` = jobs you do not want
- `dealbreakers` = phrases that should kill a job
- `positive_signals` = words that make a job more interesting
- `negative_signals` = words that make a job less interesting

Yes, you can list multiple job types.

---

## 5. Check setup

```bash
jobmatch doctor
```

Good enough to start if:

- `profile.json` exists
- `resume.txt` exists
- `searches.yaml` exists or example config is accepted
- LLM provider is shown as configured before you run scoring

---

## 6. Preview without doing anything

```bash
jobmatch run --dry-run
```

Expected shape:

```text
DRY RUN — would execute:
  discover
  enrich
  score
  dedup
```

This should not create a DB and should not need an AI key.

Important: `jobmatch run --dry-run` is only a preview. The first real command to test the bot is discovery, not the full pipeline.

---

## 7. Run discovery first

Discovery hits external job sites. Start small.

```bash
JOBMATCH_NOTIFY=0 jobmatch run discover --workers 1
```

What good looks like:

```text
Stage 'discover' completed ... ok
DB Final State:
  Total jobs:     10+
```

If you get `0 jobs`, the app still worked; your search was probably too narrow or the job board blocked/returned nothing. Edit `~/.jobmatch/searches.yaml` and try broader roles or locations.

For big search configs, chunk it:

```bash
JOBMATCH_NOTIFY=0 \
JOBMATCH_DISCOVERY_PARTS=8 \
JOBMATCH_DISCOVERY_PART=1 \
jobmatch run discover --workers 1
```

Then check:

```bash
jobmatch status
```

---

## 8. Score jobs

Scoring sends job/profile/resume text to your configured AI provider.

If you skipped AI setup, this command will fail on purpose and tell you to add `JOBMATCH_LLM_BASE_URL`, `JOBMATCH_LLM_API_KEY`, and `JOBMATCH_LLM_MODEL`.

```bash
jobmatch run score --score-limit 100 --no-notify
```

Use `--score-limit` or `JOBMATCH_SCORE_LIMIT=100` when you want a hard cap on paid AI scoring calls.

Then:

```bash
jobmatch status
```

---

## 9. Get output

### Console output, easiest first

In `.env`:

```env
JOBMATCH_NOTIFIER=console
```

Then:

```bash
jobmatch run score
```

### Telegram output

In `.env`:

```env
JOBMATCH_NOTIFIER=telegram
JOBMATCH_TELEGRAM_BOT_TOKEN=your_bot_token
JOBMATCH_TELEGRAM_CHAT_ID=your_chat_id
JOBMATCH_TELEGRAM_THREAD_ID=
```

Then:

```bash
jobmatch run score --notify
```

Telegram sends compact cards for high-fit jobs.

---

## 10. Optional cover letters

Cover letters are off by default.

If you want drafts:

```bash
jobmatch run cover --min-score 8
```

Strict mode:

```bash
jobmatch run cover --min-score 8 --validation strict
```

Review every draft before sending. The tool is not your lawyer, recruiter, or conscience.

---

## Troubleshooting

### `doctor` says LLM missing

Open `~/.jobmatch/.env` and check:

```env
JOBMATCH_LLM_BASE_URL=
JOBMATCH_LLM_API_KEY=
JOBMATCH_LLM_MODEL=
```

### Discovery is slow

Use chunking:

```bash
JOBMATCH_DISCOVERY_PARTS=8 JOBMATCH_DISCOVERY_PART=1 jobmatch run discover --workers 1
```

Run parts 1 to 8 over time.

### Telegram does not send

Start with console first:

```env
JOBMATCH_NOTIFIER=console
```

Once scoring works, switch to Telegram.

### Too many bad jobs

Edit:

```text
~/.jobmatch/preferences.yaml
```

Add more `reject_roles`, `dealbreakers`, and `negative_signals`.

### Too few jobs

Edit:

```text
~/.jobmatch/searches.yaml
```

Add broader search terms or more locations.
