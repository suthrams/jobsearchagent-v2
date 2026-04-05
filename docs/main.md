# main.py — CLI Entry Point

## Purpose

`main.py` is the top-level entry point for the Job Search Agent. It wires together all components (scrapers, agents, database, config) and exposes three commands via `argparse`:

| Command | Action |
|---|---|
| `python main.py` | Scrape new jobs, score them with Claude, print results |
| `python main.py --list` | Show all scored jobs from the database |
| `python main.py --tailor <ID>` | Tailor resume for a specific job by its database ID |
| `python main.py --dashboard` | Same as default run, then launch Streamlit dashboard automatically |
| `python main.py --list --dashboard` | List jobs then open dashboard |

## Startup Sequence

Every run goes through this bootstrap:

```
1. load_dotenv()              — loads ANTHROPIC_API_KEY, ADZUNA_APP_ID, etc. from .env
2. load_config()              — validates config/config.yaml against AppConfig (Pydantic)
3. setup_logging()            — file handler (DEBUG) + terminal handler (WARNING)
4. Database(config.storage)   — opens or creates data/jobs.db
5. ClaudeClient(config.claude) — initialises Anthropic SDK
6. PromptLoader()             — points at prompts/ directory
7. ResponseParser()           — ready to validate Claude JSON responses
8. agents dict                — ProfileAgent, ScoringAgent, TailoringAgent
```

All components are created once and injected into agents — no globals, no singletons.

## Command: Scrape and Score (default)

```
run_scrapers(config)
  └─ LinkedInScraper, AdzunaScraper, LaddersScraper run independently
  └─ one failure does not stop the others

for each job:
  └─ deduplicate by URL and by title+company before inserting

estimate_scoring_cost(num_jobs)   — prints cost estimate and asks for confirmation

ScoringAgent.score_batch(unscored, profile, db=db)
  └─ saves each job to DB immediately after scoring (crash-safe)

print_scored_jobs(all_scored)     — Rich table + output/logs/results.txt
```

## Command: Tailor (`--tailor <ID>`)

Looks up the job by ID from the database, prompts for which career track (IC / Architect / Management), then calls `TailoringAgent.tailor()`. Optionally marks the job as APPLIED.

## Command: List (`--list`)

Dumps all jobs from the database with status breakdown, then shows the scored jobs table.

## Flag: `--dashboard`

Can be combined with any command. After the primary command completes, launches `streamlit run dashboard.py` as a background subprocess via `subprocess.Popen`. The terminal prints the local URL (`http://localhost:8501`) and returns immediately — the main.py process exits while Streamlit keeps running independently.

## Key Functions

| Function | Purpose |
|---|---|
| `load_config()` | Loads and Pydantic-validates `config/config.yaml`. Exits with a clear error if invalid. |
| `setup_logging()` | File handler at DEBUG, terminal at WARNING. Suppresses noisy third-party loggers. |
| `run_scrapers()` | Runs all scrapers, combines results. Failures are warnings, not fatal. |
| `estimate_scoring_cost()` | Calculates API cost estimate before spending tokens. |
| `print_scored_jobs()` | Rich table with colour-coded scores. Filters jobs below score 50. |
| `_write_results_file()` | Saves full job details to `output/logs/results.txt`. |
| `cmd_scrape_and_score()` | Orchestrates the default run. |
| `cmd_tailor()` | Handles `--tailor` flow including track selection and APPLIED status. |
| `cmd_list()` | Shows all DB jobs with status distribution. |

`--dashboard` is handled in `main()` itself after the try/finally block using `subprocess.Popen(["streamlit", "run", "dashboard.py"])`. It is fire-and-forget — main.py does not wait for the Streamlit process to exit.

## Cost Estimation

Before scoring, the app estimates API spend using token averages observed with Sonnet 4.6:

- ~4,000 input tokens per batch (profile + 5 jobs + prompt)
- ~1,200 output tokens per batch (5 score objects)
- Pricing: $3/M input, $15/M output

The user must confirm `y` before any tokens are spent.

## Configuration

The config path is hardcoded to `config/config.yaml`. Copy `config/config.example.yaml` to get started. The resume PDF defaults to `resume.pdf` in the project root.

## Dependencies

- `config/config.yaml` — required before first run
- `resume.pdf` — required for scoring and tailoring
- `ANTHROPIC_API_KEY` in `.env` — required for all Claude calls
- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` in `.env` — required if Adzuna scraper is enabled
