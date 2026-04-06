# claude/client.py — Anthropic SDK Wrapper

## Purpose

A thin wrapper around the Anthropic Python SDK. **Every Claude API call in the project goes through this single class.** No other file imports `anthropic` directly. This centralises authentication, retry logic, logging, and per-operation settings.

## Design Principle: Single Seam

Having one class own all Claude calls means:
- API key management is in one place
- Retry behaviour is consistent across all agents
- Token usage is accumulated per operation and available after any run
- Swapping models or adding streaming in the future requires changes in exactly one file

## Agentic Pattern: Retry with Exponential Backoff

Uses `tenacity` to retry on `RateLimitError` and `APIStatusError`:

```
Attempt 1 → fails (rate limit)
Wait 2s
Attempt 2 → fails (server error)
Wait 4s
Attempt 3 → succeeds (or raises)
```

Configured with:
- `stop_after_attempt(3)` — maximum 3 tries
- `wait_exponential(multiplier=1, min=2, max=8)` — 2s, 4s, 8s
- Logs a WARNING before each retry so you can see it in the terminal

## Public Interface

### `ClaudeClient(config: ClaudeConfig)`

Reads `ANTHROPIC_API_KEY` from the environment (loaded from `.env` by `main.py`). Raises `EnvironmentError` if missing.

### `call(*, system, user, operation) → str`

Makes a single Claude API call using the Messages endpoint.

| Parameter | Type | Purpose |
|---|---|---|
| `system` | `str` | System prompt — sets Claude's role and output format |
| `user` | `str` | User message — the actual content to process |
| `operation` | `str` | One of: `resume_parsing`, `job_scoring`, `resume_tailoring` |

Returns the raw response text as a string. Callers are responsible for parsing JSON from this string (via `ResponseParser`).

On every successful call, `input_tokens` and `output_tokens` from `message.usage` are accumulated into the internal `_usage` store, keyed by operation name.

`operation` maps to per-operation settings in `config.yaml`:

| Operation | max_tokens | temperature |
|---|---|---|
| `resume_parsing` | 1,000 | 0.1 |
| `job_scoring` | 2,000 | 0.1 |
| `resume_tailoring` | 2,000 | 0.3 |

### `get_usage() → dict[str, dict[str, int]]`

Returns accumulated token counts since the last `reset_usage()` call, grouped by operation:

```python
{
    "job_scoring":       {"input": 12400, "output": 3600},
    "resume_parsing":    {"input": 2100,  "output": 800},
    "resume_tailoring":  {"input": 3200,  "output": 1100},
}
```

Used by `main.py` after a run to record actual token usage in the `runs` table and compute real API cost.

### `reset_usage() → None`

Clears all accumulated token counts. Called at the start of each `cmd_scrape_and_score` run so token totals reflect only the current execution.

## Logging

Every call logs at DEBUG level:
- Before: `operation`, `model`, `max_tokens`, `temperature`
- After: `input_tokens`, `output_tokens`

This lets you audit `output/logs/run.log` to see exact token usage per call. The same counts are also accumulated in `_usage` and persisted to the `runs` database table at the end of each run.

## Token Accumulation and Cost Tracking

`ClaudeClient` maintains a `_usage` dict that accumulates real token counts from the Anthropic API response metadata across the lifetime of a run:

```
reset_usage()           ← called at start of cmd_scrape_and_score
  │
  ├─ call(..."job_scoring")   → adds input/output tokens to _usage["job_scoring"]
  ├─ call(..."job_scoring")   → adds to the same bucket (each batch)
  ├─ call(..."resume_parsing")→ adds to _usage["resume_parsing"]
  │
get_usage()             ← called after scoring to retrieve totals
  └─ main.py computes actual_cost_usd and writes to runs table
```

This is the source of truth for cost reporting in the Run History dashboard view. The estimates shown before scoring are approximations; actual token counts are always preferred when available.

## What It Does NOT Do

- It does not parse JSON — that is `ResponseParser`'s job.
- It does not build prompts — that is `PromptLoader`'s job.
- It does not handle streaming — all calls use the standard blocking Messages API.
- It does not cache responses — caching happens at the agent level (ProfileAgent).
