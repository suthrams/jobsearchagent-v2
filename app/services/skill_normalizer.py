"""SkillNormalizer — maps raw skill strings to canonical names using data/skills.yaml.

Alias lookup is case-insensitive. Unknown skills pass through unchanged — no data loss.
The alias map is built once at __init__ time and cached for the service lifetime.
"""
from pathlib import Path

import yaml

_DEFAULT_YAML = Path("data/skills.yaml")


class SkillNormalizer:
    """Pure function object after initialisation — no DB or network calls."""

    def __init__(self, skills_yaml_path: str | Path = _DEFAULT_YAML) -> None:
        raw: dict = yaml.safe_load(Path(skills_yaml_path).read_text(encoding="utf-8"))
        # Flat alias → canonical map; canonical name itself is also a key
        self._lookup: dict[str, str] = {}
        for canonical, entry in raw.items():
            self._lookup[canonical.lower()] = canonical
            for alias in (entry or {}).get("aliases", []):
                self._lookup[alias.lower()] = canonical

    def normalize(self, skill: str) -> str:
        """Return canonical name for skill, or skill unchanged if not found."""
        return self._lookup.get(skill.strip().lower(), skill)

    def normalize_list(self, skills: list[str]) -> list[str]:
        """Normalize each skill. Preserves order. Does not deduplicate."""
        return [self.normalize(s) for s in skills]

    def normalize_and_deduplicate(self, skills: list[str]) -> list[str]:
        """Normalize and return sorted unique canonical names."""
        seen: set[str] = set()
        unique: list[str] = []
        for canonical in self.normalize_list(skills):
            if canonical not in seen:
                seen.add(canonical)
                unique.append(canonical)
        return sorted(unique)
