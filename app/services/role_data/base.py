"""RoleData provider seam (ADR-066 Decision G).

The Resume Clinic's role/track alignment defaults to the LLM's own knowledge of
"what role X expects." Model knowledge of occupation taxonomies can be stale, so
the runner consults an optional `RoleDataProvider` before invoking the reviewer.
When a provider returns occupation data, the reviewer prompt receives it as
ground truth for the alignment axis (required_skills, tools, certifications).

v1 ships only `NullRoleDataProvider` (returns None for any input); ESCO and
O*NET providers are a fast-follow per the ADR. The shape is fixed in v1 so the
reviewer prompt and schema can be designed around a known interface; future
providers populate the same shape.

Graceful fallback is a hard rule: if no provider is configured, the lookup
misses, or any provider call fails/times out, the runner proceeds on LLM
knowledge alone and the reviewer's `alignment.confidence` should reflect that.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class RoleData(BaseModel):
    """Occupation-level grounding for the alignment axis.

    Populated by real providers (ESCO, O*NET). The runner injects this into the
    reviewer's context under the `role_data` key when present; the prompt treats
    these as authoritative for "what the role expects."
    """
    occupation_title: str
    required_skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    source: str = "unknown"   # provider identity for the audit trail (e.g. "esco", "onet")


class RoleDataProvider(Protocol):
    """Protocol the runner depends on. Implementations are plug-in.

    `lookup` MUST NOT raise. Network/timeout/parse errors are absorbed and the
    return value is None — graceful fallback is the contract, not best-effort.
    """

    def lookup(self, role: str | None, track: str | None) -> RoleData | None: ...


class NullRoleDataProvider:
    """v1 default — returns None for every input.

    Keeps the seam wired without adding any runtime dependency. Real providers
    (ESCO, O*NET) replace this in the dependency graph when their credentials
    are present.
    """

    def lookup(self, role: str | None, track: str | None) -> RoleData | None:
        return None
