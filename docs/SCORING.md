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
- positive/negative signals

Rules are intentionally conservative. If preferences are missing or the role is ambiguous, the job falls back to the LLM scorer.

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
