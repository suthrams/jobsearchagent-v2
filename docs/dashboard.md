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
```

The data is cached for 30 seconds. This means a `python main.py` run that finishes will be reflected in the dashboard within 30 seconds without requiring a manual refresh. The cache uses pandas + direct SQLite connection — it does not go through the `Database` class.

Only `status = 'scored'` jobs are loaded. The query also reads the denormalised `score_ic`, `score_architect`, `score_management`, and `score_best` columns directly (avoiding JSON parsing in SQL).

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

## Plotly Integration

The Companies bar chart uses `plotly.express.bar` with:
- `orientation="h"` — horizontal for readable company names
- `color_continuous_scale="teal"` — score-proportional colouring
- `text="best_overall"` — score labels on bars

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
