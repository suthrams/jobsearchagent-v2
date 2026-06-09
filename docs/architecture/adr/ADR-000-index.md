# Architecture Decision Records

This folder tracks architecture decisions for jobsearchagent-v2.

## ADR Index

| ADR | Title | Status |
|---|---|---|
| ADR-001 | [Keep v1 Stable and Use v2 for Refactor](ADR-001-keep-v1-stable-and-use-v2-for-refactor.md) | Accepted |
| ADR-002 | [Orchestrator-Mediated Agent Coordination with Shared State](ADR-002-orchestrator-mediated-agent-coordination-with-shared-state.md) | Accepted |
| ADR-003 | [Separate Frontend and Backend Responsibilities](ADR-003-separate-frontend-and-backend-responsibilities.md) | Accepted |
| ADR-004 | [Backend Owns Workflow Orchestration](ADR-004-backend-owns-workflow-orchestration.md) | Accepted |
| ADR-005 | [Use Specialized Agents](ADR-005-use-specialized-agents.md) | Accepted |
| ADR-006 | [Keep Deterministic Work in Tools and Services](ADR-006-keep-deterministic-work-in-tools-and-services.md) | Accepted |
| ADR-007 | [Use Structured Output Schemas](ADR-007-use-structured-output-schemas.md) | Accepted |
| ADR-008 | [Use Bounded Reflection for Resume Critique](ADR-008-use-bounded-reflection-for-resume-critique.md) | Accepted |
| ADR-009 | [Do Not Use Formal Multi-Agent Protocol for MVP](ADR-009-do-not-use-formal-multi-agent-protocol-for-mvp.md) | Accepted |
| ADR-010 | [Use ReAct Selectively in Research Agent Only](ADR-010-use-react-selectively-in-research-agent-only.md) | Accepted |
| ADR-011 | [Human-in-the-Loop as Backend Workflow Pauses](ADR-011-human-in-the-loop-as-backend-workflow-pauses.md) | Superseded by ADR-059 |
| ADR-012 | [Deep Review Only on Shortlisted Jobs](ADR-012-deep-review-only-on-shortlisted-jobs.md) | Accepted |
| ADR-013 | [Separate Resume Gaps from Career Gaps](ADR-013-separate-resume-gaps-from-career-gaps.md) | Accepted |
| ADR-014 | [Interview Coach Is Conditional](ADR-014-interview-coach-is-conditional.md) | Accepted |
| ADR-015 | [Tailoring Must Be Evidence-Bound](ADR-015-tailoring-must-be-evidence-bound.md) | Accepted |
| ADR-016 | [Add Fidelity Reviewer After Tailoring Agent](ADR-016-add-fidelity-reviewer-after-tailoring-agent.md) | Accepted |
| ADR-017 | [Ethical AI Use for Career Decision Support](ADR-017-ethical-ai-use-for-career-decision-support.md) | Accepted |
| ADR-018 | [Global Ethics Guardrails Must Be Included in Agent Prompts](ADR-018-global-ethics-guardrails-must-be-included-in-agent-prompts.md) | Accepted |
| ADR-019 | [Treat Scraped Job Descriptions as Untrusted Input](ADR-019-treat-scraped-job-descriptions-as-untrusted-input.md) | Accepted |
| ADR-020 | [Minimize PII Sent to LLMs](ADR-020-minimize-pii-sent-to-llms.md) | Accepted |
| ADR-021 | [Store Workflow Runs, Not Just Final Results](ADR-021-store-workflow-runs-not-just-final-results.md) | Accepted |
| ADR-022 | [Use JSON Columns for Evolving Agent Outputs](ADR-022-use-json-columns-for-evolving-agent-outputs.md) | Accepted |
| ADR-023 | [Make Observability First-Class](ADR-023-make-observability-first-class.md) | Accepted |
| ADR-024 | [Track Prompt Versions](ADR-024-track-prompt-versions.md) | Accepted |
| ADR-025 | [Add Security and Policy Layer Around Agents and Tools](ADR-025-add-security-and-policy-layer-around-agents-and-tools.md) | Accepted |
| ADR-026 | [Track Security Events](ADR-026-track-security-events.md) | Accepted |
| ADR-027 | [Add Cost, Token, and Latency Tracking](ADR-027-add-cost-token-and-latency-tracking.md) | Accepted |
| ADR-028 | [Start with Streamlit and SQLite MVP](ADR-028-start-with-streamlit-and-sqlite-mvp.md) | Accepted |
| ADR-029 | [Add FastAPI Only After Service Layer Stabilizes](ADR-029-add-fastapi-only-after-service-layer-stabilizes.md) | Accepted |
| ADR-030 | [Use skills.yaml for Application Skill Taxonomy](ADR-030-use-skillsyaml-for-application-skill-taxonomy.md) | Accepted |
| ADR-031 | [Separate Claude Code Support Files from App Code](ADR-031-separate-claude-code-support-files-from-app-code.md) | Accepted |
| ADR-032 | [Abstract LLM Providers](ADR-032-abstract-llm-providers.md) | Accepted |
| ADR-033 | [Status Manager Must Be Non-AI](ADR-033-status-manager-must-be-non-ai.md) | Accepted |
| ADR-034 | [Do Not Overbuild Before Proving Core Workflow](ADR-034-do-not-overbuild-before-proving-core-workflow.md) | Accepted |
| ADR-035 | [Enforce a Structured Workflow State Schema](ADR-035-enforce-a-structured-workflow-state-schema.md) | Accepted |
| ADR-036 | [Define Explicit Agent Input and Output Contracts](ADR-036-define-explicit-agent-input-and-output-contracts.md) | Accepted |
| ADR-037 | [Standard Failure and Retry Strategy](ADR-037-standard-failure-and-retry-strategy.md) | Accepted |
| ADR-038 | [Version Prompts, Agents, Schemas, and Workflows](ADR-038-version-prompts-agents-schemas-and-workflows.md) | Accepted |
| ADR-039 | [Define Sequential MVP Execution Model with Future Parallelism](ADR-039-define-sequential-mvp-execution-model-with-future-parallelism.md) | Accepted |
| ADR-040 | [Define Data Retention and Privacy Policy](ADR-040-define-data-retention-and-privacy-policy.md) | Accepted |
| ADR-041 | [All Agent Execution Must Be Bounded](ADR-041-all-agent-execution-must-be-bounded.md) | Accepted |
| ADR-042 | [Define Testing and Evaluation Strategy](ADR-042-define-testing-and-evaluation-strategy.md) | Accepted |
| ADR-043 | [Define Prompt Evaluation and Regression Strategy](ADR-043-define-prompt-evaluation-and-regression-strategy.md) | Accepted |
| ADR-044 | [Define v1 to v2 Migration Strategy](ADR-044-define-v1-to-v2-migration-strategy.md) | Accepted |
| ADR-045 | [Job Intake Supports Automated Discovery and Manual Input](ADR-045-Job-Intake-Supports-Automated-Discovery-and-Manual-Input.md) | Accepted |
| ADR-046 | [Hybrid Configuration Model (YAML + DB Overrides)](ADR-046-Hybrid_Configuration_Model_YAML_And_DB_Overrides.md) | Accepted |
| ADR-047 | [Use SqliteSaver for LangGraph Workflow Checkpoint Persistence](ADR-047-use-sqlitesaver-for-workflow-checkpoint-persistence.md) | Accepted |
| ADR-048 | [API Key Presence as Live/Mock Mode Gate](ADR-048-api-key-presence-as-live-mock-mode-gate.md) | Accepted |
| ADR-049 | [Use ThreadPoolExecutor for Concurrent Job Scoring](ADR-049-use-threadpoolexecutor-for-concurrent-job-scoring.md) | Accepted |
| ADR-050 | [Wrap v1 AdzunaScraper with a Concurrent Adapter](ADR-050-wrap-v1-adzuna-scraper-with-concurrent-adapter.md) | Accepted |
| ADR-051 | [Tiered Model Assignment — Haiku for Volume/Validation, Sonnet for Generative](ADR-051-tiered-model-assignment-haiku-for-volume-sonnet-for-generative.md) | Superseded by ADR-053 |
| ADR-052 | [Reduce MAX_JOBS_PER_RUN as the Primary Volume Cost Control Lever](ADR-052-reduce-max-jobs-per-run-as-cost-control.md) | Accepted |
| ADR-053 | [Pluggable Per-Agent Provider and Model Selection](ADR-053-pluggable-per-agent-provider-and-model-selection.md) | Accepted |
| ADR-054 | [Allow Deep Review for All Qualifying Jobs](ADR-054-allow-deep-review-for-all-qualifying-jobs.md) | Accepted |
| ADR-055 | [On-Demand Tailoring as an Out-of-Graph Operation](ADR-055-on-demand-tailoring-as-out-of-graph-operation.md) | Accepted |
| ADR-056 | [Tailoring Page-Budget Contract and Section-Grouped Suggestions](ADR-056-tailoring-page-budget-and-section-grouping.md) | Accepted |
| ADR-057 | [Restore Per-Job Exclusion (v1 Design) as a Pipeline Filter](ADR-057-restore-per-job-exclusion.md) | Accepted |
| ADR-058 | [Model Config to YAML with Per-Workflow Snapshot](ADR-058-model-config-to-yaml-with-per-workflow-snapshot.md) | Accepted |
| ADR-059 | [Retire In-Graph HITL; Add a Human Edit Decision](ADR-059-retire-in-graph-hitl-and-add-human-edit-decision.md) | Accepted |
| ADR-060 | [Human Triage Before Scoring (Widen Discovery, Score Only Selected)](ADR-060-human-triage-before-scoring.md) | Accepted |
| ADR-061 | [Configurable Funnel Width + On-Demand Deep Review and Interview Prep](ADR-061-configurable-funnel-width.md) | Accepted |
| ADR-062 | [Multi-User Profiles with a Single Swappable Identity Seam](ADR-062-multi-user-profiles.md) | Accepted |
| ADR-063 | [Retire the v1 Reference Code (Keep the Shared Scraper/Model Libraries)](ADR-063-retire-v1-reference-code.md) | Accepted |
| ADR-064 | [Per-Profile Search Criteria Drive Discovery; Configurable Relevance Filters](ADR-064-per-profile-search-criteria-drive-discovery.md) | Accepted |
| ADR-065 | [Experience-Targeted Discovery (Years-of-Experience Cap + Senior Exclusion)](ADR-065-experience-targeted-discovery.md) | Accepted |
| ADR-066 | [Standalone Resume Clinic (Job-Agnostic Review, Advice, and Overhaul)](ADR-066-standalone-resume-clinic.md) | Accepted |
| ADR-067 | [Preserve Full Resume Fidelity at Parse Time (GPA, Honors, Skill Groups)](ADR-067-preserve-resume-fidelity-at-parse-time.md) | Accepted |
| ADR-068 | [Chat-Revise Loop for the Resume Clinic](ADR-068-chat-revise-loop-for-the-resume-clinic.md) | Accepted |
| ADR-069 | [Redact Direct Identifiers at the LLM Context Seam](ADR-069-redact-direct-identifiers-at-the-llm-seam.md) | Accepted |
| ADR-070 | [Data Retention and State De-duplication (At-Rest Phase 1)](ADR-070-data-retention-and-state-deduplication.md) | Accepted |
| ADR-071 | [Per-Profile Active Scoring Tracks](ADR-071-per-profile-active-scoring-tracks.md) | Accepted |
| ADR-072 | [Resume Live Chat + Export in the Tailoring Flow](ADR-072-resume-live-chat-in-tailoring.md) | Accepted |
| ADR-073 | [Wire Security-Event Emit Sites and a Unified System Dashboard](ADR-073-wire-security-events-and-system-dashboard.md) | Accepted |
| ADR-074 | [Close the Remaining Observability Gaps](ADR-074-close-remaining-observability-gaps.md) | Accepted (fully closed: Gaps 1-5 + both minors) |
| ADR-075 | [Funnel UI Reads Through the API (Retire the Direct-SQLite Read Path)](ADR-075-funnel-ui-reads-through-api.md) | Accepted (fully implemented; db_reader deleted) |
| ADR-076 | [Observe Runtime Budget-Cap Trips](ADR-076-observe-runtime-budget-cap-trips.md) | Accepted (implemented) |
| ADR-077 | [Attribute Failed LLM-Call Spend + Cost-Logging Completeness Invariant](ADR-077-attribute-failed-llm-call-spend.md) | Accepted (implemented) |
| ADR-078 | [Observe the Structured-Output Repair Rate (Tier-1 Drift Proxy)](ADR-078-observe-structured-output-repair-rate.md) | Accepted (implemented) |
| ADR-079 | [Reasoning Relevance Pre-Filter Between Discovery and Scoring](ADR-079-relevance-prefilter-before-scoring.md) | Accepted (implemented) |
| ADR-080 | [Posting-Age Staleness Signal + Opt-In Max-Age Filter](ADR-080-posting-age-staleness.md) | Accepted (implemented) |
| ADR-081 | [ATS-Direct Job Sources (Greenhouse + Lever)](ADR-081-ats-direct-sources.md) | Accepted (prototype) |
| ADR-082 | [Idempotent Workflow Kickoff + In-Flight Execution Guard](ADR-082-idempotent-workflow-kickoff.md) | Accepted (implemented) |
| ADR-083 | [Cooperative Workflow Run Cancellation](ADR-083-cooperative-run-cancellation.md) | Accepted (implemented) |
| ADR-084 | [Liveness + Readiness Health Endpoints (/health, /readyz)](ADR-084-health-and-readiness-endpoints.md) | Accepted (implemented) |
| ADR-085 | [Cost cut: interview prep on-demand by default + verbose-agent output conciseness](ADR-085-cost-cut-interview-prep-on-demand-and-output-conciseness.md) | Accepted (implemented) |
| ADR-086 | [Scoring-specific resume projection](ADR-086-scoring-resume-projection.md) | Accepted (implemented) |
| ADR-087 | [Asynchronous Message Batches API scoring mode](ADR-087-async-batches-api-scoring-mode.md) | Proposed (deferred) |
| ADR-088 | [Reorganize the UI Around the Job-Seeker Journey](ADR-088-reorganize-ui-around-job-seeker-journey.md) | Accepted (implemented) |
| ADR-089 | [Matches as the Live Home Base](ADR-089-matches-as-live-home-base.md) | Accepted (implemented) |
| ADR-090 | [My Favorite Jobs + Job-Focused Resume Clinic](ADR-090-favorites-and-job-focused-clinic.md) | Accepted (implementing) |
| ADR-091 | [Resume Clinic Chat Reliability, Fidelity Feedback, and Export Fidelity](ADR-091-resume-clinic-chat-reliability-and-fidelity-feedback.md) | Accepted (implemented) |
| ADR-092 | [Clinic Chat Cost — Haiku Chat + On-Demand / At-Accept Fidelity](ADR-092-clinic-chat-cost-haiku-and-on-demand-fidelity.md) | Accepted (implemented) |
| ADR-093 | [Apply-Link Reliability + "Where to Focus" Triage Strip](ADR-093-apply-link-reliability-and-focus-triage.md) | Accepted (implemented) |
| ADR-094 | [Security-Clearance Exclusion (Folded into the Relevance Filter)](ADR-094-clearance-exclusion-in-relevance-filter.md) | Accepted (implemented) |
| ADR-095 | [Best-Effort Dead-Link Filter (Opt-in Discovery Step)](ADR-095-best-effort-dead-link-filter.md) | Accepted (implemented) |
| ADR-096 | [Durable Run Recovery Across Restarts (Graceful Drain + Checkpointed Auto-Resume)](ADR-096-durable-run-recovery-across-restarts.md) | Accepted (implemented) |
