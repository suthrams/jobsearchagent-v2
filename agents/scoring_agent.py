# agents/scoring_agent.py
# ─────────────────────────────────────────────────────────────────────────────
# Scores job postings against your profile across all active career tracks.
# Sends up to BATCH_SIZE jobs per Claude call for efficiency.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from claude.client import ClaudeClient
from claude.prompt_loader import PromptLoader
from claude.response_parser import ResponseParser
from models.job import BatchJobScore, Job, ApplicationStatus, TrackScores
from models.profile import Profile
from models.config_schema import TracksConfig, SalaryConfig
from models.filters import EXCLUDED_TITLE_KEYWORDS, TECH_DESCRIPTION_KEYWORDS

logger = logging.getLogger(__name__)

# Number of jobs sent to Claude in a single API call.
# At 10, one call covers ~6500 input tokens and ~3000 output tokens.
# Doubling from 5 halves round-trips and keeps the ephemeral cache window tighter.
BATCH_SIZE = 10


class ScoringAgent:
    """
    Scores job postings against the candidate's profile in batches.

    For each batch of up to BATCH_SIZE jobs:
      1. Skip stale jobs and jobs with no description
      2. Render the score_job prompt with profile + all jobs in the batch
      3. Call Claude once and parse the JSON array response
      4. Map scores back to jobs by job_index
      5. Persist each scored job immediately via the optional db handle
    """

    def __init__(
        self,
        client: ClaudeClient,
        loader: PromptLoader,
        parser: ResponseParser,
        tracks_config: TracksConfig,
        salary_config: SalaryConfig,
    ) -> None:
        self.client = client
        self.loader = loader
        self.parser = parser
        self.tracks_config = tracks_config
        self.salary_config = salary_config
        # Populated after each score_batch() call — read by main.py for run stats
        self.last_run_stats: dict = {
            "elapsed_score_s": 0.0,
            "avg_batch_latency_s": 0.0,
            "jobs_per_second": 0.0,
        }

    def score_batch(
        self,
        jobs: list[Job],
        profile: Profile,
        db=None,
        on_progress: Callable[[int, int, list[Job]], None] | None = None,
    ) -> list[Job]:
        """
        Scores a list of jobs in batches of BATCH_SIZE.

        Args:
            jobs        : Jobs to score — must already be in the database.
            profile     : The candidate's parsed Profile.
            db          : Optional Database — saves each job after scoring so
                          progress is not lost on cancellation.
            on_progress : Optional callback invoked before each batch with
                          (batch_number, total_batches, batch_jobs). The third
                          argument is the list of Job objects in the current batch,
                          allowing callers to display titles as scoring progresses.

        Returns:
            The same list with scores and status populated on eligible jobs.
        """
        # Split into valid (scoreable) and invalid (stale / no description / excluded)
        valid: list[Job] = []
        for job in jobs:
            if job.is_stale:
                logger.info("Skipping stale job: %s at %s", job.title, job.company)
            elif not job.description:
                logger.warning("No description — skipping: %s at %s", job.title, job.company)
            elif self._is_excluded_title(job.title):
                logger.info("Skipping excluded title: %s at %s", job.title, job.company)
            elif not self._has_tech_description(job.description):
                logger.info("Skipping non-tech description: %s at %s", job.title, job.company)
            else:
                valid.append(job)

        skipped = len(jobs) - len(valid)
        if skipped:
            logger.info("Skipped %d jobs (stale or no description)", skipped)

        if not valid:
            logger.info("No scoreable jobs in this batch")
            return jobs

        chunks = [valid[i : i + BATCH_SIZE] for i in range(0, len(valid), BATCH_SIZE)]
        total_batches = len(chunks)
        scored_count = 0
        batch_latencies: list[float] = []

        t_score_start = time.perf_counter()

        for batch_num, chunk in enumerate(chunks, 1):
            if on_progress:
                on_progress(batch_num, total_batches, chunk)

            t_batch_start = time.perf_counter()
            try:
                scores_list = self._score_chunk(chunk, profile)
                batch_latency = time.perf_counter() - t_batch_start
                batch_latencies.append(batch_latency)
                logger.debug("Batch %d/%d latency: %.2fs", batch_num, total_batches, batch_latency)

                for job, track_scores in zip(chunk, scores_list):
                    if track_scores is not None:
                        job.scores = track_scores
                        job.status = ApplicationStatus.SCORED
                        if db:
                            db.update_job(job)
                        scored_count += 1
                        logger.info(
                            "Scored: %s at %s | ic=%s | arch=%s | mgmt=%s",
                            job.title,
                            job.company,
                            track_scores.ic.score if track_scores.ic else "n/a",
                            track_scores.architect.score if track_scores.architect else "n/a",
                            track_scores.management.score if track_scores.management else "n/a",
                        )
            except Exception as e:
                logger.error("Batch %d/%d failed: %s", batch_num, total_batches, e)

        elapsed_score_s = time.perf_counter() - t_score_start
        avg_batch_latency_s = sum(batch_latencies) / len(batch_latencies) if batch_latencies else 0.0
        jobs_per_second = scored_count / elapsed_score_s if elapsed_score_s > 0 else 0.0

        self.last_run_stats = {
            "elapsed_score_s": elapsed_score_s,
            "avg_batch_latency_s": avg_batch_latency_s,
            "jobs_per_second": jobs_per_second,
        }

        logger.info(
            "Scoring complete: scored=%d skipped=%d failed=%d elapsed=%.1fs avg_batch=%.1fs throughput=%.2f jobs/s",
            scored_count,
            skipped,
            len(valid) - scored_count,
            elapsed_score_s,
            avg_batch_latency_s,
            jobs_per_second,
        )
        return jobs

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _score_chunk(self, jobs: list[Job], profile: Profile) -> list[TrackScores | None]:
        """
        Sends one batch of jobs to Claude and returns a TrackScores (or None)
        for each job in the same order.

        Prompt caching strategy:
        - System prompt contains all static content (instructions + profile).
          It is marked with cache_control so the Anthropic API reuses the compiled
          KV cache on batches 2-N, paying only 0.1x the normal input token price.
        - The <jobs> block changes per batch so it goes in the user message.
        """
        active_tracks = self._active_track_names()
        if not active_tracks:
            logger.warning("No career tracks enabled — nothing to score")
            return [None] * len(jobs)

        # Build the <jobs> block for the user message — each job gets an index tag
        jobs_block = "\n\n".join(
            f'<job index="{i}">\n{self._job_summary(job)}\n</job>'
            for i, job in enumerate(jobs)
        )

        # System prompt: fully static across all batches — no per-batch variables.
        # num_jobs is intentionally excluded: including it caused a cache miss on the
        # last batch whenever len(jobs) != BATCH_SIZE (i.e. almost every run).
        prompt = self.loader.load(
            "score_job",
            profile=self._profile_summary(profile),
            tracks=", ".join(active_tracks),
            salary_min=str(self.salary_config.min_desired),
            salary_currency=self.salary_config.currency,
        )

        # Wrap as a cached content block — subsequent batches reuse at ~90% cost reduction
        system = [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # Jobs go in the user message — the only part that changes between batches.
        # Job count is stated here (not in the cached system prompt) so the system
        # prompt stays byte-identical across all batches, maximising cache hits.
        user = (
            f"<jobs>\n{jobs_block}\n</jobs>\n\n"
            f"Score these {len(jobs)} job(s) and return the JSON array."
        )

        raw = self.client.call(
            system=system,
            user=user,
            operation="job_scoring",
        )

        batch_results: list[BatchJobScore] = self.parser.parse_list(raw, BatchJobScore)

        # Map by job_index so we're safe if Claude reorders items
        score_map = {item.job_index: item for item in batch_results}

        results: list[TrackScores | None] = []
        for i in range(len(jobs)):
            item = score_map.get(i)
            if item is not None:
                results.append(
                    TrackScores(
                        ic=item.ic,
                        architect=item.architect,
                        management=item.management,
                    )
                )
            else:
                logger.warning("No score returned for job_index %d (%s)", i, jobs[i].title)
                results.append(None)

        return results

    @staticmethod
    def _is_excluded_title(title: str) -> bool:
        """Returns True if the title matches any entry in EXCLUDED_TITLE_KEYWORDS."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in EXCLUDED_TITLE_KEYWORDS)

    @staticmethod
    def _has_tech_description(description: str) -> bool:
        """
        Returns True if the description contains at least one tech keyword.
        Universal gate that catches non-IT roles regardless of title —
        hotel maintenance, civil/mechanical engineering, distribution ops, etc.
        """
        desc_lower = description.lower()
        return any(kw in desc_lower for kw in TECH_DESCRIPTION_KEYWORDS)

    def _active_track_names(self) -> list[str]:
        tracks = []
        if self.tracks_config.ic:
            tracks.append("ic")
        if self.tracks_config.architect:
            tracks.append("architect")
        if self.tracks_config.management:
            tracks.append("management")
        return tracks

    @staticmethod
    def _profile_summary(profile: Profile) -> str:
        """
        Compact profile representation optimised for job scoring.

        Includes only fields that directly affect scoring decisions:
        - Current title and total experience (seniority signals)
        - Skills and technologies (tech stack matching)
        - Certifications (role-specific requirements)
        - Experience history with technologies and descriptions (domain fit)
        - Headline and summary (context and self-positioning)

        Excludes: email, location, education, raw start/end years.
        Computes `years` per role so Claude can assess seniority directly.

        This trimmed representation is also more cache-efficient — smaller
        payload means a smaller cache write on the first batch call.
        """
        data = {
            "current_title": profile.current_title,
            "total_years_experience": round(profile.total_years_experience, 1),
            "headline": profile.headline,
            "summary": profile.summary,
            "skills": profile.skills,
            "certifications": [
                {"name": c.name, "issuer": c.issuer}
                for c in profile.certifications
            ],
            "experience": [
                {
                    "title": e.title,
                    "company": e.company,
                    "years": round(e.years, 1),
                    "technologies": e.technologies,
                    "description": e.description,
                }
                for e in profile.experience
            ],
        }
        return json.dumps(data, indent=2, default=str)

    @staticmethod
    def _job_summary(job: Job) -> str:
        parts = [
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location or 'Not specified'}",
            f"Work mode: {job.work_mode or 'Not specified'}",
        ]

        if job.salary:
            sal = job.salary
            parts.append(f"Salary: {sal.currency} {sal.min or '?'} – {sal.max or '?'}")

        if job.posted_at:
            parts.append(f"Posted: {job.posted_at.strftime('%Y-%m-%d')}")

        parts.append(f"\nDescription:\n{job.description}")

        return "\n".join(parts)
