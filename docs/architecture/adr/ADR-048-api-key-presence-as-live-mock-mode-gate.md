# ADR-048: API Key Presence as Live/Mock Mode Gate

## Status
Accepted

## Context
Phase 7 wires real Claude providers, real scrapers, and SqliteSaver. The test suite and CI must continue to run without API keys. A mechanism is needed to switch the entire dependency graph between live and mock mode without code changes.

## Decision
At startup, `app/api/dependencies.py` checks for `ANTHROPIC_API_KEY`:
- Present → `_build_real_deps()`: real `ClaudeProvider`, `SqliteSaver`, real scrapers
- Absent → `_build_mocked_deps()`: all agents mocked, `MemorySaver`, no scrapers

## Rationale
- Zero-config: CI never needs secrets; engineers opt into live mode by setting the key.
- A single env var controls the entire dependency graph — no flags, no config entries, no separate run commands.
- All 8 agents are mocked in test mode so the full 389-test suite passes without any API calls.
- Consistent with twelve-factor app principles (config via environment).

## Consequences

### Positive
- CI is always mock mode — fast, cheap, no secrets required
- Local development can be live or mock just by setting/unsetting the key
- The graph topology is identical in both modes — only the leaf dependencies change

### Tradeoffs
- A developer with the key set will always get live mode; they must explicitly unset the key to test mock mode
- Mock responses are fixed fixtures — they do not reflect real LLM output quality

## Implementation Notes
- `_build_mocked_deps()` uses `MagicMock` agents with `side_effect` functions returning valid Pydantic schema fixtures
- `python-dotenv` loads `.env` before the gate check, so the key can be stored in `.env` rather than exported in the shell
