"""Role-data providers — pluggable seam for grounding the Resume Clinic's
target-role alignment in occupation taxonomies (ADR-066 Decision G).

The v1 default is NullRoleDataProvider (LLM-only). Real providers (ESCO, O*NET)
are a fast-follow and slot in here without changing the runner or agent.
"""

from .base import NullRoleDataProvider, RoleData, RoleDataProvider

__all__ = ["NullRoleDataProvider", "RoleData", "RoleDataProvider"]
