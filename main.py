# main.py
# ─────────────────────────────────────────────────────────────────────────────
# Entry point for the job search agent.
# Run this file to scrape new jobs, score them against your profile,
# and print a summary to the terminal.
#
# Usage:
#   python main.py                  # scrape + score all new jobs
#   python main.py --tailor <id>    # tailor resume for a specific job ID
#   python main.py --list           # show all scored jobs in the database
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

from claude.client import ClaudeClient
from claude.prompt_loader import PromptLoader
from claude.response_parser import ResponseParser
from agents.profile_agent import ProfileAgent
from agents.scoring_agent import ScoringAgent, BATCH_SIZE
from agents.tailoring_agent import TailoringAgent
from models.config_schema import AppConfig
from models.job import ApplicationStatus, CareerTrack
from scrapers.linkedin import LinkedInScraper
from scrapers.adzuna import AdzunaScraper
from scrapers.ladders import LaddersScraper
from storage.db import Database

# ─── Bootstrap ────────────────────────────────────────────────────────────────

# Load .env so ANTHROPIC_API_KEY is available before anything else runs
load_dotenv()

logging.getLogger("pdfminer").setLevel(logging.WARNING)

# Rich console for pretty terminal output
console = Console()

# Path to your resume PDF — update this if your resume lives elsewhere
RESUME_PATH = "resume.pdf"

# Path to the active config file
CONFIG_PATH = "config/config.yaml"


# ─── Logging ──────────────────────────────────────────────────────────────────


