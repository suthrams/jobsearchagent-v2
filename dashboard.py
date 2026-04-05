# dashboard.py
# ─────────────────────────────────────────────────────────────────────────────
# Streamlit dashboard for browsing scored job results.
#
# Run with:
#   streamlit run dashboard.py
#
# Views:
#   Top Matches   — all scored jobs ranked by best score across all tracks
#   IC Track      — sorted by IC engineering score with Claude summaries
#   Architect     — sorted by architect score with Claude summaries
#   Management    — sorted by management/director score with Claude summaries
#   Companies     — aggregated by company with a bar chart of top targets
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path("data/jobs.db")
RESUME_PATH = "resume.pdf"

# ─── Agent initialisation (runs once per server start) ────────────────────────


@st.cache_resource
def init_agents() -> dict | None:
    """
    Loads config and initialises the ProfileAgent and TailoringAgent.
    Cached as a resource so the Anthropic client is created only once.
    Returns None if config/config.yaml is missing or fails to load.
    """
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        return None
    try:
        import yaml
        from models.config_schema import AppConfig
        from claude.client import ClaudeClient
        from claude.prompt_loader import PromptLoader
        from claude.response_parser import ResponseParser
        from agents.profile_agent import ProfileAgent
        from agents.tailoring_agent import TailoringAgent

        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        config = AppConfig.model_validate(raw)

        client = ClaudeClient(config.claude)
        loader = PromptLoader()
        parser = ResponseParser()
        return {
            "profile_agent": ProfileAgent(client, loader, parser),
            "tailoring_agent": TailoringAgent(
                client, loader, parser, config.storage.tailored_resumes_dir
            ),
        }
    except Exception as e:
        return None


# ─── DB helpers used by the tailoring UI ──────────────────────────────────────


def load_job_description(job_id: int) -> str | None:
    """Fetches the description text for a single job from the database."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute("SELECT description FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def mark_job_applied(job_id: int) -> None:
    """Sets status = 'applied' and records applied_at for the given job."""
    from datetime import datetime
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE jobs SET status = 'applied', applied_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), job_id),
    )
    conn.commit()
    conn.close()
    st.cache_data.clear()

# ─── Data loading ─────────────────────────────────────────────────────────────


@st.cache_data(ttl=30)  # refresh every 30s so a new run is picked up automatically
def load_jobs() -> pd.DataFrame:
    """Loads all scored jobs from the database into a DataFrame."""
    if not DB_PATH.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        """
        SELECT
            id, title, company, location, source, status,
            score_ic, score_architect, score_management, score_best,
            url, scores_json, salary_json, posted_at, found_at
        FROM jobs
        WHERE status = 'scored'
        ORDER BY score_best DESC
        """,
        conn,
    )
    conn.close()

    # Parse salary range
    def fmt_salary(sal_json: str | None) -> str:
        if not sal_json:
            return ""
        try:
            s = json.loads(sal_json)
            lo = s.get("min")
            hi = s.get("max")
            cur = s.get("currency", "USD")
            if lo and hi:
                return f"{cur} {lo:,} – {hi:,}"
            if lo:
                return f"{cur} {lo:,}+"
            if hi:
                return f"up to {cur} {hi:,}"
        except Exception:
            pass
        return ""

    df["salary"] = df["salary_json"].apply(fmt_salary)
    df["posted_at"] = pd.to_datetime(df["posted_at"], errors="coerce").dt.date
    df["found_at"] = pd.to_datetime(df["found_at"], errors="coerce").dt.date

    return df


def get_summary(scores_json: str | None, track: str) -> str:
    """Extracts Claude's one-sentence summary for a given track."""
    if not scores_json:
        return ""
    try:
        data = json.loads(scores_json)
        track_data = data.get(track) or {}
        return track_data.get("summary", "")
    except Exception:
        return ""


