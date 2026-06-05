"""WorkflowGraph — LangGraph StateGraph wiring all 8 agents into the full workflow.

build_graph(deps) is the single entry point. The caller constructs WorkflowDependencies
with all agents, services, repositories, and the checkpointer, then calls build_graph()
to get a compiled, checkpointed graph ready for invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.agents.career_advisor import CareerAdvisor
from app.agents.fidelity_reviewer import FidelityReviewer
from app.agents.interview_coach import InterviewCoach
from app.agents.relevance_filter_agent import RelevanceFilterAgent
from app.agents.research_agent import ResearchAgent
from app.agents.resume_chat import ResumeChatAgent
from app.agents.resume_critic import ResumeCritic
from app.agents.resume_reviewer import ResumeReviewerAgent
from app.agents.review_auditor import ReviewAuditor
from app.agents.scoring_agent import ScoringAgent
from app.agents.tailoring_agent import TailoringAgent
from app.repositories.advice_repository import AdviceRepository
from app.repositories.job_repository import JobRepository
from app.repositories.resume_clinic_repository import ResumeClinicRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.score_repository import ScoreRepository
from app.repositories.tailoring_repository import TailoringRepository
from app.repositories.resume_repository import ResumeRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.job_discovery_service import JobDiscoveryService
from app.services.observability_service import ObservabilityService
from app.services.report_generator import ReportGenerator
from app.services.resume_parser import ResumeParser
from app.workflows import run_control
from app.workflows.graph_state import WorkflowGraphState
from app.workflows.nodes.await_job_selection import make_await_job_selection_node
from app.workflows.nodes.await_scoring_selection import make_await_scoring_selection_node
from app.workflows.nodes.career_advice import make_career_advice_node
from app.workflows.nodes.deep_review import make_deep_review_node
from app.workflows.nodes.discover_jobs import make_discover_jobs_node
from app.workflows.nodes.generate_report import make_generate_report_node
from app.workflows.nodes.interview_prep import make_interview_prep_node
from app.workflows.nodes.load_resume import make_load_resume_node
from app.workflows.nodes.register_run import make_register_run_node
from app.workflows.nodes.relevance_filter import make_relevance_filter_node
from app.workflows.nodes.score_jobs import make_score_jobs_node
from app.workflows.routers import (
    deep_review_gate,
    entry_router,
    interview_router,
    scoring_mode_gate,
)


@dataclass
class WorkflowDependencies:
    """All dependencies required to build and run the workflow graph."""
    # Agents
    research_agent: ResearchAgent
    scoring_agent: ScoringAgent
    # ADR-079: opt-in reasoning relevance pre-filter (in-graph, before scoring).
    relevance_filter_agent: RelevanceFilterAgent
    resume_critic: ResumeCritic
    review_auditor: ReviewAuditor
    career_advisor: CareerAdvisor
    interview_coach: InterviewCoach
    # Tailoring agents are not wired into the graph (tailoring is an out-of-graph
    # operation per ADR-055; the in-graph path was retired in ADR-059). They are
    # retained on the dependency bundle because the tailoring router resolves
    # them via get_deps().
    tailoring_agent: TailoringAgent
    fidelity_reviewer: FidelityReviewer
    # ADR-066: the Resume Clinic is also out-of-graph (job-agnostic; runs only
    # on user request). The clinic router resolves these via get_deps().
    resume_reviewer: ResumeReviewerAgent
    # ADR-068: the chat-revise loop's agent. Same get_deps() resolution path.
    resume_chat: ResumeChatAgent
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
    resume_clinic_repo: ResumeClinicRepository
    workflow_repo: WorkflowRepository
    resume_repo: ResumeRepository
    # Cross-cutting
    observability: ObservabilityService
    checkpointer: SqliteSaver
    # Optional per-run scraper factory; receives (urls, workflow_id) and returns
    # a BaseScraper-compatible object. workflow_id is forwarded so the scraper
    # can attribute its LLM-fallback cost to the run. None disables custom URL
    # ingestion.
    custom_url_scraper_factory: Callable[[list[str], str], Any] | None = None
    # ADR-064/065: optional per-run Adzuna scraper factory; receives
    # (roles, locations, exclude_senior) from the run's search_criteria/effective
    # config and returns a scraper (or None) so a profile's own roles drive
    # auto-discovery. None falls back to the built-in startup Adzuna.
    adzuna_scraper_factory: Callable[[list[str], list[str], bool], Any] | None = None
    # ADR-081: optional per-run ATS-direct scraper factory; receives the run's
    # roles and returns a list of BaseScraper-compatible objects (Greenhouse +
    # Lever) built from the configured company list. None / [] disables ATS
    # discovery (purely additive alongside Adzuna).
    ats_scraper_factory: Callable[[list[str]], list] | None = None


def _instrument_step(step_name: str, fn: Callable[[dict], dict], observability):
    """Wrap a LangGraph node so step_executions records its timing (ADR-074 Gap 2).

    Logs a started row before the node runs and a completed row after, or a failed
    row if it raises (then re-raises - the failure path is the node's, not the
    audit's). All logging routes through ObservabilityService, which swallows its
    own errors, so a broken audit write never breaks a workflow step.

    Cooperative cancellation (ADR-083): before the node runs, if a cancel was
    requested for this run, raise WorkflowCancelled *before* logging a started row
    (a node cancelled before it ran is not a failed step). The run wrappers in the
    workflows router catch this and finalize the run to `cancelled`. Granularity is
    one node - a node already executing finishes first.
    """
    def wrapped(state: dict) -> dict:
        workflow_id = state.get("workflow_id", "")
        if workflow_id and run_control.is_cancel_requested(workflow_id):
            raise run_control.WorkflowCancelled(workflow_id)
        step_id = observability.log_step_started(workflow_id, step_name)
        try:
            result = fn(state)
        except Exception:
            observability.log_step_failed(workflow_id, step_id, notes=step_name)
            raise
        observability.log_step_completed(workflow_id, step_id, 0, notes=step_name)
        return result

    return wrapped


def build_graph(deps: WorkflowDependencies):
    """Construct and compile the full workflow StateGraph.

    Returns a CompiledStateGraph backed by the SqliteSaver checkpointer.
    Invoke with: graph.invoke(initial_state, {"configurable": {"thread_id": workflow_id}})

    The graph runs end to end with no interrupt() pauses (ADR-059): job selection
    auto-selects, and tailoring is an out-of-graph operation (ADR-055).
    """
    graph = StateGraph(WorkflowGraphState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    # ADR-074 Gap 2: every node is wrapped by _instrument_step so the
    # step_executions table records node-level timing + transitions (distinct from
    # agent_events, which is per-LLM-call). Routing functions on conditional edges
    # are not nodes and stay uninstrumented (they are near-instant).
    nodes = {
        "register_run": make_register_run_node(deps.workflow_repo, deps.observability),
        "discover_jobs": make_discover_jobs_node(
            deps.discovery_service, deps.job_repo, deps.observability,
            custom_url_scraper_factory=deps.custom_url_scraper_factory,
            adzuna_scraper_factory=deps.adzuna_scraper_factory,
            ats_scraper_factory=deps.ats_scraper_factory),
        "load_resume": make_load_resume_node(
            deps.resume_parser, deps.observability, deps.resume_repo),
        "score_jobs": make_score_jobs_node(
            deps.research_agent, deps.scoring_agent, deps.score_repo, deps.observability),
        # ADR-079: opt-in cheap reasoning pre-filter on the auto-scoring branch.
        "relevance_filter": make_relevance_filter_node(
            deps.relevance_filter_agent, deps.observability),
        "await_job_selection": make_await_job_selection_node(),
        # Manual-selection mode (ADR-060): phase-1 terminal that parks discovered
        # jobs for the user to triage before any scoring spend.
        "await_scoring_selection": make_await_scoring_selection_node(deps.workflow_repo),
        "deep_review": make_deep_review_node(
            deps.resume_critic, deps.review_auditor, deps.review_repo, deps.observability),
        "career_advice": make_career_advice_node(
            deps.career_advisor, deps.advice_repo, deps.observability),
        "interview_prep": make_interview_prep_node(
            deps.interview_coach, deps.advice_repo, deps.observability),
        "generate_report": make_generate_report_node(
            deps.report_generator, deps.observability, deps.workflow_repo),
    }
    for name, fn in nodes.items():
        graph.add_node(name, _instrument_step(name, fn, deps.observability))

    # ── Entry point ───────────────────────────────────────────────────────────
    # Conditional (ADR-060): a normal kickoff starts at register_run; the phase-2
    # scoring trigger re-enters the same graph at score_jobs via phase="scoring".
    graph.set_conditional_entry_point(
        entry_router,
        {"register_run": "register_run", "score_jobs": "score_jobs"},
    )

    # ── Sequential edges ──────────────────────────────────────────────────────
    graph.add_edge("register_run",   "discover_jobs")
    graph.add_edge("discover_jobs",  "load_resume")
    # ADR-079: the relevance pre-filter feeds straight into scoring.
    graph.add_edge("relevance_filter", "score_jobs")
    graph.add_edge("score_jobs",     "await_job_selection")
    graph.add_edge("deep_review",    "career_advice")
    graph.add_edge("await_scoring_selection", END)
    graph.add_edge("generate_report", END)

    # ── Conditional edges ─────────────────────────────────────────────────────
    # After load_resume: manual-selection mode parks for user triage (ADR-060);
    # the relevance pre-filter (ADR-079) triages with a cheap LLM then scores;
    # otherwise score every discovered job as before.
    graph.add_conditional_edges(
        "load_resume",
        scoring_mode_gate,
        {
            "score_jobs": "score_jobs",
            "relevance_filter": "relevance_filter",
            "await_scoring_selection": "await_scoring_selection",
        },
    )

    # After auto-select: skip deep review entirely if no jobs qualified.
    graph.add_conditional_edges(
        "await_job_selection",
        deep_review_gate,
        {"deep_review": "deep_review", "generate_report": "generate_report"},
    )

    # After career_advice: run InterviewCoach if score is high enough, else finish.
    graph.add_conditional_edges(
        "career_advice",
        interview_router,
        {"interview_prep": "interview_prep", "generate_report": "generate_report"},
    )
    graph.add_edge("interview_prep", "generate_report")

    return graph.compile(checkpointer=deps.checkpointer)
