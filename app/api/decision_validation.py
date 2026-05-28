"""Shared decision request validator (ADR-059).

The same approve | revise | reject | edit shape is used by:
  - tailoring router  (POST /tailorings/{id}/decisions)
  - resume-clinic router (POST /resume-clinic/{id}/decisions)

Both endpoints persist the user's choice into a per-domain repository column
and treat `edit` as the human-as-final-author override: the human draft lives
in `edited_json` and is NOT re-reviewed by Fidelity (the reviewer polices the
agent, not the accountable human). `approval` is preserved as the wire field
name because the tailoring router already shipped with it; the clinic router
uses the same shape so consumers can reuse the same submit pattern.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DecisionRequest(BaseModel):
    approval: Literal["approve", "revise", "reject", "edit"] = Field(
        description=(
            "approve = use this draft as-is; revise = needs changes; "
            "reject = discard; edit = accept with the user's own wording"
        )
    )
    # Present only when approval == "edit": the human-authored final draft.
    # A human edit is trusted as final and is NOT re-run through the Fidelity
    # Reviewer (ADR-059) — the reviewer polices the agent, not the accountable
    # human. The agent's original draft is retained separately for the audit trail.
    edited: dict | None = Field(default=None)

    @model_validator(mode="after")
    def _require_edited_on_edit(self) -> "DecisionRequest":
        if self.approval == "edit" and not self.edited:
            raise ValueError(
                "`edited` (the human-authored draft) is required when approval is 'edit'"
            )
        return self
