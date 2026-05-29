"""ResumeChatAgent (ADR-068) - drives the Resume Clinic chat-revise loop.

One agent call per user-sent chat turn. The agent receives the user's
feedback, the current overhaul (the chat-edited state or the agent's
original overhaul if no edits have happened yet), and the last N turns of
conversation, and returns a short reply plus the FULL revised overhaul.

The runner (the chat endpoint in `app/api/routers/resume_clinic.py`) then
runs the Fidelity Reviewer on the new rewrites - same translation glue the
Resume Clinic runner uses - and persists the new overhaul into
`resume_clinic_reviews.edited_json`. The `decision` field is NOT changed
by chat turns; that is a separate explicit user action.

Context keys (set by the router):
  resume_id, resume_profile (dict; never raw_text), current_overhaul (dict),
  history (list of {role, message}), section (focus hint), message (user input)
"""

from app.providers.llm_client import LLMClient
from app.schemas.resume_chat import ResumeChatTurnResult
from app.services.observability_service import ObservabilityService

from .base_agent import BaseAgent


class ResumeChatAgent(BaseAgent):
    AGENT_NAME = "resume_chat"

    def __init__(self, provider: LLMClient, observability: ObservabilityService) -> None:
        super().__init__(provider, observability)

    def run(self, workflow_id: str, context: dict) -> ResumeChatTurnResult:
        result = self._run(workflow_id, context, ResumeChatTurnResult)
        return ResumeChatTurnResult(**result)

    def _output_summary(self, result: dict) -> str:
        rewrites = (result.get("overhaul") or {}).get("rewrites") or []
        changed = result.get("changed_sections") or []
        reply_preview = (result.get("reply") or "")[:80]
        return (f"rewrites={len(rewrites)} changed={list(changed)} "
                f"reply={reply_preview!r}")
