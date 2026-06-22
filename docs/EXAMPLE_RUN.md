# Example Run

This is a real smoke-test shape from the maintainer setup. Your numbers will vary by search config, location, provider, network, and job boards.

## Discovery chunk

Full discovery can be slow because it searches a matrix of queries and locations. For scheduled runs, split discovery into chunks.

Example command:

```bash
JOBMATCH_NOTIFY=0 \
APIFY_ENABLED=false \
DEALLS_ENABLED=false \
JOBMATCH_DISCOVERY_PARTS=8 \
JOBMATCH_DISCOVERY_PART=1 \
jobmatch run discover --workers 1
```

Observed result:

| Metric | Result |
|---|---:|
| Chunk | 1 / 8 |
| Search combinations | 11 / 84 |
| Source hit in this chunk | LinkedIn via JobSpy |
| Elapsed time | 326s |
| New jobs | 23 |
| Duplicates | 19 |
| Errors | 0 |

Configured live discovery surface for this run:

| Source family | Status |
|---|---|
| JobSpy / LinkedIn | enabled |
| Workday employers | DBS Bank, Visa; final chunk only |
| Smart/custom extractors | final chunk only |
| Apify-backed sources | disabled for this run |
| Dealls | disabled for this run |

## Scoring sample

Bounded scoring sample:

```bash
# Internal sample used run_scoring(limit=10) to avoid scoring the whole backlog.
```

Observed result:

| Metric | Result |
|---|---:|
| Jobs scored | 10 |
| Elapsed time | 43s |
| Model | `deepseek-v4-flash` |
| LLM calls | 10 |
| Prompt tokens | 39,839 |
| Completion tokens | 2,958 |
| Total tokens | 42,797 |
| Estimated cost | `$0.0064` |
| Approx cost/job | `$0.00064` |
| Errors | 0 |

All 10 sampled jobs scored below the active threshold and were marked `low_score`. That is a good outcome: the model filtered weak matches instead of flattering everything.

## Telegram digest shape

Telegram messages are HTML-formatted by the bot. A digest starts like this:

```html
🧠 <b>JobMatch Digest — 3 sample jobs score ≥8</b>
Top score: 9
2026-06-22 15:03 UTC
```

Example card shape:

```html
🔥 <b>[9]</b> <a href="https://example.com/job">Vendor Management &amp; Sourcing Specialist</a>
Example Company · Remote

The role owns sourcing/procurement execution, vendor management, and performance tracking across the claims/vendor function.
```

A real run sends one compact card per high-fit job and may send an urgent summary when score-9 jobs are present.

## Notes

- Discovery speed depends mostly on job boards, query count, and location count.
- Scoring cost depends on provider pricing and job-description length.
- The example model was accessed through the OpenAI-compatible provider path.
- `run --dry-run` costs nothing and creates no DB.
- `run discover` hits external job sites.
- `run score` sends job/profile/resume text to the configured AI provider.