def setup_logging(log_dir: str) -> None:
    """
    Configures logging to both the terminal (WARNING+) and a log file (DEBUG+).
    Log files are written to the directory specified in config.yaml.

    Args:
        log_dir: Directory where log files are written.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / "run.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            # File handler — captures everything at DEBUG level
            logging.FileHandler(log_file, encoding="utf-8"),
            # Terminal handler — only shows WARNING and above to keep output clean
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


# ─── Config ───────────────────────────────────────────────────────────────────


def load_config() -> AppConfig:
    """
    Loads and validates config/config.yaml against the AppConfig Pydantic model.
    Exits with a helpful error message if the file is missing or invalid.

    Returns:
        A validated AppConfig object.
    """
    config_path = Path(CONFIG_PATH)
    if not config_path.exists():
        console.print(
            f"[red]Config file not found: {CONFIG_PATH}[/red]\n"
            "Copy config/config.example.yaml to config/config.yaml and fill in your preferences."
        )
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    try:
        return AppConfig.model_validate(raw)
    except Exception as e:
        console.print(f"[red]Config validation error:[/red] {e}")
        sys.exit(1)


# ─── Scraping ─────────────────────────────────────────────────────────────────


def run_scrapers(config: AppConfig) -> list:
    """
    Runs all enabled scrapers and returns a combined list of Job objects.
    Scrapers run independently — one failing does not stop the others.

    Args:
        config: The loaded AppConfig.

    Returns:
        Combined list of Job objects from all scrapers.
    """
    jobs = []

    scrapers = [
        LinkedInScraper(config.scrapers.linkedin.inbox_file),
        AdzunaScraper(config.scrapers.adzuna),
        LaddersScraper(config.scrapers.ladders),
    ]

    for scraper in scrapers:
        try:
            new_jobs = scraper.scrape()
            jobs.extend(new_jobs)
        except Exception as e:
            console.print(f"[yellow]Scraper {scraper.name} failed: {e}[/yellow]")

    return jobs


# ─── Display ──────────────────────────────────────────────────────────────────


def print_scored_jobs(jobs: list) -> None:
    """
    Prints a Rich table of scored jobs grouped by best track score.
    Also writes results to output/logs/results.txt for easy reference.
    Filters out jobs with no scores or best score below 50.
    """
    # Normalise status — handle both enum and string values
    scored = [
        j
        for j in jobs
        if str(j.status) in ("scored", "ApplicationStatus.SCORED")
        or j.status == ApplicationStatus.SCORED
        or j.status == "scored"
    ]

    if not scored:
        console.print("[yellow]No scored jobs found in database.[/yellow]")
        return

    # Filter out low scoring jobs — below 50 on all tracks is noise
    def best(j) -> int:
        scores = []
        if j.scores and j.scores.ic:
            scores.append(j.scores.ic.score)
        if j.scores and j.scores.architect:
            scores.append(j.scores.architect.score)
        if j.scores and j.scores.management:
            scores.append(j.scores.management.score)
        return max(scores) if scores else 0

    def recommended(j) -> bool:
        if j.scores:
            if j.scores.ic and j.scores.ic.recommended:
                return True
            if j.scores.architect and j.scores.architect.recommended:
                return True
            if j.scores.management and j.scores.management.recommended:
                return True
        return False

    # Sort by best score descending
    ranked = sorted([j for j in scored if best(j) >= 50], key=best, reverse=True)
    noise = len(scored) - len(ranked)

    console.print(f"\n[bold cyan]Job Search Results[/bold cyan]")
    console.print(
        f"Total scored: [cyan]{len(scored)}[/cyan] | "
        f"Showing score >= 50: [cyan]{len(ranked)}[/cyan] | "
        f"Filtered out (< 50): [dim]{noise}[/dim]\n"
    )

    if not ranked:
        console.print("[yellow]No jobs scored above 50.[/yellow]")
        return

    # ── Rich terminal table ──────────────────────────────────────────────────
    table = Table(
        title="Top Jobs by Score",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("ID", style="dim", width=5)
    table.add_column("Title", width=35)
    table.add_column("Company", width=22)
    table.add_column("IC", justify="center", width=5)
    table.add_column("Arch", justify="center", width=5)
    table.add_column("Mgmt", justify="center", width=5)
    table.add_column("Best", justify="center", width=5)
    table.add_column("Rec", justify="center", width=4)
    table.add_column("Source", justify="center", width=8)

    for job in ranked:
        ic_s = str(job.scores.ic.score) if job.scores and job.scores.ic else "-"
        arch_s = (
            str(job.scores.architect.score)
            if job.scores and job.scores.architect
            else "-"
        )
        mgmt_s = (
            str(job.scores.management.score)
            if job.scores and job.scores.management
            else "-"
        )
        best_s = str(best(job))
        rec_s = "[green]Y[/green]" if recommended(job) else ""

        # Colour the best score
        score_colour = (
            "green" if best(job) >= 75 else "yellow" if best(job) >= 60 else "white"
        )

        table.add_row(
            str(job.id or ""),
            job.title[:33],
            job.company[:20],
            ic_s,
            arch_s,
            mgmt_s,
            f"[{score_colour}]{best_s}[/{score_colour}]",
            rec_s,
            job.source if isinstance(job.source, str) else job.source.value,
        )

    console.print(table)

    # ── Write to file ────────────────────────────────────────────────────────
    _write_results_file(ranked, best, recommended)


def _write_results_file(jobs: list, best_fn, rec_fn) -> None:
    """
    Writes a human-readable results file to output/logs/results.txt.
    Each job gets a full block with title, company, URL, scores, and summaries.
    """
    import os
    from datetime import datetime as dt

    out_dir = Path("output/logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "results.txt"

    lines = [
        "=" * 70,
        f"JOB SEARCH RESULTS — {dt.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total jobs shown (score >= 50): {len(jobs)}",
        "=" * 70,
        "",
    ]

    for i, job in enumerate(jobs, 1):
        lines += [
            f"#{i}  {job.title}",
            f"    Company  : {job.company}",
            f"    Location : {job.location or 'Not specified'}",
            f"    Source   : {job.source if isinstance(job.source, str) else job.source.value}",
            f"    URL      : {job.url}",
            f"    Best Score: {best_fn(job)} | Recommended: {'Yes' if rec_fn(job) else 'No'}",
        ]

        if job.scores:
            if job.scores.ic:
                lines.append(
                    f"    IC ({job.scores.ic.score})         : {job.scores.ic.summary}"
                )
            if job.scores.architect:
                lines.append(
                    f"    Architect ({job.scores.architect.score})  : {job.scores.architect.summary}"
                )
            if job.scores.management:
                lines.append(
                    f"    Management ({job.scores.management.score}): {job.scores.management.summary}"
                )

        if job.salary:
            sal = job.salary
            lines.append(
                f"    Salary   : {sal.currency} {sal.min or '?'} – {sal.max or '?'}"
            )

        lines += ["", "-" * 70, ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[green]Results written to:[/green] {out_path.resolve()}")


def _best_score(job) -> int:
    """Returns the highest score across all tracks for a job."""
    scores = []
    if job.scores.ic:
        scores.append(job.scores.ic.score)
    if job.scores.architect:
        scores.append(job.scores.architect.score)
    if job.scores.management:
        scores.append(job.scores.management.score)
    return max(scores) if scores else 0


def _is_recommended(job) -> bool:
    """Returns True if any track has recommended=True."""
    if job.scores.ic and job.scores.ic.recommended:
        return True
    if job.scores.architect and job.scores.architect.recommended:
        return True
    if job.scores.management and job.scores.management.recommended:
        return True
    return False


# ─── Cost estimation ──────────────────────────────────────────────────────────

# Claude Sonnet 4.6 pricing (USD per million tokens, as of 2025)
_INPUT_COST_PER_MTOK = 3.00
_OUTPUT_COST_PER_MTOK = 15.00

# Rough token estimates per batch of BATCH_SIZE jobs
_EST_INPUT_TOKENS_PER_BATCH = 4_000   # profile + 5 job descriptions + prompt
_EST_OUTPUT_TOKENS_PER_BATCH = 1_200  # 5 score objects as JSON


def estimate_scoring_cost(num_jobs: int, batch_size: int) -> tuple[float, int]:
    """
    Returns (estimated_cost_usd, num_batches) for scoring num_jobs jobs.
    Uses conservative token estimates based on observed Sonnet 4.6 usage.
    """
    num_batches = math.ceil(num_jobs / batch_size)
    cost = num_batches * (
        _EST_INPUT_TOKENS_PER_BATCH / 1_000_000 * _INPUT_COST_PER_MTOK
        + _EST_OUTPUT_TOKENS_PER_BATCH / 1_000_000 * _OUTPUT_COST_PER_MTOK
    )
    return cost, num_batches


# ─── Main commands ────────────────────────────────────────────────────────────


def cmd_scrape_and_score(config: AppConfig, db: Database, agents: dict) -> None:
    """
    Default command — scrape new jobs, score them, save to DB, print summary.

    Steps:
      1. Run all scrapers
      2. Insert new jobs into the database (duplicates are ignored)
      3. Score all new (unscored) jobs with Claude
      4. Update the database with scores
      5. Print the results table
    """
    console.print("[bold]Scraping jobs...[/bold]")
    raw_jobs = run_scrapers(config)
    console.print(f"Found [cyan]{len(raw_jobs)}[/cyan] jobs across all sources")

    # Insert into DB — deduplicates by URL and by title+company
    new_count = 0
    for job in raw_jobs:
        if db.get_by_url(job.url) or db.get_by_title_company(job.title, job.company):
            continue
        db.insert_job(job)
        new_count += 1

    console.print(f"[cyan]{new_count}[/cyan] new jobs added to database")

    # Score all unscored jobs
    unscored = db.get_by_status(ApplicationStatus.NEW)
    if not unscored:
        console.print("[yellow]No new jobs to score.[/yellow]")
    else:
        est_cost, num_batches = estimate_scoring_cost(len(unscored), BATCH_SIZE)
        console.print(
            f"\n[bold]Scoring plan:[/bold] [cyan]{len(unscored)}[/cyan] jobs in "
            f"[cyan]{num_batches}[/cyan] batch(es) of up to [cyan]{BATCH_SIZE}[/cyan]\n"
            f"Estimated API cost: [yellow]~${est_cost:.2f}[/yellow] (Sonnet 4.6)\n"
        )
        confirm = input("Continue? [y/N]: ").strip().lower()
        if confirm != "y":
            console.print("[yellow]Scoring cancelled.[/yellow]")
        else:
            profile = agents["profile"].load(RESUME_PATH)

            def on_progress(batch_num: int, total_batches: int) -> None:
                console.print(f"  Scoring batch [cyan]{batch_num}[/cyan]/[cyan]{total_batches}[/cyan]...")

            agents["scoring"].score_batch(unscored, profile, db=db, on_progress=on_progress)

    # Print all scored jobs
    # Print all scored jobs
    all_scored = db.get_by_status(ApplicationStatus.SCORED)
    if not all_scored:
        # Also try loading all jobs in case status comparison is off
        all_jobs = db.get_all()
        print_scored_jobs(all_jobs)
    else:
        print_scored_jobs(all_scored)


def cmd_tailor(config: AppConfig, db: Database, agents: dict, job_id: int) -> None:
    """
    Tailors your resume for a specific job by its database ID.

    Args:
        job_id: The integer ID shown in the scored jobs table.
    """
    job = db.get_by_id(job_id)
    if not job:
        console.print(f"[red]No job found with ID {job_id}[/red]")
        return

    console.print(
        f"Tailoring resume for: [bold]{job.title}[/bold] at [bold]{job.company}[/bold]"
    )

    # Ask which track to optimise for
    console.print("Which track? [1] IC  [2] Architect  [3] Management")
    choice = input("> ").strip()
    track_map = {
        "1": CareerTrack.IC,
        "2": CareerTrack.ARCHITECT,
        "3": CareerTrack.MANAGEMENT,
    }
    track = track_map.get(choice)
    if not track:
        console.print("[red]Invalid choice[/red]")
        return

    profile = agents["profile"].load(RESUME_PATH)
    result = agents["tailoring"].tailor(job, profile, track)

    console.print(f"\n[green]Tailored resume saved to:[/green] {result.output_path}")
    console.print(f"\n[bold]ATS Keywords:[/bold] {', '.join(result.keywords)}")
    if result.gaps:
        console.print(f"\n[yellow]Gaps to address:[/yellow]")
        for gap in result.gaps:
            console.print(f"  • {gap}")

    # Mark as applied if the user confirms
    apply = input("\nMark as APPLIED? (y/n): ").strip().lower()
    if apply == "y":
        from datetime import datetime

        job.applied_at = datetime.utcnow()
        job.status = ApplicationStatus.APPLIED
        db.update_job(job)
        console.print("[green]Status updated to APPLIED[/green]")


def cmd_list(db: Database) -> None:
    """Lists all jobs currently in the database regardless of status."""
    all_jobs = db.get_all()
    console.print(f"Total jobs in database: {len(all_jobs)}")

    # Debug — show status distribution
    from collections import Counter

    statuses = Counter(str(j.status) for j in all_jobs)
    console.print(f"Status breakdown: {dict(statuses)}")

    print_scored_jobs(all_jobs)


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    """
    Parses CLI arguments and dispatches to the appropriate command.
    """
    parser = argparse.ArgumentParser(description="Job Search Agent")
    parser.add_argument(
        "--tailor", type=int, metavar="ID", help="Tailor resume for job ID"
    )
    parser.add_argument("--list", action="store_true", help="List all scored jobs")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch Streamlit dashboard after run completes",
    )
    args = parser.parse_args()

    # Load config and set up logging
    config = load_config()
    setup_logging(config.storage.logs_dir)

    logger = logging.getLogger(__name__)
    logger.info("Job search agent starting")

    # Initialise shared components
    db = Database(config.storage.database)
    client = ClaudeClient(config.claude)
    loader = PromptLoader()
    rparser = ResponseParser()

    agents = {
        "profile": ProfileAgent(client, loader, rparser),
        "scoring": ScoringAgent(client, loader, rparser, config.tracks, config.salary),
        "tailoring": TailoringAgent(
            client, loader, rparser, config.storage.tailored_resumes_dir
        ),
    }

    try:
        if args.tailor:
            cmd_tailor(config, db, agents, args.tailor)
        elif args.list:
            cmd_list(db)
        else:
            cmd_scrape_and_score(config, db, agents)
    finally:
        db.close()
        logger.info("Job search agent finished")

    if args.dashboard:
        import subprocess
        console.print("\n[bold cyan]Launching Streamlit dashboard...[/bold cyan]")
        subprocess.Popen(["streamlit", "run", "dashboard.py"])
        console.print(
            "[green]Dashboard opening at http://localhost:8501[/green]\n"
            "[dim]Stop it with Ctrl+C in the Streamlit terminal.[/dim]"
        )


if __name__ == "__main__":
    main()