def get_recommended(scores_json: str | None, track: str) -> bool:
    """Returns True if Claude recommended the job for the given track."""
    if not scores_json:
        return False
    try:
        data = json.loads(scores_json)
        track_data = data.get(track) or {}
        return bool(track_data.get("recommended", False))
    except Exception:
        return False


# ─── Shared components ────────────────────────────────────────────────────────


def score_badge(score: int | None) -> str:
    """Returns a coloured emoji badge for a score."""
    if score is None:
        return "—"
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    if score >= 50:
        return f"🟠 {score}"
    return f"🔴 {score}"


def render_job_card(row: pd.Series, highlight_track: str = "architect") -> None:
    """Renders a single job as an expander with full detail and tailoring UI."""
    score = row.get(f"score_{highlight_track}")
    rec = get_recommended(row["scores_json"], highlight_track)
    rec_tag = " ✅" if rec else ""

    label = f"**{row['title']}** — {row['company']}  |  {score_badge(score)}{rec_tag}"
    with st.expander(label, expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("IC Score", row["score_ic"] or "—")
        col2.metric("Architect Score", row["score_architect"] or "—")
        col3.metric("Mgmt Score", row["score_management"] or "—")

        st.markdown(f"**Company:** {row['company']}")
        if row.get("location"):
            st.markdown(f"**Location:** {row['location']}")
        if row.get("salary"):
            st.markdown(f"**Salary:** {row['salary']}")
        if row.get("posted_at"):
            st.markdown(f"**Posted:** {row['posted_at']}")
        st.markdown(f"**Source:** {row['source']}")
        st.markdown(f"[View Job Posting]({row['url']})", unsafe_allow_html=False)

        # Per-track summaries
        for track, label_ in [("ic", "IC"), ("architect", "Architect"), ("management", "Management")]:
            summary = get_summary(row["scores_json"], track)
            if summary:
                st.markdown(f"**{label_}:** {summary}")

        # ── Tailoring UI ─────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("**Tailor Resume**")

        job_id = int(row["id"])
        agents = init_agents()

        if agents is None:
            st.caption("Tailoring unavailable — config/config.yaml not found.")
        else:
            track_choice = st.selectbox(
                "Track",
                options=["IC", "Architect", "Management"],
                key=f"track_{job_id}",
            )
            tailor_key = f"tailor_{job_id}_{track_choice}"

            if st.button("Tailor Resume", key=f"btn_tailor_{job_id}"):
                description = load_job_description(job_id)
                if not description:
                    st.error("No job description in database for this role.")
                else:
                    from models.job import Job, JobSource, CareerTrack
                    track_map = {
                        "IC": CareerTrack.IC,
                        "Architect": CareerTrack.ARCHITECT,
                        "Management": CareerTrack.MANAGEMENT,
                    }
                    source_map = {s.value: s for s in JobSource}
                    source = source_map.get(str(row["source"]), JobSource.ADZUNA)
                    location = row["location"] if pd.notna(row.get("location")) else None
                    job_obj = Job(
                        url=row["url"],
                        source=source,
                        title=row["title"],
                        company=row["company"],
                        location=location,
                        description=description,
                    )
                    with st.spinner(f"Calling Claude to tailor for {track_choice}..."):
                        try:
                            result = agents["tailoring_agent"].tailor(
                                job_obj,
                                agents["profile_agent"].load(RESUME_PATH),
                                track_map[track_choice],
                            )
                            st.session_state[tailor_key] = result
                        except Exception as e:
                            st.error(f"Tailoring failed: {e}")

            # Show cached result if available
            if tailor_key in st.session_state:
                result = st.session_state[tailor_key]
                st.success(f"Saved to: `{result.output_path}`")

                if result.keywords:
                    st.markdown(f"**ATS Keywords:** {', '.join(result.keywords)}")

                if result.gaps:
                    st.markdown("**Gaps to address:**")
                    for gap in result.gaps:
                        st.markdown(f"- {gap}")

                applied_key = f"applied_{job_id}"
                if applied_key not in st.session_state:
                    if st.button("Mark as Applied", key=f"btn_applied_{job_id}"):
                        mark_job_applied(job_id)
                        st.session_state[applied_key] = True
                        st.success("Status updated to APPLIED")
                else:
                    st.info("Marked as APPLIED ✓")


def render_track_table(df: pd.DataFrame, score_col: str, min_score: int) -> None:
    """Renders a sortable summary table for a single track."""
    track = score_col.replace("score_", "")
    filtered = df[df[score_col] >= min_score].copy()
    filtered = filtered.sort_values(score_col, ascending=False)

    filtered["summary"] = filtered["scores_json"].apply(
        lambda x: get_summary(x, track)
    )
    filtered["rec"] = filtered["scores_json"].apply(
        lambda x: "Yes" if get_recommended(x, track) else ""
    )

    display = filtered[[
        "id", "title", "company", "location", score_col, "rec", "salary", "url", "summary"
    ]].rename(columns={
        score_col: "Score",
        "rec": "Rec",
        "summary": "Claude Summary",
        "url": "URL",
    })

    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "company": st.column_config.TextColumn("Company", width="medium"),
            "location": st.column_config.TextColumn("Location", width="medium"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d"
            ),
            "Rec": st.column_config.TextColumn("Rec", width="small"),
            "salary": st.column_config.TextColumn("Salary", width="medium"),
            "URL": st.column_config.LinkColumn("Link", width="small"),
            "Claude Summary": st.column_config.TextColumn("Claude Summary", width="large"),
        },
    )

    st.caption(f"{len(filtered)} jobs with {track} score >= {min_score}")


# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent")
    st.markdown("---")

    view = st.radio(
        "View",
        ["Top Matches", "IC Track", "Architect Track", "Management Track", "Companies"],
        index=0,
    )

    st.markdown("---")
    min_score = st.slider("Minimum score", min_value=0, max_value=100, value=60, step=5)

    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Maximus, architect")

    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

# ─── Load data ────────────────────────────────────────────────────────────────

df = load_jobs()

if df.empty:
    st.warning("No scored jobs found. Run `python main.py` first.")
    st.stop()

# Apply search filter
if search:
    mask = (
        df["title"].str.contains(search, case=False, na=False)
        | df["company"].str.contains(search, case=False, na=False)
    )
    df = df[mask]

# ─── Views ────────────────────────────────────────────────────────────────────

if view == "Top Matches":
    st.header("Top Matches")
    st.caption(f"Jobs scored >= {min_score} across all tracks, ranked by best score")

    filtered = df[df["score_best"] >= min_score].copy()

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total scored", len(df))
    m2.metric(f"Score >= {min_score}", len(filtered))
    m3.metric("Recommended", int((filtered["scores_json"].apply(
        lambda x: get_recommended(x, "architect") or get_recommended(x, "ic") or get_recommended(x, "management")
    )).sum()))
    m4.metric("Companies", filtered["company"].nunique())

    st.markdown("---")

    # Full table
    display = filtered[[
        "id", "title", "company", "location",
        "score_ic", "score_architect", "score_management", "score_best",
        "salary", "url",
    ]].rename(columns={
        "score_ic": "IC",
        "score_architect": "Arch",
        "score_management": "Mgmt",
        "score_best": "Best",
        "url": "URL",
    })

    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "company": st.column_config.TextColumn("Company", width="medium"),
            "location": st.column_config.TextColumn("Location", width="medium"),
            "IC": st.column_config.ProgressColumn("IC", min_value=0, max_value=100, format="%d"),
            "Arch": st.column_config.ProgressColumn("Arch", min_value=0, max_value=100, format="%d"),
            "Mgmt": st.column_config.ProgressColumn("Mgmt", min_value=0, max_value=100, format="%d"),
            "Best": st.column_config.ProgressColumn("Best", min_value=0, max_value=100, format="%d"),
            "salary": st.column_config.TextColumn("Salary", width="medium"),
            "URL": st.column_config.LinkColumn("Link", width="small"),
        },
    )

    st.markdown("---")
    st.subheader("Job Details")
    for _, row in filtered.iterrows():
        render_job_card(row, highlight_track="architect")

