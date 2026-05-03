"""WorkflowGraph — LangGraph StateGraph wiring all 8 agents into the full workflow.

build_graph(deps) is the single entry point. The caller constructs WorkflowDependencies
with all agents, services, repositories, and the checkpointer, then calls build_graph()
to get a compiled, checkpointed graph ready for invocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.research_agent import ResearchAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.repositories.advice_repository import AdviceRepository
from app.repositories.job_repository import JobRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.services.resume_parser import ResumeParser
from app.workflows.graph_state import WorkflowGraphState
from app.workflows.nodes.await_job_selection import make_await_job_selection_node
from app.workflows.nodes.await_tailoring_approval import make_await_tailoring_approval_node
from app.workflows.nodes.career_advice import make_career_advice_node
from app.workflows.nodes.deep_review import make_deep_review_node
from app.workflows.nodes.discover_jobs import make_discover_jobs_node
from app.workflows.nodes.generate_report import make_generate_report_node
from app.workflows.nodes.interview_prep import make_interview_prep_node
from app.workflows.nodes.load_resume import make_load_resume_node
from app.workflows.nodes.register_run import make_register_run_node
from app.workflows.nodes.score_jobs import make_score_jobs_node
from app.workflows.nodes.tailoring import make_tailoring_node
from app.workflows.routers import deep_review_gate, interview_router, tailoring_router


@dataclass
class WorkflowDependencies:
    """All dependencies required to build and run the workflow graph."""
    # Agents
    research_agent: ResearchAgent
    scoring_agent: ScoringAgent
    resume_critic: ResumeCritic
    review_auditor: ReviewAuditor
    career_advisor: CareerAdvisor
    interview_coach: InterviewCoach
    tailoring_agent: TailoringAgent
    fidelity_reviewer: FidelityReviewer
    # Services
    discovery_service: JobDiscoveryService
    resume_parser: ResumeParser
    report_generator: ReportGenerator
    # Repositories
    job_repo: JobRepository
    score_repo: ScoreRepository
    advice_repo: AdviceRepository
    review_repo: ReviewRepository
    tailoring_repo: TailoringRepository
    workflow_repo: WorkflowRepository
    resume_repo: ResumeRepository
    # Cross-cutting
    observability: ObservabilityService
    checkpointer: SqliteSaver
    # Optional per-run scraper factory; receives the URL list and returns a
    # BaseScraper-compatible object. None disables custom URL ingestion.
    custom_url_scraper_factory: Callable[[list[str]], Any] | None = None


def build_graph(deps: WorkflowDependencies):
    """Construct and compile the full workflow StateGraph.

    Returns a CompiledStateGraph backed by the SqliteSaver checkpointer.
    Invoke with: graph.invoke(initial_state, {"configurable": {"thread_id": workflow_id}})
    Resume with: graph.invoke(Command(resume=decision), {"configurable": {"thread_id": workflow_id}})
    """
    graph = StateGraph(WorkflowGraphState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    graph.add_node("register_run", make_register_run_node(deps.workflow_repo))

    graph.add_node("discover_jobs", make_discover_jobs_node(
        deps.discovery_service, deps.job_repo, deps.observability,
        custom_url_scraper_factory=deps.custom_url_scraper_factory))

    graph.add_node("load_resume", make_load_resume_node(
        deps.resume_parser, deps.observability, deps.resume_repo))

    graph.add_node("score_jobs", make_score_jobs_node(
        deps.research_agent, deps.scoring_agent, deps.score_repo, deps.observability))

    graph.add_node("await_job_selection", make_await_job_selection_node())

    graph.add_node("deep_review", make_deep_review_node(
        deps.resume_critic, deps.review_auditor, deps.review_repo, deps.observability))

    graph.add_node("career_advice", make_career_advice_node(
        deps.career_advisor, deps.advice_repo, deps.observability))

    graph.add_node("interview_prep", make_interview_prep_node(
        deps.interview_coach, deps.advice_repo, deps.observability))

    graph.add_node("tailoring", make_tailoring_node(
        deps.tailoring_agent, deps.fidelity_reviewer, deps.tailoring_repo, deps.observability))

    graph.add_node("await_tailoring_approval", make_await_tailoring_approval_node())

    graph.add_node("generate_report", make_generate_report_node(
        deps.report_generator, deps.observability, deps.workflow_repo))

    # ── Entry point ───────────────────────────────────────────────────────────
    graph.set_entry_point("register_run")

    # ── Sequential edges ──────────────────────────────────────────────────────
    graph.add_edge("register_run",             "discover_jobs")
    graph.add_edge("discover_jobs",            "load_resume")
    graph.add_edge("load_resume",              "score_jobs")
    graph.add_edge("score_jobs",               "await_job_selection")
    graph.add_edge("deep_review",              "career_advice")
    graph.add_edge("tailoring",                "await_tailoring_approval")
    graph.add_edge("await_tailoring_approval", "generate_report")
    graph.add_edge("generate_report",          END)

    # ── Conditional edges ─────────────────────────────────────────────────────
    # After auto-select: skip deep review entirely if no jobs qualified.
    graph.add_conditional_edges(
        "await_job_selection",
        deep_review_gate,
        {"deep_review": "deep_review", "generate_report": "generate_report"},
    )

    # After career_advice: run InterviewCoach if score is high enough, else check tailoring
    graph.add_conditional_edges(
        "career_advice",
        interview_router,
        {"interview_prep": "interview_prep", "tailoring_check": "tailoring_check_node"},
    )

    # Intermediate routing node to check tailoring flag (after interview_prep or after career_advice skip)
    graph.add_node("tailoring_check_node", lambda state: {})
    graph.add_edge("interview_prep", "tailoring_check_node")
    graph.add_conditional_edges(
        "tailoring_check_node",
        tailoring_router,
        {"tailoring": "tailoring", "generate_report": "generate_report"},
    )

    return graph.compile(checkpointer=deps.checkpointer)
