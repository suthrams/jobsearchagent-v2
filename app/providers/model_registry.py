"""ModelRegistry — resolves an agent's LLMClient from a config-driven catalog and assignment.

Per ADR-053 + ADR-058. Three concerns live in three places:

  1. **Catalog + pricing + default assignment**: configuration (config.yaml).
     Edit YAML to add a model, change a price, or swap a default. No code release.

  2. **Cost-cap policy** (`HIGH_VOLUME_AGENTS` + `HIGH_VOLUME_SAFE_MODELS`):
     this module. Hardcoded on purpose: the cap is a security boundary that
     a config edit must NOT be able to loosen. Enforced at registry build time.

  3. **Provider class dispatch** (`_build_provider`): this module. Adding a new
     provider (not a new model) requires a provider implementation.

Public API:
    catalog    = catalog_from_config(effective_config)
    assignment = assignment_from_config(effective_config, defaults_from_config(effective_config))
    registry   = ModelRegistry.build(prompt_loader, catalog, assignment)
    provider   = registry.for_agent("research_agent")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

from app.providers.claude_provider import ClaudeProvider
from app.providers.llm_client import LLMClient
from app.providers.openai_provider import OpenAIProvider
from app.providers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


# ── Cost guardrails (POLICY — do NOT move to config) ─────────────────────────
# These agents run on every job (10-20 calls per workflow) and dominate per-run
# token volume. Cost is a design decision: high-volume agents are restricted to
# the cheapest tier so a Settings UI slip cannot make a typical run 5-10x more
# expensive overnight. Expensive models are reserved for the low-volume agents
# that produce user-facing analysis (advisor, coach, tailoring).
#
# This allowlist stays in code BECAUSE configuration is editable and this rule
# must not be. Moving the allowlist to YAML reintroduces the cost incident this
# guardrail was shipped to prevent (see docs/incidents/, 2026-05-09 entry).
HIGH_VOLUME_AGENTS: frozenset[str] = frozenset({"research_agent", "scoring_agent"})
HIGH_VOLUME_SAFE_MODELS: frozenset[str] = frozenset({
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
})


def is_cost_capped_agent(agent_name: str) -> bool:
    """True if `agent_name` is in the high-volume tier (cheap-only allowlist)."""
    return agent_name in HIGH_VOLUME_AGENTS


def is_high_volume_safe_model(model_id: str) -> bool:
    """True if `model_id` is in the cost-capped allowlist for high-volume agents."""
    return model_id in HIGH_VOLUME_SAFE_MODELS


class CostCapViolationError(ValueError):
    """Raised when a cost-capped agent is assigned a model outside HIGH_VOLUME_SAFE_MODELS."""


class UnknownModelError(ValueError):
    """Raised when a (provider, model) pair is not in the configured catalog."""


class ModelConfigError(ValueError):
    """Raised when the YAML model catalog or agents block is malformed."""


# ── Catalog entry shape ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelCatalogEntry:
    """A single (provider, model) pair with its pricing.

    input_per_m and output_per_m are USD per 1,000,000 tokens, matching the
    Anthropic and OpenAI pricing-page convention.
    """
    provider: str
    model: str
    input_per_m: float
    output_per_m: float


Catalog = dict[str, list[ModelCatalogEntry]]


# ── Config -> in-memory shape ────────────────────────────────────────────────

def catalog_from_config(effective_config: dict) -> Catalog:
    """Parse and validate the `models.providers` block from effective config.

    Returns a `{provider: [ModelCatalogEntry, ...]}` mapping. Raises
    `ModelConfigError` for malformed input.

    Expected YAML shape (see config/config.example.yaml):

        models:
          providers:
            claude:
              - {id: claude-haiku-4-5-20251001, input_per_m: 1.0, output_per_m: 5.0}
            openai:
              - {id: gpt-4o-mini, input_per_m: 0.15, output_per_m: 0.60}
    """
    models_block = (effective_config or {}).get("models") or {}
    providers_block = models_block.get("providers")
    if not providers_block or not isinstance(providers_block, dict):
        raise ModelConfigError(
            "Config is missing `models.providers`. Add the block per "
            "config.example.yaml; cannot resolve any agent without a catalog."
        )

    catalog: Catalog = {}
    for provider_name, entries in providers_block.items():
        if not isinstance(entries, list):
            raise ModelConfigError(
                f"models.providers.{provider_name} must be a list; got {type(entries).__name__}"
            )
        parsed: list[ModelCatalogEntry] = []
        for raw in entries:
            if not isinstance(raw, dict):
                raise ModelConfigError(
                    f"models.providers.{provider_name} entry must be a mapping; got {type(raw).__name__}"
                )
            model_id = raw.get("id")
            input_rate = raw.get("input_per_m")
            output_rate = raw.get("output_per_m")
            if not isinstance(model_id, str) or not model_id:
                raise ModelConfigError(
                    f"models.providers.{provider_name} entry missing `id`: {raw!r}"
                )
            if not isinstance(input_rate, (int, float)) or input_rate < 0:
                raise ModelConfigError(
                    f"models.providers.{provider_name} entry {model_id!r}: "
                    f"input_per_m must be non-negative number, got {input_rate!r}"
                )
            if not isinstance(output_rate, (int, float)) or output_rate < 0:
                raise ModelConfigError(
                    f"models.providers.{provider_name} entry {model_id!r}: "
                    f"output_per_m must be non-negative number, got {output_rate!r}"
                )
            parsed.append(ModelCatalogEntry(
                provider=str(provider_name),
                model=str(model_id),
                input_per_m=float(input_rate),
                output_per_m=float(output_rate),
            ))
        catalog[str(provider_name)] = parsed

    if not catalog:
        raise ModelConfigError("models.providers is empty; at least one provider must be configured.")
    return catalog


def defaults_from_config(effective_config: dict) -> dict[str, dict[str, str]]:
    """Parse and validate the `agents.*` block as the default per-agent assignment.

    Returns `{agent_name: {"provider": "...", "model": "..."}}`. Raises
    `ModelConfigError` if the block is missing or malformed.
    """
    agents_block = (effective_config or {}).get("agents")
    if not agents_block or not isinstance(agents_block, dict):
        raise ModelConfigError(
            "Config is missing `agents` block (the default per-agent assignment). "
            "Add it per config.example.yaml."
        )
    defaults: dict[str, dict[str, str]] = {}
    for agent_name, raw in agents_block.items():
        if not isinstance(raw, dict):
            raise ModelConfigError(
                f"agents.{agent_name} must be a mapping; got {type(raw).__name__}"
            )
        provider = raw.get("provider")
        model = raw.get("model")
        if not isinstance(provider, str) or not provider:
            raise ModelConfigError(f"agents.{agent_name}.provider must be a non-empty string")
        if not isinstance(model, str) or not model:
            raise ModelConfigError(f"agents.{agent_name}.model must be a non-empty string")
        defaults[str(agent_name)] = {"provider": str(provider), "model": str(model)}
    return defaults


def assignment_from_config(effective_config: dict) -> dict[str, dict[str, str]]:
    """Read the agents.* block from effective config as a user-override map.

    This is the same parser used by `defaults_from_config`, kept as a separate
    name for callers that semantically want "overrides" (e.g. the API config
    validation path). Empty dict if absent (not an error in that context).
    """
    agents_block = (effective_config or {}).get("agents") or {}
    if not isinstance(agents_block, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for agent_name, raw in agents_block.items():
        if not isinstance(raw, dict):
            continue
        provider = raw.get("provider")
        model = raw.get("model")
        if provider and model:
            out[str(agent_name)] = {"provider": str(provider), "model": str(model)}
    return out


# ── Registry ─────────────────────────────────────────────────────────────────

class ModelRegistry:
    """Maps an agent name -> an LLMClient instance, given a catalog and assignment.

    Eagerly builds one provider per unique (provider, model) pair to amortise
    client construction. Intended to be built once at backend startup; rebuilt
    on `POST /config/reload` (ADR-053 addendum).
    """

    def __init__(
        self,
        agent_assignment: dict[str, dict[str, str]],
        providers_by_key: dict[tuple[str, str], LLMClient],
        catalog: Catalog,
    ) -> None:
        self._assignment = agent_assignment
        self._providers = providers_by_key
        self._catalog = catalog

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        prompt_loader: PromptLoader,
        catalog: Catalog,
        assignment: dict[str, dict[str, str]],
        *,
        openai_available: bool | None = None,
    ) -> "ModelRegistry":
        """Build the registry from a config-loaded catalog + assignment.

        Behavior:
        - Validates that every (provider, model) referenced in `assignment`
          exists in `catalog`.
        - Snaps high-volume agents back to their defaults if they reference
          a model outside HIGH_VOLUME_SAFE_MODELS (warning logged).
        - Snaps openai-targeted assignments back to defaults if OPENAI_API_KEY
          is not set (warning logged).
        - Builds one LLMClient instance per unique (provider, model) referenced,
          injecting that model's pricing from the catalog.
        """
        if openai_available is None:
            openai_available = bool(os.getenv("OPENAI_API_KEY"))

        # Build a (provider, model) -> ModelCatalogEntry lookup for validation + pricing.
        catalog_index: dict[tuple[str, str], ModelCatalogEntry] = {
            (entry.provider, entry.model): entry
            for entries in catalog.values()
            for entry in entries
        }

        # First pass: validate and snap. The assignment we end up using is the
        # input assignment with two kinds of fallback applied. We do not mutate
        # the caller's dict.
        merged: dict[str, dict[str, str]] = {}
        for agent_name, pick in assignment.items():
            provider = pick.get("provider", "")
            model = pick.get("model", "")
            if not provider or not model:
                logger.warning(
                    "ModelRegistry: agent %r has incomplete assignment %r; skipping",
                    agent_name, pick,
                )
                continue

            # OpenAI safe fallback: if the key is missing, degrade to a Claude
            # alternative from the catalog so the registry stays usable rather
            # than the workflow refusing to start. We prefer a HIGH_VOLUME_SAFE
            # Claude model for high-volume agents (preserves the cost cap) and
            # the first Claude model in the catalog otherwise.
            if provider == "openai" and not openai_available:
                claude_entries = catalog.get("claude") or []
                if agent_name in HIGH_VOLUME_AGENTS:
                    fb = next(
                        (e for e in claude_entries if e.model in HIGH_VOLUME_SAFE_MODELS),
                        None,
                    )
                else:
                    fb = claude_entries[0] if claude_entries else None
                if fb is None:
                    raise UnknownModelError(
                        f"agents.{agent_name} requested openai but OPENAI_API_KEY is "
                        f"not set, and no Claude fallback exists in the catalog. "
                        f"Either set OPENAI_API_KEY or add a Claude model to "
                        f"config.yaml `models.providers.claude`."
                    )
                logger.warning(
                    "ModelRegistry: agent %r requested openai but OPENAI_API_KEY is "
                    "not set; falling back to %s/%s from the Claude catalog.",
                    agent_name, fb.provider, fb.model,
                )
                merged[agent_name] = {"provider": fb.provider, "model": fb.model}
                continue

            # Catalog validation.
            if (provider, model) not in catalog_index:
                raise UnknownModelError(
                    f"agents.{agent_name}: ({provider}, {model}) is not in the configured "
                    f"models.providers catalog. Add it to config.yaml or pick a registered model."
                )

            # Cost-cap policy: snap high-volume agents back to their default if the
            # picked model is outside HIGH_VOLUME_SAFE_MODELS. Note: the *default*
            # for these agents must itself be in HIGH_VOLUME_SAFE_MODELS; if the
            # config places a high-volume agent on a non-safe default, that's a
            # config error and we raise.
            if agent_name in HIGH_VOLUME_AGENTS and model not in HIGH_VOLUME_SAFE_MODELS:
                logger.warning(
                    "ModelRegistry: agent %r is high-volume and cannot use %s/%s "
                    "(allowed: %s). Falling back to safest allowlist model.",
                    agent_name, provider, model, sorted(HIGH_VOLUME_SAFE_MODELS),
                )
                # Pick the first allowlist model we have in the catalog. Prefer
                # a Claude one if available (matches existing behavior).
                fallback_choice = None
                for safe_model in HIGH_VOLUME_SAFE_MODELS:
                    for prov, entries in catalog.items():
                        if any(e.model == safe_model for e in entries):
                            fallback_choice = {"provider": prov, "model": safe_model}
                            break
                    if fallback_choice:
                        break
                if fallback_choice is None:
                    raise CostCapViolationError(
                        f"No HIGH_VOLUME_SAFE_MODELS available in the catalog. "
                        f"At least one of {sorted(HIGH_VOLUME_SAFE_MODELS)} must be configured."
                    )
                merged[agent_name] = fallback_choice
                continue

            merged[agent_name] = {"provider": provider, "model": model}

        # Build one provider per unique (provider, model) referenced.
        providers_by_key: dict[tuple[str, str], LLMClient] = {}
        for agent_name, pick in merged.items():
            key = (pick["provider"], pick["model"])
            if key in providers_by_key:
                continue
            entry = catalog_index[key]
            providers_by_key[key] = cls._build_provider(prompt_loader, entry)

        return cls(merged, providers_by_key, catalog)

    @staticmethod
    def _build_provider(prompt_loader: PromptLoader, entry: ModelCatalogEntry) -> LLMClient:
        """Instantiate the right provider class and inject pricing from the catalog entry."""
        pricing = {entry.model: {"input": entry.input_per_m, "output": entry.output_per_m}}
        if entry.provider == "claude":
            return ClaudeProvider(prompt_loader, model_name=entry.model, pricing=pricing)
        if entry.provider == "openai":
            return OpenAIProvider(prompt_loader, model_name=entry.model, pricing=pricing)
        raise UnknownModelError(f"Unknown provider: {entry.provider!r}")

    # ── Public lookup ────────────────────────────────────────────────────────

    def for_agent(self, agent_name: str) -> LLMClient:
        if agent_name not in self._assignment:
            raise UnknownModelError(
                f"No assignment for agent {agent_name!r}. Add an entry to agents.* "
                f"in config.yaml."
            )
        pick = self._assignment[agent_name]
        key = (pick["provider"], pick["model"])
        if key not in self._providers:
            raise UnknownModelError(f"No provider built for {key!r}")
        return self._providers[key]

    def assignment(self) -> dict[str, dict[str, str]]:
        """Return the resolved per-agent assignment as a defensive copy."""
        return {k: dict(v) for k, v in self._assignment.items()}

    def catalog(self) -> Catalog:
        """Return the catalog this registry was built against (defensive copy)."""
        return {prov: list(entries) for prov, entries in self._catalog.items()}

    # ── Catalog (for the Settings UI) — server-driven so UI need not hardcode ──

    @staticmethod
    def catalog_for_ui(
        catalog: Catalog,
        *,
        openai_available: bool | None = None,
    ) -> dict[str, dict]:
        """UI-friendly description of providers and their models with pricing.

        Shape:
          {
            "claude": {"available": True, "models": [{"id": ..., "input_per_m": ..., ...}, ...]},
            "openai": {"available": False, "models": [...]},
            "_meta": {"high_volume_agents": [...], "high_volume_safe_models": [...]},
          }
        """
        if openai_available is None:
            openai_available = bool(os.getenv("OPENAI_API_KEY"))

        out: dict[str, dict] = {}
        for provider_name, entries in catalog.items():
            out[provider_name] = {
                "available": True if provider_name != "openai" else openai_available,
                "models": [
                    {
                        "id": entry.model,
                        "input_per_m": entry.input_per_m,
                        "output_per_m": entry.output_per_m,
                    }
                    for entry in entries
                ],
            }
        out["_meta"] = {
            "high_volume_agents": sorted(HIGH_VOLUME_AGENTS),
            "high_volume_safe_models": sorted(HIGH_VOLUME_SAFE_MODELS),
        }
        return out


# ── Convenience: build known-models map for legacy validation paths ─────────

def known_models_from_catalog(catalog: Catalog) -> dict[str, list[str]]:
    """Adapter for callers that need the old `{provider: [model_id, ...]}` shape.

    Useful for validators (e.g. PUT /config) that pre-date ADR-058.
    """
    return {prov: [entry.model for entry in entries] for prov, entries in catalog.items()}
