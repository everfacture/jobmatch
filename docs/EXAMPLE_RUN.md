# Example Run

Real numbers from a maintainer setup on 2026-11-12. Yours will vary with search config, job boards, provider and network.

## One full run

```bash
jobmatch run --no-notify
```

A mature profile (53 query x location searches, one region, two custom extractors):

| Stage | Time | Result |
|---|---:|---|
| discover | 732 s | 53 LinkedIn/Indeed searches, 4 new; custom extractors 70 s, 4 new |
| enrich | 0.2 s | nothing pending (new rows were fetched during discovery) |
| score | 5 s | 5 jobs, 5 LLM calls |
| dedup | 0.1 s | 0 duplicates |
| **total** | **738 s** | 0 errors |

A fresh profile is slower on discovery because every result is new and its page is fetched: about 60 s per search instead of 10.

## Scoring cost

500 jobs scored with `deepseek-v4-flash` through the OpenAI-compatible path:

| Metric | Result |
|---|---:|
| Elapsed | 254 s (5 workers) |
| LLM calls | 500 |
| Average prompt | ~2,700 tokens |
| Estimated cost | about $0.30 |
| Errors | 0 |

Most jobs score below the threshold and are marked `low_score`. That is the tool filtering, not failing.

Deterministic rules (reject roles, hard caps, target-role matches) settle roughly 10% of jobs before any LLM call.

## Retention

```bash
jobmatch prune --older-than 30 --yes
```

On a 60,000-row database: 41,724 inactive rows deleted, file 372 MB -> 134 MB, 3 s.

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

One compact card per high-fit job. Jobs already sent are not re-sent, even when a board reposts them under a new URL.

## Notes

- `run --dry-run` costs nothing and creates no DB.
- `run discover` hits external job sites.
- `run score` sends job/profile/resume text to the configured AI provider.
