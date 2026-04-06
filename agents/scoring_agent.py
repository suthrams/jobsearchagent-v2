# agents/scoring_agent.py
# ─────────────────────────────────────────────────────────────────────────────
# Scores job postings against your profile across all active career tracks.
# Sends up to BATCH_SIZE jobs per Claude call for efficiency.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
from typing import Callable

from claude.client import ClaudeClient
from claude.prompt_loader import PromptLoader
from claude.response_parser import ResponseParser
from models.job import BatchJobScore, Job, ApplicationStatus, TrackScores
from models.profile import Profile
from models.config_schema import TracksConfig, SalaryConfig

logger = logging.getLogger(__name__)

# Number of jobs sent to Claude in a single API call.
# At 5, one call covers ~4000 input tokens and ~1200 output tokens.
BATCH_SIZE = 5

# Title substrings that disqualify a job from scoring, regardless of source.
# Mirrors the exclusion list in scrapers/adzuna.py so LinkedIn jobs are also filtered.
EXCLUDED_TITLE_KEYWORDS = [
    "presales",
    "pre-sales",
    "pre sales",
    "sales manager",
    "sales representative",
    "sales engineer",
    "account manager",
    "account executive",
    "java developer",
    "java engineer",
    "electrical engineer",
    "department lead",
    # Non-IT engineering disciplines
    "structural engineer",
    "mechanical engineer",
    "process engineer",
    "hydraulic engineer",
    "civil engineer",
    "landscape architect",
    "maintenance engineer",
    "facilities engineer",
    "hotel",
    "hvac",
]

# At least one of these must appear in the job description (case-insensitive).
# Acts as a universal tech-domain gate — catches non-IT roles that slip past
# the title filter (hotel maintenance, civil engineering, distribution ops, etc.).
# Adzuna descriptions are short snippets (~200 chars) but always name the domain.
TECH_DESCRIPTION_KEYWORDS = [
    "software", "cloud", "api", "microservice", "kubernetes", "docker",
    "aws", "azure", "gcp", "devops", "platform engineering",
    "python", "javascript", "typescript", ".net", "golang", "rust",
    "architecture", "distributed system", "data engineering", "data pipeline",
    "machine learning", "artificial intelligence", " ai ", "llm",
    "database", "backend", "frontend", "full stack", "fullstack",
    "infrastructure", "terraform", "ci/cd", "cicd",
    "saas", "paas", "iaas", "application development",
    "engineering team", "software engineer", "software development",
    "technology", "digital transformation", "technical leadership",
    "information technology", " it ", "tech stack",
    # IoT / connected devices / edge
    "iot", "internet of things", "mqtt", "edge computing",
    "embedded", "connected devices", "industrial iot", "iiot",
    "device management", "telemetry", "firmware",
]


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

    def score_batch(
        self,
        jobs: list[Job],
        profile: Profile,
        db=None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[Job]:
        """
        Scores a list of jobs in batches of BATCH_SIZE.

        Args:
            jobs        : Jobs to score — must already be in the database.
            profile     : The candidate's parsed Profile.
            db          : Optional Database — saves each job after scoring so
                          progress is not lost on cancellation.
            on_progress : Optional callback invoked before each batch with
                          (batch_number, total_batches).

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

        for batch_num, chunk in enumerate(chunks, 1):
            if on_progress:
                on_progress(batch_num, total_batches)

            try:
                scores_list = self._score_chunk(chunk, profile)
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

        logger.info(
            "Scoring complete — scored: %d | skipped: %d | failed: %d",
            scored_count,
            skipped,
            len(valid) - scored_count,
        )
        return jobs

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _score_chunk(self, jobs: list[Job], profile: Profile) -> list[TrackScores | None]:
        """
        Sends one batch of jobs to Claude and returns a TrackScores (or None)
        for each job in the same order.
        """
        active_tracks = self._active_track_names()
        if not active_tracks:
            logger.warning("No career tracks enabled — nothing to score")
            return [None] * len(jobs)

        # Build the <jobs> block — each job gets an index tag for mapping
        jobs_block = "\n\n".join(
            f'<job index="{i}">\n{self._job_summary(job)}\n</job>'
            for i, job in enumerate(jobs)
        )

        prompt = self.loader.load(
            "score_job",
            profile=self._profile_summary(profile),
            jobs=jobs_block,
            num_jobs=str(len(jobs)),
            tracks=", ".join(active_tracks),
            salary_min=str(self.salary_config.min_desired),
            salary_currency=self.salary_config.currency,
        )

        raw = self.client.call(
            system=prompt,
            user=f"Score these {len(jobs)} job posting(s) and return the JSON array.",
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
        return json.dumps(profile.model_dump(), indent=2, default=str)

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