elif view == "IC Track":
    st.header("IC Engineering Track")
    st.caption("Senior / Staff / Principal Engineer roles, ranked by IC score")
    render_track_table(df, "score_ic", min_score)

    st.markdown("---")
    st.subheader("Job Details")
    filtered = df[df["score_ic"] >= min_score].sort_values("score_ic", ascending=False)
    for _, row in filtered.iterrows():
        render_job_card(row, highlight_track="ic")

elif view == "Architect Track":
    st.header("Architect Track")
    st.caption("Solutions / Principal / Enterprise Architect roles, ranked by architect score")
    render_track_table(df, "score_architect", min_score)

    st.markdown("---")
    st.subheader("Job Details")
    filtered = df[df["score_architect"] >= min_score].sort_values("score_architect", ascending=False)
    for _, row in filtered.iterrows():
        render_job_card(row, highlight_track="architect")

elif view == "Management Track":
    st.header("Management / Director Track")
    st.caption("Senior Manager / Director / VP roles, ranked by management score")
    render_track_table(df, "score_management", min_score)

    st.markdown("---")
    st.subheader("Job Details")
    filtered = df[df["score_management"] >= min_score].sort_values("score_management", ascending=False)
    for _, row in filtered.iterrows():
        render_job_card(row, highlight_track="management")

elif view == "Companies":
    st.header("Top Target Companies")
    st.caption("Aggregated by company — how many strong roles each company has posted")

    # Aggregate per company
    agg = (
        df.groupby("company")
        .agg(
            jobs=("id", "count"),
            best_ic=("score_ic", "max"),
            best_arch=("score_architect", "max"),
            best_mgmt=("score_management", "max"),
            best_overall=("score_best", "max"),
        )
        .reset_index()
        .sort_values("best_overall", ascending=False)
    )

    # Filter to companies with at least one role at or above min_score
    agg = agg[agg["best_overall"] >= min_score]

    # Bar chart — top 20 companies
    top = agg.head(20).sort_values("best_overall")
    fig = px.bar(
        top,
        x="best_overall",
        y="company",
        orientation="h",
        color="best_overall",
        color_continuous_scale="teal",
        labels={"best_overall": "Best Score", "company": "Company"},
        title=f"Top {len(top)} Companies by Best Match Score",
        text="best_overall",
    )
    fig.update_layout(showlegend=False, coloraxis_showscale=False, height=600)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, width="stretch")

    st.markdown("---")

    # Company table
    display = agg.rename(columns={
        "company": "Company",
        "jobs": "Roles",
        "best_ic": "Best IC",
        "best_arch": "Best Arch",
        "best_mgmt": "Best Mgmt",
        "best_overall": "Best Score",
    })

    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "Company": st.column_config.TextColumn("Company", width="large"),
            "Roles": st.column_config.NumberColumn("Roles", width="small"),
            "Best IC": st.column_config.ProgressColumn("Best IC", min_value=0, max_value=100, format="%d"),
            "Best Arch": st.column_config.ProgressColumn("Best Arch", min_value=0, max_value=100, format="%d"),
            "Best Mgmt": st.column_config.ProgressColumn("Best Mgmt", min_value=0, max_value=100, format="%d"),
            "Best Score": st.column_config.ProgressColumn("Best Score", min_value=0, max_value=100, format="%d"),
        },
    )

    st.markdown("---")

    # Drill into a company
    selected_company = st.selectbox(
        "Drill into a company",
        options=["—"] + list(agg["company"].tolist()),
    )
    if selected_company != "—":
        company_jobs = df[df["company"] == selected_company].sort_values("score_best", ascending=False)
        st.subheader(f"{selected_company} — {len(company_jobs)} role(s)")
        for _, row in company_jobs.iterrows():
            render_job_card(row, highlight_track="architect")
