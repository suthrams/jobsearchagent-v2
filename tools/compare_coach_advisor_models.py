"""A/B comparison harness: career_advisor + interview_coach on Sonnet vs Haiku.

Reads a recent qualifying job + the resume profile + final review from data/v2.db
and runs each agent twice (once per model). Writes a side-by-side markdown
report under blogs/cost_ab/ for the user to read and decide whether the Haiku
output is good enough to swap in (Tier 2 levers L3 / L4 in the cost-reduction
investigation).

This script makes real LLM calls and bills your Anthropic account. Each run is
roughly 4 calls × ~10K input tokens. With Sonnet at $3/M input + Haiku at $1/M
input, expect a per-execution cost in the low tens of cents.

Usage:
    python scripts/compare_coach_advisor_models.py
    python scripts/compare_coach_advisor_models.py --job-id <id>
    python scripts/compare_coach_advisor_models.py --workflow-id <id>

The output path is printed when the run finishes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Allow running from repo root without `pip install -e .`
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.agents.career_advisor import CareerAdvisor  # noqa: E402
from app.agents.interview_coach import InterviewCoach  # noqa: E402
from app.providers.claude_provider import ClaudeProvider  # noqa: E402
from app.providers.prompt_loader import PromptLoader  # noqa: E402
from app.repositories.database import DEFAULT_DB_PATH  # noqa: E402

_OUT_DIR = _REPO_ROOT / "blogs" / "cost_ab"


# ── Data loading ──────────────────────────────────────────────────────────────

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _pick_qualifying_job(conn: sqlite3.Connection,
                        workflow_id: str | None,
                        job_id: str | None) -> dict:
    """Return the candidate job + score + run context.

    Strategy:
      - If --job-id given, use it.
      - Else if --workflow-id given, pick the highest-scoring job from that run.
      - Else pick the highest-scoring job across the most recent 5 runs that
        had at least one qualifying score.
    """
    if job_id:
        row = conn.execute(
            """
            SELECT js.workflow_run_id, js.job_id, js.score_json, j.job_description,
                   j.title, j.company, j.url, js.resume_id
            FROM job_scores js
            JOIN jobs j ON j.id = js.job_id
            WHERE js.job_id = ?
            ORDER BY js.created_at DESC LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    elif workflow_id:
        row = conn.execute(
            """
            SELECT js.workflow_run_id, js.job_id, js.score_json, j.job_description,
                   j.title, j.company, j.url, js.resume_id
            FROM job_scores js
            JOIN jobs j ON j.id = js.job_id
            WHERE js.workflow_run_id = ?
            ORDER BY js.overall_score DESC LIMIT 1
            """,
            (workflow_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT js.workflow_run_id, js.job_id, js.score_json, j.job_description,
                   j.title, j.company, j.url, js.resume_id
            FROM job_scores js
            JOIN jobs j ON j.id = js.job_id
            WHERE js.workflow_run_id IN (
                SELECT workflow_run_id FROM run_metrics
                WHERE total_cost IS NOT NULL
                ORDER BY started_at DESC LIMIT 5
            )
            ORDER BY js.overall_score DESC LIMIT 1
            """,
        ).fetchone()
    if row is None:
        raise SystemExit("No suitable job found. Pass --job-id or --workflow-id.")
    return dict(row)


def _load_resume_profile(conn: sqlite3.Connection, resume_id: str) -> dict:
    row = conn.execute(
        "SELECT parsed_profile_json FROM resumes WHERE id = ?",
        (resume_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"Resume {resume_id} not found in DB.")
    return json.loads(row["parsed_profile_json"])


def _load_final_review(conn: sqlite3.Connection,
                       workflow_id: str, job_id: str) -> dict:
    row = conn.execute(
        """
        SELECT review_json FROM resume_reviews
        WHERE workflow_run_id = ? AND job_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (workflow_id, job_id),
    ).fetchone()
    return json.loads(row["review_json"]) if row else {}


# ── Comparison runners ────────────────────────────────────────────────────────

class _NullObservability:
    """Stand-in observability that no-ops every call. Comparison runs do not
    need a real audit trail; they're operator-driven and ephemeral."""
    def log_agent_started(self, *a, **kw): return "evt-noop"
    def log_agent_completed(self, *a, **kw): return None
    def log_agent_failed(self, *a, **kw): return None
    def log_llm_call(self, *a, **kw): return None


def _build_provider(model_name: str) -> ClaudeProvider:
    return ClaudeProvider(PromptLoader(), model_name=model_name)


def _run_advisor(provider: ClaudeProvider, ctx: dict) -> tuple[dict, dict]:
    agent = CareerAdvisor(provider, _NullObservability())
    advice = agent.run("ab-test", ctx)
    ti, to, cost = provider.last_call_usage()
    return advice.model_dump(), {"tokens_input": ti, "tokens_output": to, "cost_usd": cost}


def _run_coach(provider: ClaudeProvider, ctx: dict) -> tuple[dict, dict]:
    agent = InterviewCoach(provider, _NullObservability())
    prep = agent.run("ab-test", ctx)
    ti, to, cost = provider.last_call_usage()
    return prep.model_dump(), {"tokens_input": ti, "tokens_output": to, "cost_usd": cost}


# ── Markdown rendering ────────────────────────────────────────────────────────

def _render_report(job: dict, score: dict,
                   advisor_results: dict, coach_results: dict) -> str:
    lines = [
        f"# Coach + Advisor: Sonnet vs Haiku — {datetime.now(timezone.utc).isoformat()}",
        "",
        f"**Job:** {job.get('title') or '?'} at {job.get('company') or '?'}",
        f"**URL:** {job.get('url') or '?'}",
        f"**Workflow:** `{job.get('workflow_run_id', '?')}` "
        f"(job `{job.get('job_id', '?')}`)",
        f"**Overall score:** {score.get('overall_score', '?')} "
        f"(tech={score.get('technical_score', '?')}, "
        f"arch={score.get('architecture_score', '?')}, "
        f"lead={score.get('leadership_score', '?')})",
        "",
        "## Cost & token comparison",
        "",
        "| Agent | Model | Tokens in | Tokens out | Cost (USD) |",
        "|---|---|---:|---:|---:|",
    ]
    for label, key in (("Career advisor", "advisor"), ("Interview coach", "coach")):
        for variant in ("sonnet", "haiku"):
            results = advisor_results if key == "advisor" else coach_results
            usage = results[variant]["usage"]
            lines.append(
                f"| {label} | {variant} "
                f"| {usage['tokens_input']:,} | {usage['tokens_output']:,} "
                f"| ${usage['cost_usd']:.4f} |"
            )
    sonnet_total = sum(
        r[v]["usage"]["cost_usd"]
        for r in (advisor_results, coach_results)
        for v in ("sonnet",)
    )
    haiku_total = sum(
        r[v]["usage"]["cost_usd"]
        for r in (advisor_results, coach_results)
        for v in ("haiku",)
    )
    lines.append(
        f"| **Total** |  |  |  | "
        f"**Sonnet ${sonnet_total:.4f}** vs **Haiku ${haiku_total:.4f}** "
        f"(saved ${sonnet_total - haiku_total:.4f}) |"
    )
    lines.append("")

    for label, key in (("Career advisor", "advisor"), ("Interview coach", "coach")):
        results = advisor_results if key == "advisor" else coach_results
        lines.append(f"## {label}")
        lines.append("")
        lines.append("### Sonnet output")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(results["sonnet"]["output"], indent=2))
        lines.append("```")
        lines.append("")
        lines.append("### Haiku output")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(results["haiku"]["output"], indent=2))
        lines.append("```")
        lines.append("")

    lines.append("## How to read this")
    lines.append("")
    lines.append(
        "Compare the two outputs side by side. The questions to ask:"
    )
    lines.append(
        "1. **Coherence** — does Haiku's output feel like a real plan, or a "
        "checklist of generic items?"
    )
    lines.append(
        "2. **Specificity** — do both versions reference details from the JD "
        "and the resume? Sonnet usually does this better."
    )
    lines.append(
        "3. **Actionability** — would you do anything differently after "
        "reading Haiku vs Sonnet?"
    )
    lines.append("")
    lines.append(
        "If the answer to #3 is 'no meaningful difference,' Haiku is the "
        "better choice — saves the cost above on every run. If Sonnet's "
        "output is materially better, keep Sonnet."
    )
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                        help="Path to v2.db (default: data/v2.db)")
    parser.add_argument("--workflow-id", default=None,
                        help="Pick the highest-scoring job from this run")
    parser.add_argument("--job-id", default=None,
                        help="Use this specific job_id")
    parser.add_argument("--out", default=None,
                        help="Output markdown path (default: blogs/cost_ab/<ts>.md)")
    parser.add_argument("--sonnet-model", default="claude-sonnet-4-6")
    parser.add_argument("--haiku-model", default="claude-haiku-4-5-20251001")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = _connect(db_path)
    try:
        job = _pick_qualifying_job(conn, args.workflow_id, args.job_id)
        score = json.loads(job["score_json"]) if job.get("score_json") else {}
        resume_profile = _load_resume_profile(conn, job["resume_id"])
        final_review = _load_final_review(conn, job["workflow_run_id"], job["job_id"])
    finally:
        conn.close()

    print(f"Job: {job.get('title')} at {job.get('company')}")
    print(f"Overall score: {score.get('overall_score')}")
    print(f"Running 4 LLM calls (advisor x2, coach x2). This will bill your account.")
    print()

    sonnet = _build_provider(args.sonnet_model)
    haiku = _build_provider(args.haiku_model)

    advisor_ctx = {
        "_cached": {"resume_profile": resume_profile},
        "job_id": job["job_id"],
        "resume_id": job["resume_id"],
        "job_description": job.get("job_description") or "",
        "final_review": final_review,
        "job_score": score,
        "career_track": "ic",
    }
    coach_ctx = {
        "_cached": {"resume_profile": resume_profile},
        "job_id": job["job_id"],
        "job_description": job.get("job_description") or "",
        "job_score": score,
        "research_context": {},
        "career_advice": {},
        "final_review": final_review,
    }

    print("Running career_advisor on Sonnet ...")
    s_advice, s_advice_usage = _run_advisor(sonnet, advisor_ctx)
    print(f"  {s_advice_usage['tokens_input']} in / {s_advice_usage['tokens_output']} out / ${s_advice_usage['cost_usd']:.4f}")

    print("Running career_advisor on Haiku ...")
    h_advice, h_advice_usage = _run_advisor(haiku, advisor_ctx)
    print(f"  {h_advice_usage['tokens_input']} in / {h_advice_usage['tokens_output']} out / ${h_advice_usage['cost_usd']:.4f}")

    print("Running interview_coach on Sonnet ...")
    s_prep, s_prep_usage = _run_coach(sonnet, coach_ctx)
    print(f"  {s_prep_usage['tokens_input']} in / {s_prep_usage['tokens_output']} out / ${s_prep_usage['cost_usd']:.4f}")

    print("Running interview_coach on Haiku ...")
    h_prep, h_prep_usage = _run_coach(haiku, coach_ctx)
    print(f"  {h_prep_usage['tokens_input']} in / {h_prep_usage['tokens_output']} out / ${h_prep_usage['cost_usd']:.4f}")

    advisor_results = {
        "sonnet": {"output": s_advice, "usage": s_advice_usage},
        "haiku":  {"output": h_advice, "usage": h_advice_usage},
    }
    coach_results = {
        "sonnet": {"output": s_prep, "usage": s_prep_usage},
        "haiku":  {"output": h_prep, "usage": h_prep_usage},
    }
    md = _render_report(job, score, advisor_results, coach_results)

    if args.out:
        out_path = Path(args.out)
    else:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = _OUT_DIR / f"coach_advisor_ab_{ts}_{uuid4().hex[:6]}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print()
    print(f"Wrote {out_path}")
    print(
        f"Total spend this run: "
        f"Sonnet ${s_advice_usage['cost_usd'] + s_prep_usage['cost_usd']:.4f} "
        f"vs Haiku ${h_advice_usage['cost_usd'] + h_prep_usage['cost_usd']:.4f}"
    )


if __name__ == "__main__":
    main()
