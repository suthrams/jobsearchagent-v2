"""PromptLoader — loads, caches, and assembles agent prompts.

Prompt file layout under app/prompts/:
  shared/guardrails.txt         injected into every agent's system message
  agents/{agent_name}.txt       role + task + constraints + output schema

Assembly produces two LangChain messages per call:
  SystemMessage  —  guardrails + agent prompt  (static; defines Claude's role)
  HumanMessage   —  JSON-serialized context    (dynamic; the actual input data)

Version tags in agent files:
  # version: 1
  The first line is parsed to extract the version integer.
  PromptLoader strips it from the prompt content before sending to Claude.
"""

import json
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# app/providers/ → app/ → app/prompts/
_DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_VERSION_PREFIX = "# version:"


class PromptLoader:
    """Loads prompt files on first use, caches them, and assembles LangChain messages.

    Thread-safe for read-only access after warm-up.
    Files are never reloaded unless a new PromptLoader instance is created.
    """

    def __init__(self, prompts_dir: Path = _DEFAULT_PROMPTS_DIR) -> None:
        self._prompts_dir = prompts_dir
        self._file_cache: dict[str, str] = {}    # absolute path → raw file content
        self._versions: dict[str, int] = {}      # agent_name → parsed version int

    # ── Public API ────────────────────────────────────────────────────────────

    def assemble(self, agent_name: str, context: dict) -> list:
        """Build [SystemMessage, HumanMessage] for the given agent and context.

        Two-tier prompt caching (Anthropic supports up to 4 cache_control blocks):

          Block 1 — agent prompt (always cached):
            guardrails + agent prompt. Static per (agent, prompt-version) for
            the lifetime of the process. Highest cache hit rate.

          Block 2 — session-static context (cached if context["_cached"] is set):
            Pulled out of context["_cached"]. Intended for fields that are
            constant across all calls within a user session — typically
            resume_profile (1-2K tokens). Cached for 5 minutes per content
            identity. The first call in a window pays full price; subsequent
            calls within that window pay 10% on this block too.

          Block 3 — per-call context (never cached):
            The remaining context dict (everything except "_cached"). Varies
            per call (job description, review, advice).

        Anthropic's cache minimum is 1024 tokens per block. Block 2 is opt-in
        precisely because resume_profile is right at this threshold and the
        cache lookup has its own overhead — only worth the round-trip when the
        content is genuinely substantial.

        Args:
            agent_name:  e.g. "scoring_agent" — must match a file in agents/.
            context:     Input variables serialized into the human message.
                         A nested key "_cached" (dict) is moved into a separate
                         cached system block; everything else lands in the
                         human message.

        Returns:
            List of messages: [SystemMessage(prompt), maybe SystemMessage(cached_ctx), HumanMessage(per_call_ctx)].
        """
        # Pull cached context out of the regular context dict so it doesn't
        # get serialized into the human message twice.
        cached_ctx = None
        per_call_ctx = context
        if isinstance(context, dict) and "_cached" in context:
            cached_ctx = context.get("_cached") or None
            per_call_ctx = {k: v for k, v in context.items() if k != "_cached"}

        messages: list = [
            SystemMessage(content=[{
                "type": "text",
                "text": self._build_system_content(agent_name),
                "cache_control": {"type": "ephemeral"},
            }]),
        ]
        if cached_ctx:
            # Second cached block. Anthropic enforces the 1024-token minimum
            # internally; below that, the block is sent without caching.
            messages.append(SystemMessage(content=[{
                "type": "text",
                "text": (
                    "STATIC CONTEXT (constant across calls in this session — "
                    "do not re-emit any of these fields in your output, just "
                    "use them as background):\n"
                    + json.dumps(cached_ctx, indent=2, default=str)
                ),
                "cache_control": {"type": "ephemeral"},
            }]))
        messages.append(HumanMessage(content=self._build_human_content(per_call_ctx)))
        return messages

    def get_version(self, agent_name: str) -> str:
        """Return the version string for this agent, e.g. "scoring_agent:v1".

        Triggers a file load if the agent prompt has not been accessed yet.
        """
        if agent_name not in self._versions:
            self._load_agent_prompt(agent_name)
        return f"{agent_name}:v{self._versions[agent_name]}"

    # ── Message construction ──────────────────────────────────────────────────

    def _build_system_content(self, agent_name: str) -> str:
        """Prepend guardrails to the agent-specific prompt."""
        guardrails = self._load_guardrails()
        agent_prompt = self._load_agent_prompt(agent_name)
        return f"{guardrails}\n\n---\n\n{agent_prompt}"

    def _build_human_content(self, context: dict) -> str:
        """Wrap the context dict in a framing instruction and serialize to JSON."""
        return (
            "Process the following input context and return a JSON response "
            "that matches the schema described in the system prompt.\n\n"
            "INPUT:\n"
            + json.dumps(context, indent=2, default=str)
        )

    # ── File loading and caching ──────────────────────────────────────────────

    def _load_guardrails(self) -> str:
        """Load shared/guardrails.txt, cached after first read."""
        path = self._prompts_dir / "shared" / "guardrails.txt"
        return self._read_file(path)

    def _load_agent_prompt(self, agent_name: str) -> str:
        """Load agents/{agent_name}.txt, strip version tag, cache content."""
        path = self._prompts_dir / "agents" / f"{agent_name}.txt"
        raw = self._read_file(path)
        self._versions[agent_name] = self._extract_version(raw)
        return self._strip_version_line(raw)

    def _read_file(self, path: Path) -> str:
        """Return cached content or read from disk on first access."""
        key = str(path)
        if key not in self._file_cache:
            if not path.exists():
                raise FileNotFoundError(
                    f"Prompt file not found: {path}\n"
                    f"Expected directory: {self._prompts_dir}"
                )
            self._file_cache[key] = path.read_text(encoding="utf-8")
            logger.debug("Prompt loaded: %s", path.name)
        return self._file_cache[key]

    # ── Version parsing ───────────────────────────────────────────────────────

    def _extract_version(self, content: str) -> int:
        """Parse '# version: 2' from the first non-empty line; default to 1."""
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        if not first_line.startswith(_VERSION_PREFIX):
            return 1
        try:
            return int(first_line.split(":", 1)[1].strip())
        except (ValueError, IndexError):
            return 1

    def _strip_version_line(self, content: str) -> str:
        """Remove the version comment so it doesn't appear in Claude's prompt."""
        lines = content.splitlines()
        if lines and lines[0].startswith(_VERSION_PREFIX):
            lines = lines[1:]
        return "\n".join(lines).strip()
