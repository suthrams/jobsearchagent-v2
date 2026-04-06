# dashboard.py — Streamlit Dashboard

## Purpose

A browser-based dashboard for browsing scored job results and triggering resume tailoring. Reads directly from the SQLite database and renders scored jobs across five views. From any job card, you can tailor your resume and mark roles as Applied — all without leaving the browser. Run with:

```bash
streamlit run dashboard.py
```

## Views

| View | Description |
|---|---|
| **Top Matches** | All scored jobs ranked by best score across all tracks. Shows score metrics and job cards. |
| **IC Track** | Senior/Staff/Principal Engineer roles, ranked by IC score. |
| **Architect Track** | Solutions/Principal/Enterprise Architect roles, ranked by architect score. |
| **Management Track** | Senior Manager/Director/VP roles, ranked by management score. |
| **Companies** | Aggregated by company — horizontal bar chart of top companies by best score, plus drill-down table. |
| **Run History** | Per-run cost and token reporting. Charts, metrics, and a full run table. See below. |

## Sidebar Controls

| Control | Purpose |
|---|---|
| View selector | Radio buttons to switch between the 5 views |
| Minimum score slider | Filters out jobs below the threshold (default: 60) |
| Search box | Filters by job title or company name (case-insensitive) |
| Refresh button | Clears the 30-second data cache and forces a database re-read |

## Data Loading

```python
@st.cache_data(ttl=30)
def load_jobs() -> pd.DataFrame:
    ...

@st.cache_data(ttl=30)
def load_runs() -> pd.DataFrame:
    ...
```

Both loaders are cached for 30 seconds. A `python main.py` run that finishes will be reflected within 30 seconds without a manual refresh. Both use pandas + direct SQLite connections — they do not go through the `Database` class.

`load_jobs()` reads only `status = 'scored'` jobs, using the denormalised `score_ic`, `score_architect`, `score_management`, and `score_best` columns directly (avoiding JSON parsing in SQL).

`load_runs()` reads all rows from the `runs` table ordered by `run_at ASC`, adds a `cumulative_cost` column, and returns an empty DataFrame if the table doesn't exist yet (first launch before any run). It also derives `display_cost` — preferring `actual_cost_usd` over `est_cost_usd` when real token data is available.

## Job Cards

Each job is rendered as a `st.expander` with:
- IC, Architect, and Management score metrics side by side
- Company, location, salary, posted date, source
- Clickable link to the original job posting
- Claude's per-track one-sentence summary
- **Tailoring section** — track selector, Tailor Resume button, and results display (see below)

## Resume Tailoring

Each job card includes a tailoring panel at the bottom of its expander. The flow:

1. Select a career track (IC / Architect / Management)
2. Click **Tailor Resume** — the dashboard calls Claude via `TailoringAgent`
3. A spinner shows while Claude runs (~5–10 seconds)
4. On completion: the saved file path, ATS keywords, and gaps are shown inline
5. A **Mark as Applied** button sets the job status to `APPLIED` in the database

Tailoring results are held in `st.session_state` keyed by `tailor_{job_id}_{track}` — they persist across rerenders until the dashboard server restarts.

**Agent initialisation:** `@st.cache_resource` ensures `ClaudeClient`, `ProfileAgent`, and `TailoringAgent` are created once per server start, not on every page render. If `config/config.yaml` is missing, the tailoring section shows a warning instead.

**Requirements for tailoring to work:**
- `config/config.yaml` must exist
- `resume.pdf` must be present in the project root
- `ANTHROPIC_API_KEY` must be set in `.env`

## Score Badges

Colour-coded badge function used throughout:
| Score | Badge |
|---|---|
| >= 80 | 🟢 (green) |
| >= 65 | 🟡 (yellow) |
| >= 50 | 🟠 (orange) |
| < 50 | 🔴 (red) |

## Companies View

The Companies view uses pandas `groupby` to aggregate:
- Total roles per company
- Best IC, Architect, Management, and overall score per company

A horizontal Plotly bar chart shows the top 20 companies. The colour scale is proportional to the best score (teal gradient).

Below the chart, a selectbox lets you drill into a specific company to see all their roles.

## Run History View

The Run History view reads from the `runs` table and shows all data from `ClaudeClient`'s token accumulator, persisted by `main.py` after each scoring run.

### Summary metrics
- **Total Runs** — count of all recorded executions
- **Total Cost** — sum of `actual_cost_usd` (or `est_cost_usd` for older rows)
- **Total Jobs Scored** — cumulative jobs scored across all runs
- **Last Run** — timestamp of the most recent execution
- **Total Input / Output Tokens** — lifetime token totals (shown when real data exists)
- **Input : Output Ratio** — typical for Sonnet scoring is ~3:1 to 4:1

### Charts
| Chart | Description |
|---|---|
| **Cost per Run** | Bar chart — one bar per run, labelled with USD cost |
| **Cumulative Spend** | Line chart — running total of spend over time |
| **Input Tokens per Run** | Stacked bar — broken down by operation (scoring / parsing / tailoring) |
| **Output Tokens per Run** | Stacked bar — same breakdown |
| **Latest Run Cost Breakdown** | Table — per-operation input tokens, output tokens, and USD cost |
| **Jobs per Run** | Grouped bar — Scraped / New / Scored / Skipped side by side |

Token breakdown charts are only shown once real token data exists (i.e. after the first run following the token tracking upgrade). Older run rows show cost charts only, using `est_cost_usd`.

### All Runs table
Sortable table showing every row in the `runs` table, with columns for timestamp, job activity counts, token counts (when available), per-run cost, and cumulative cost.

## Plotly Integration

Charts use `plotly.express` throughout:
- Companies bar chart: `orientation="h"`, `color_continuous_scale="teal"`, score labels on bars
- Run History cost chart: `color_continuous_scale="teal"`, value labels above bars
- Token breakdown: stacked bars colour-coded by operation — blue (scoring), green (parsing), orange (tailoring)
- Cumulative spend: line chart with `markers=True`

## Configuration

No configuration required beyond having `data/jobs.db` exist with scored jobs. The dashboard reads the database directly from `data/jobs.db` (hardcoded path). Page config:

```python
st.set_page_config(
    page_title="Job Search Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)
```

## Dependencies

- `streamlit >= 1.35.0`
- `pandas >= 2.0.0`
- `plotly >= 5.20.0`
- `python-dotenv` — loads `.env` on startup so `ANTHROPIC_API_KEY` is available
- `pyyaml`, `pydantic` — for config loading in the tailoring path
- `anthropic` — via `ClaudeClient` when tailoring is triggered
