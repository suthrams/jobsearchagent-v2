# main.py — CLI Entry Point

## Purpose

`main.py` is the top-level entry point for the Job Search Agent. It wires together all components (scrapers, agents, database, config) and exposes commands via `argparse`:

| Command | Action |
|---|---|
| `python main.py` | Scrape new jobs, score them with Claude, print results |
| `python main.py --list` | Show all scored jobs from the database |
| `python main.py --tailor <ID>` | Tailor resume for a specific job by its database ID |
| `python main.py --dashboard` | Same as default run, then launch Streamlit dashboard automatically |
| `python main.py --dashboard-only` | Launch dashboard immediately without scraping or scoring |
| `python main.py --purge` | Delete all scored jobs with best score < 75 (confirmation required) |
| `python main.py --purge --threshold 80` | Same, but with a custom score cutoff |

## Startup Sequence

Every run goes through this bootstrap:

```
1. load_dotenv()              — loads ANTHROPIC_API_KEY, ADZUNA_APP_ID, etc. from .env
2. load_config()              — validates config/config.yaml against AppConfig (Pydantic)
3. setup_logging()            — file handler (DEBUG) + terminal handler (WARNING)
4. Database(config.storage)   — opens or creates data/jobs.db; runs schema migrations
5. db.backfill_states()       — one-time pass to populate state column for existing rows
6. ClaudeClient(config.claude) — initialises Anthropic SDK
7. PromptLoader()             — points at prompts/ directory
8. ResponseParser()           — ready to validate Claude JSON responses
9. agents dict                — ProfileAgent, ScoringAgent, TailoringAgent
```

Step 5 (`backfill_states`) is idempotent — it only updates rows where `state IS NULL AND location IS NOT NULL`, so it is a no-op after the first run that populates all rows.

All components are created once and injected into agents — no globals, no singletons.

## Command: Scrape and Score (default)

```
run_started_at = datetime.now(tz=timezone.utc)  — captured BEFORE scraping; passed to insert_run
                                       so dashboard WHERE found_at >= run_at works correctly
client.reset_usage()                — clear token counters before this run starts

run_scrapers(config)
  └─ LinkedInScraper, AdzunaScraper, LaddersScraper run independently
  └─ one failure does not stop the others

for each job:
  └─ deduplicate by URL and by title+company before inserting

estimate_scoring_cost(num_jobs)     — prints cost estimate and asks for confirmation

ScoringAgent.score_batch(unscored, profile, db=db)
  └─ saves each job to DB immediately after scoring (crash-safe)
  └─ ClaudeClient accumulates real token counts per operation

client.get_usage()                  — retrieve actual input/output tokens per operation
tokens_to_cost(input, output)       — convert to USD using Sonnet 4.6 pricing

db.insert_run(run_at=run_started_at, ...)  — persist run stats and token counts to runs table

print_scored_jobs(all_scored)       — Rich table + output/logs/results.txt
```

## Command: Tailor (`--tailor <ID>`)

Looks up the job by ID from the database, prompts for which career track (IC / Architect / Management), then calls `TailoringAgent.tailor()`. Optionally marks the job as APPLIED.

## Command: List (`--list`)

Dumps all jobs from the database with status breakdown, then shows the scored jobs table.

## Command: Purge (`--purge [--threshold N]`)

Hard-deletes scored jobs from the database where `score_best < threshold` (default 75). Designed for periodic cleanup to keep the database focused on high-quality matches.

```
Purge preview
  Total jobs in DB : 312
  To be deleted    : 241  (score_best < 75, status not applied/offer)
  Will remain      : 71

Permanently delete 241 job(s)? This cannot be undone. [y/N]:
```

**Safety rules:**
- Jobs with `status = 'applied'` or `status = 'offer'` are **never** deleted, regardless of score — your application history is preserved.
- Unscored jobs (`score_best IS NULL`, status `new`) are left untouched.
- Requires explicit `y` confirmation — no accidental deletions.
- The `--threshold` flag (default 75) lets you raise or lower the cutoff: `--threshold 80` keeps only strong matches.

## Flag: `--dashboard`

Can be combined with any command. After the primary command completes, launches `streamlit run dashboard.py` as a background subprocess via `subprocess.Popen`. The terminal prints the local URL (`http://localhost:8501`) and returns immediately — the main.py process exits while Streamlit keeps running independently.

## Key Functions

| Function | Purpose |
|---|---|
| `load_config()` | Loads and Pydantic-validates `config/config.yaml`. Exits with a clear error if invalid. |
| `setup_logging()` | File handler at DEBUG, terminal at WARNING. Suppresses noisy third-party loggers. |
| `run_scrapers()` | Runs all scrapers, combines results. Failures are warnings, not fatal. |
| `estimate_scoring_cost()` | Calculates API cost estimate before spending tokens. Returns `(cost_usd, num_batches)`. |
| `tokens_to_cost()` | Converts real `(input_tokens, output_tokens)` to USD using Sonnet 4.6 pricing. Used after scoring to compute `actual_cost_usd`. |
| `print_scored_jobs()` | Rich table with colour-coded scores. Columns: ID, Title, Company, State, IC, Arch, Mgmt, Best, Rec, Source. Filters jobs below score 50. |
| `_write_results_file()` | Saves full job details to `output/logs/results.txt`. |
| `cmd_scrape_and_score()` | Orchestrates the default run — scrape, dedup, score, record run, print results. Takes `client` so it can reset and read token usage. |
| `cmd_tailor()` | Handles `--tailor` flow including track selection and APPLIED status. |
| `cmd_list()` | Shows all DB jobs with status distribution. |
| `cmd_purge()` | Handles `--purge` — shows preview counts, prompts for confirmation, calls `db.delete_below_threshold()`. |

`--dashboard` is handled in `main()` itself after the try/finally block using `subprocess.Popen(["streamlit", "run", "dashboard.py"])`. It is fire-and-forget — main.py does not wait for the Streamlit process to exit.

## Cost Estimation and Actual Tracking

Before scoring, the app estimates API spend using token averages observed with Sonnet 4.6:

- ~6,500 input tokens per batch (profile + 10 jobs + prompt)
- ~3,000 output tokens per batch (10 score objects — ~300 tokens each)
- Pricing: $3/M input, $15/M output

The user must confirm `y` before any tokens are spent.

After scoring completes, actual token counts are read from `ClaudeClient.get_usage()` — these are the real values returned by the Anthropic API in `message.usage`. The actual cost is computed via `tokens_to_cost()` and stored in the `runs` table alongside per-operation breakdowns (scoring, parsing, tailoring). The dashboard Run History view uses actual cost where available and falls back to the estimate for older runs.

## Configuration

The config path is hardcoded to `config/config.yaml`. Copy `config/config.example.yaml` to get started. The resume PDF defaults to `resume.pdf` in the project root.

## Dependencies

- `config/config.yaml` — required before first run
- `resume.pdf` — required for scoring and tailoring
- `ANTHROPIC_API_KEY` in `.env` — required for all Claude calls
- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` in `.env` — required if Adzuna scraper is enabled
