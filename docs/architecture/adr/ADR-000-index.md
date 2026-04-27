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
| ADR-011 | [Human-in-the-Loop as Backend Workflow Pauses](ADR-011-human-in-the-loop-as-backend-workflow-pauses.md) | Accepted |
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
