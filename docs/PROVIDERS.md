# Providers

JobMatch supports any OpenAI-compatible `/chat/completions` endpoint as the first-class provider path.

## Environment variables

### Canonical names

```env
JOBMATCH_LLM_BASE_URL=
JOBMATCH_LLM_API_KEY=
JOBMATCH_LLM_MODEL=
JOBMATCH_FALLBACK_LLM_BASE_URL=
JOBMATCH_FALLBACK_LLM_API_KEY=
JOBMATCH_FALLBACK_LLM_MODEL=
```

### Legacy aliases still accepted

```env
LLM_URL
LLM_API_KEY
LLM_MODEL
FALLBACK_LLM_URL
FALLBACK_LLM_API_KEY
FALLBACK_LLM_MODEL
OPENAI_API_KEY
GEMINI_API_KEY
```

These are a migration convenience. Prefer the canonical names in new setups.

## Common provider URLs

| Provider | Base URL |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` |
| LM Studio | `http://localhost:1234/v1` |
| Ollama | `http://localhost:11434/v1` |

## Notes

- Local endpoints may omit an API key.
- Remote endpoints with a blank API key are treated as **not configured**.
- `jobmatch doctor` reports provider labels/models, not secret values.
- Fallback providers are optional and are used automatically when the primary provider is exhausted.
- Token-priced models record estimated USD cost when usage tokens and pricing are known.
- Request-billed models can record `request_count` instead of fake token costs, so usage reports do not pretend unknown token prices are zero.
