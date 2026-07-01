# Scoring

JobMatch uses a hybrid scoring model:

1. **Deterministic rules** for obvious configured cases.
2. **LLM scoring** for ambiguous cases.

## Where scoring config lives

Private scoring preferences belong in:

```text
~/.jobmatch/preferences.yaml
```

The repo ships a generic example only:

```text
src/jobmatch/config/preferences.example.yaml
```

## Deterministic rules

Configured preferences can drive:

- target roles
- adjacent roles
- reject roles
- dealbreakers
- hard caps
- location boosts
- positive/negative signals

Rules are intentionally conservative. If preferences are missing or the role is ambiguous, the job falls back to the LLM scorer.

### Hard caps

Hard caps are deterministic safety rails. They can only reduce a score.

```yaml
scoring:
  hard_caps:
    - name: "onsite_only"
      patterns: ["onsite only", "must relocate"]
      max_score: 5
```

### Location boosts

Location boosts are optional positive adjustments after normal scoring. They are useful when a role is already a strong fit and happens to be in a preferred location or work mode.

```yaml
scoring:
  location_boosts:
    - name: "preferred_region"
      patterns: ["Remote", "Hybrid", "New York"]
      points: 1
      min_base_score: 8
      max_score: 10
```

They are deliberately limited:

- no boost unless the location text matches a configured pattern
- no boost below `min_base_score`
- no boost above `max_score`
- no source-code defaults for one person's city or career lane

That keeps public installs plug-and-play while still letting each user tune their local `~/.jobmatch/preferences.yaml`.

## Notification dedupe

JobMatch records successful digest sends in a local `notification_history` table. It stores fingerprints based on apply URL/source URL plus title/company/location. This catches common reposts where a job board changes the URL overnight but the role is effectively the same.

When a repost is suppressed, the new row is marked `notified_at` so it will not repeat, but the digest report keeps that separate from actual Telegram cards sent.

## LLM prompt

The scoring prompt is built from:

- `profile.json`
- `resume.txt`
- `preferences.yaml`
- job posting

It is not tied to one candidate or one career lane in source.

## Current public limitations

- deterministic rules are most valuable when preferences are explicit
- scoring is still only as good as the resume/profile/job text it receives
- hosted multi-user isolation does not exist yet
