"""Model-pin invariant: every agent's resolved (provider, model) must match
the last validated entry in tests/model_pins.json.

When this test fails it means an agent's model assignment changed (in
config.yaml, in the user_config DB, or by editing the catalog) without a
matching pin update. The failure message says exactly what to do.

This is the cross-layer invariant the cost-as-dashboard incident taught us
to add for every load-bearing seam: ADR-058 already records which model ran
which agent per workflow (audit), but nothing failed the build when an
assignment changed without explicit re-validation (gate). This is the gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.providers.model_registry import (
    ModelRegistry,
    catalog_from_config,
    defaults_from_config,
)
from app.providers.prompt_loader import PromptLoader
from app.services.config_service import ConfigService

PINS_PATH = Path(__file__).parent.parent / "model_pins.json"

# user_id "0" is the system default profile (ADR-062). Pinning against this
# profile catches changes to config.yaml AND to user_config rows for "0".
# Per-profile pins are out of scope: a profile that is not "0" is by
# definition expressing a user preference, not a system-wide assignment.
_PIN_USER_ID = "0"


def _load_pins() -> dict[str, dict[str, str]]:
    raw = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    return raw["pins"]


def _resolved_assignment() -> dict[str, dict[str, str]]:
    """Build the registry the same way app startup does, for the pin profile."""
    effective = ConfigService().get_effective_config(user_id=_PIN_USER_ID)
    catalog = catalog_from_config(effective)
    defaults = defaults_from_config(effective)
    # openai_available=True so a missing OPENAI_API_KEY does not mask real
    # drift behind the registry's openai-to-claude fallback. The test runs
    # in a synthetic resolution mode: we want to see what the assignment
    # ACTUALLY is, not what it would degrade to without a key.
    registry = ModelRegistry.build(
        prompt_loader=PromptLoader(),
        catalog=catalog,
        assignment=defaults,
        openai_available=True,
    )
    return registry.assignment()


def test_every_agent_assignment_matches_its_pin() -> None:
    pins = _load_pins()
    resolved = _resolved_assignment()

    drifted: list[str] = []

    for agent, pin in pins.items():
        actual = resolved.get(agent)
        if actual is None:
            drifted.append(
                f"  {agent}: PIN={pin['provider']}/{pin['model']}  ACTUAL=<no assignment>"
            )
            continue
        if (actual["provider"], actual["model"]) != (pin["provider"], pin["model"]):
            drifted.append(
                f"  {agent}: "
                f"PIN={pin['provider']}/{pin['model']} (validated {pin['validated_on']})  "
                f"ACTUAL={actual['provider']}/{actual['model']}"
            )

    # Agents present in the resolved assignment but missing from pins -- a new
    # registry consumer shipped without an explicit pin entry.
    for agent in sorted(resolved.keys() - pins.keys()):
        drifted.append(f"  {agent}: PIN=<not pinned>  ACTUAL={resolved[agent]['provider']}/{resolved[agent]['model']}")

    if drifted:
        pytest.fail(
            "Model assignment has drifted from tests/model_pins.json:\n"
            + "\n".join(drifted)
            + "\n\n"
            + "If this drift is intentional:\n"
            + "  1. Run the live suite for each drifted agent:\n"
            + "       pytest -m integration tests/\n"
            + "  2. Inspect the structured outputs for SEMANTIC drift\n"
            + "     (scores in expected ranges, decisions sensible,\n"
            + "     evidence quality intact) -- not just that the call succeeded.\n"
            + "  3. Update tests/model_pins.json with the new\n"
            + "     {provider, model, validated_on}.\n"
            + "  4. Commit the pin update separately from the config change\n"
            + "     so the re-validation is auditable.\n"
        )
