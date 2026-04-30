"""ReportGenerator — assembles Markdown reports from repository data.

Reads from repositories, not from in-memory WorkflowState, so reports can be
regenerated after a run has completed. No LLM calls.
"""
import json
import logging
import uuid

from app.repositories.advice_repository import AdviceRepository
from app.repositories.job_repository import JobRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Assembles Markdown reports from scored jobs, reviews, advice, and tailoring data."""

    def __init__(
        self,
        score_repo: ScoreRepository,
        review_repo: ReviewRepository,
        advice_repo: AdviceRepository,
        tailoring_repo: TailoringRepository,
        report_repo: ReportRepository,
        job_repo: JobRepository,
    ) -> None:
        self._scores = score_repo
        self._reviews = review_repo
        self._advice = advice_repo
        self._tailoring = tailoring_repo
        self._reports = report_repo
        self._jobs = job_repo

    def generate_run_summary(self, workflow_id: str) -> str:
        """Assemble a full Markdown run report, persist it, and return the Markdown string."""
        scores = self._scores.get_by_workflow_run(workflow_id)

        lines: list[str] = [
            "# Job Search Run Summary",
            "",
            f"**Run ID:** `{workflow_id}`",
            "",
        ]

        if scores:
            lines += [
                "## Jobs Scored",
                "",
                "| Title | Company | Overall | Technical | Architecture | Leadership |",
                "|---|---|---|---|---|---|",
            ]
            for row in scores:
                job = self._jobs.get_by_id(row["job_id"])
                title = job["title"] if job else row["job_id"]
                company = job["company"] if job else "—"
                s = json.loads(row["score_json"])
                lines.append(
                    f"| {title} | {company}"
                    f" | {s.get('overall_score', '—')}"
                    f" | {s.get('technical_score', '—')}"
                    f" | {s.get('architecture_score', '—')}"
                    f" | {s.get('leadership_score', '—')} |"
                )
            lines.append("")

        for row in scores:
            job_section = self.generate_job_report(workflow_id, row["job_id"])
            if job_section:
                lines.append(job_section)

        markdown = "\n".join(lines)
        self._reports.create(
            report_id=str(uuid.uuid4()),
            workflow_run_id=workflow_id,
            report_markdown=markdown,
        )
        return markdown

    def generate_job_report(self, workflow_id: str, job_id: str) -> str:
        """Assemble a per-job Markdown section. Returns empty string if no data exists."""
        job = self._jobs.get_by_id(job_id)

        # Find the score for this workflow run + job
        all_job_scores = self._scores.get_by_job(job_id)
        score_row = next(
            (r for r in all_job_scores if r.get("workflow_run_id") == workflow_id), None
        )

        if not score_row and not job:
            return ""

        title = job["title"] if job else job_id
        company = job["company"] if job else "—"
        lines: list[str] = ["---", "", f"## {title} — {company}", ""]

        if score_row:
            s = json.loads(score_row["score_json"])
            lines += [
                "### Fit Scores",
                "",
                "| Dimension | Score |",
                "|---|---|",
                f"| Overall | {s.get('overall_score', '—')} |",
                f"| Technical | {s.get('technical_score', '—')} |",
                f"| Architecture | {s.get('architecture_score', '—')} |",
                f"| Leadership | {s.get('leadership_score', '—')} |",
                f"| Domain | {s.get('domain_score', '—')} |",
                "",
            ]
            if s.get("match_summary"):
                lines += ["### Match Summary", "", s["match_summary"], ""]
            if s.get("strengths"):
                lines += ["### Strengths", ""]
                lines += [f"- {item}" for item in s["strengths"]]
                lines.append("")
            if s.get("gaps"):
                lines += ["### Gaps", ""]
                lines += [f"- {item}" for item in s["gaps"]]
                lines.append("")

        review_row = self._reviews.get_review_by_run_job(workflow_id, job_id)
        if review_row:
            r = json.loads(review_row["review_json"])
            if r.get("overall_fit_summary"):
                lines += ["### Resume Analysis", "", r["overall_fit_summary"], ""]
            critical = r.get("critical_gaps", [])
            if critical:
                lines += ["**Critical Gaps:**", ""]
                lines += [f"- {g}" for g in critical]
                lines.append("")
            resume_gaps = r.get("resume_only_gaps", [])
            career_gaps = r.get("career_gaps_observed", [])
            if resume_gaps:
                lines += ["**Resume Gaps:**", ""]
                lines += [f"- {g}" for g in resume_gaps]
                lines.append("")
            if career_gaps:
                lines += ["**Career Gaps:**", ""]
                lines += [f"- {g}" for g in career_gaps]
                lines.append("")

        advice_row = self._advice.get_advice_by_run_job(workflow_id, job_id)
        if advice_row:
            a = json.loads(advice_row["advice_json"])
            if a.get("positioning_summary"):
                lines += ["### Career Advice", "", a["positioning_summary"], ""]
            if a.get("recommended_next_action"):
                lines += [f"**Recommended next action:** {a['recommended_next_action']}", ""]

        prep_row = self._advice.get_prep_by_run_job(workflow_id, job_id)
        if prep_row:
            p = json.loads(prep_row["prep_json"])
            plan = p.get("seven_day_prep_plan", [])
            if plan:
                lines += ["### 7-Day Interview Prep", ""]
                lines += [f"{i}. {day}" for i, day in enumerate(plan, 1)]
                lines.append("")

        tailoring_row = self._tailoring.get_by_run_job(workflow_id, job_id)
        if tailoring_row and tailoring_row.get("approved"):
            t = json.loads(tailoring_row["tailored_json"])
            bullets = t.get("experience_bullet_suggestions", [])
            if bullets:
                lines += ["### Approved Tailoring Suggestions", ""]
                for b in bullets:
                    lines.append(f"- **Original:** {b.get('original_text', '')}")
                    lines.append(f"  **Suggested:** {b.get('suggested_text', '')}")
                lines.append("")

        return "\n".join(lines)
