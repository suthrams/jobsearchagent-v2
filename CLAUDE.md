# Job Search Agent — Claude Notes

## Project overview
Python tool that scores jobs against your profile across three career tracks:
- `ic` — Senior / Staff / Principal Engineer
- `architect` — Solutions Architect / Principal Architect
- `management` — Senior Manager / Director / Head of Engineering / VP

## Key design decisions

### v2 (active refactor — work here)
- **Stack**: LangChain + LangGraph (orchestration) · FastAPI (backend API) · Streamlit (UI) · SQLite (persistence)
- All agent outputs are JSON validated by Pydantic schemas
- Prompts live in `app/prompts/` with shared guardrails injected into every agent prompt
- LangGraph owns workflow state, branching, HITL pauses, and reflection loops
- FastAPI exposes workflow services to Streamlit — UI never calls workflow internals directly
- SQLAlchemy, Celery, and Redis are excluded from v2 scope

### v1 (stable — do not modify)
- Direct Anthropic SDK only · raw sqlite3 · Streamlit
- Prompts live in `prompts/*.md` as XML-tagged templates
- LinkedIn handled manually via `inbox/linkedin.txt`
- Models: `claude-sonnet-4-6` (parsing, tailoring) · `claude-haiku-4-5-20251001` (scoring) — configurable per operation in `config.yaml` under `claude.model`

## Running the agent
```bash
python main.py                        # scrape + score all new jobs
python main.py --list                 # show all scored jobs
python main.py --tailor 42            # tailor resume for job ID 42
python main.py --dashboard            # scrape + score, then launch dashboard
python main.py --dashboard-only       # launch dashboard immediately (no scraping)
python main.py --purge                # delete scored jobs with best score < 75
python main.py --purge --threshold 80 # delete with a custom cutoff
```

## File structure
- `models/`      — Pydantic data models + shared filter lists (`filters.py`)
- `claude/`      — Anthropic SDK client, prompt loader, response parser
- `prompts/`     — Claude prompt templates
- `scrapers/`    — Job scrapers (LinkedIn, Adzuna, Ladders)
- `agents/`      — Profile parsing, scoring, tailoring
- `storage/`     — SQLite database layer
- `dashboard.py` — Streamlit UI (job list + resume tailoring)
- `tests/`       — pytest test suite (`python -m pytest tests/`)

## Key invariants
- `EXCLUDED_TITLE_KEYWORDS` and `TECH_DESCRIPTION_KEYWORDS` live **only** in `models/filters.py`. Both `scrapers/adzuna.py` and `agents/scoring_agent.py` import from there — never define local copies.
- `run_at` in `db.insert_run()` must be captured **before** scraping begins (see `run_started_at` in `main.py`). If captured at run end, the dashboard "New Jobs" view returns empty.
- Adzuna quota: `(len(locations) × len(keywords)) + len(remote_keywords)` must stay below 100/day (free tier).
- `MAX_PARALLEL_BATCHES = 3` in `agents/scoring_agent.py` — safe for Anthropic free-tier RPM. Raise to 5 on paid tiers. Do not raise without also verifying RPM headroom.
- The system prompt passed to `ClaudeClient.call()` in `ScoringAgent._score_chunk()` must remain byte-identical across all batches (including parallel ones). Never add per-batch variables (like `num_jobs`) to the system prompt — put them in the user message only.
