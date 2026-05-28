"""ResumeReviewerAgent — the standalone Resume Clinic's reviewer (ADR-066).

Job-agnostic by construction. Produces:
  - Quality scorecard (always; role-agnostic)
  - Role/track alignment (when target_role or target_track is given; otherwise null)
  - Reorganization plan
  - Evidence-bound rewrites in the tailoring claim-type shape

The Fidelity Reviewer runs on `rewrites` after this agent in
app/services/resume_clinic_runner.py — never optional. A human `edit` decision
is owner-authored and is NOT re-reviewed (ADR-059).

Context keys (set by run_clinic):
  resume_id, resume_profile (dict; never raw_text), target_role (str|None),
  target_track (str|None), seniority_aware (bool), role_data (dict|None)
"""

from app.providers.llm_client import LLMClient
from app.schemas.resume_clinic import ResumeClinicReview
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ResumeReviewerAgent(BaseAgent):
    AGENT_NAME = "resume_reviewer"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> ResumeClinicReview:
        result = self._run(workflow_id, context, ResumeClinicReview)
        return ResumeClinicReview(**result)

    def _output_summary(self, result: dict) -> str:
        q = (result.get("quality") or {}).get("dimensions") or []
        rewrites = result.get("rewrites") or []
        align = "yes" if result.get("alignment") else "no"
        return f"dims={len(q)} rewrites={len(rewrites)} alignment={align}"
