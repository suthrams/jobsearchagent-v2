# ADR-067: Preserve full resume fidelity at parse time

## Status

Accepted (2026-05-28). Schema-additive; no breaking changes.

## Context

The parsed `ResumeProfile` is the single source of structured truth for every
downstream agent (Resume Critic, Tailoring, Resume Reviewer for the Resume
Clinic, Scoring) and for the deterministic resume renderer (ADR-066 export
feature). If a field is not in the schema, the parser cannot store it, and
nothing downstream can recover it — `raw_text` is reserved for the Fidelity
Reviewer per the prompt rule, and other agents see only the parsed profile.

A Resume Clinic export of a test profile's resume on 2026-05-28 surfaced two concrete
content-loss bugs caused by this:

1. `EducationEntry` has no `gpa` or `honors` field. The source resume's
   `GPA: 3.9/4.0` and `Achievements: 3x Presidents List and Deans List
   Scholar.` were silently dropped during parsing.
2. `ResumeProfile.skills` is a flat `list[str]`. The source resume's five
   categorised groups (`Security & Monitoring`, `Networking & Protocols`,
   `Security Tools`, `Scripting & Operating Systems`, `Cloud & Collaboration`)
   were flattened to a single 32-element list with all category labels
   discarded. Exported resumes look "ATS-flat" because the structure is gone.

These are not renderer bugs and not clinic bugs. The renderer prints what is
in the parsed profile; the clinic agents reason over what is in the parsed
profile. The loss happened before either could see the data.

## Decision

Extend `ResumeProfile` (and `_ResumeEnhancement`, the LLM-output schema used
by the parser) to preserve resume content that the original PDF carried but
the v1 schema had no slot for. Additive only — existing fields stay; agents
that already read `skills` (the flat list) keep working unchanged.

### New fields

```python
class EducationEntry(BaseModel):
    institution: str
    degree: str
    year: int | None = None
    gpa: str | None = None                # NEW: as-written ("3.9/4.0", "First Class")
    honors: list[str] = []                # NEW: free-text awards / dean's list / etc.

class SkillGroup(BaseModel):              # NEW
    category: str                          # e.g. "Security & Monitoring"
    skills: list[str]

class ResumeProfile(BaseModel):
    ...
    skill_groups: list[SkillGroup] = []   # NEW: categorised view; populated when source had categories
    skills: list[str] = []                # KEPT (flat list, derived from skill_groups when populated)
```

The flat `skills` list is preserved because the Scoring Agent and the keyword
filters in `models/filters.py` consume it directly. When the LLM populates
`skill_groups`, the parser flattens it into `skills` to keep the two in sync;
when the LLM only returns `skills` (older / less structured resumes), nothing
populates `skill_groups` and the renderer falls back to the flat view.

### Parser prompt change

`app/prompts/agents/resume_parser.txt` is updated to ask for:

- `education[].gpa` and `education[].honors[]` whenever the source contains
  them — verbatim, no fabrication; null/empty when absent.
- `skill_groups[]` whenever the source skills section contains category
  headings; the LLM also fills the flat `skills` list as the union of all
  groups.

### Renderer change

`app/services/resume_text_renderer.py` reads the new fields:

- Education line includes `GPA: X` and honors when present.
- Skills section renders as a categorised list when `skill_groups` is
  populated; falls back to the flat list otherwise (existing behaviour for
  resumes parsed before this change).

## Options considered

- **Schema-additive (chosen).** Smallest change; preserves all existing
  consumers; new fields default to empty so old parsed_profile rows stay
  valid.
- **Replace `skills` with `skill_groups` entirely.** Rejected — would break
  the Scoring Agent and the keyword filters that read `skills` as a flat
  list. The two views coexist.
- **Stuff GPA / honors into `EducationEntry.degree` as one long string.**
  Rejected — couples display to the parsed field, makes downstream filtering
  (e.g. "candidates with GPA ≥ 3.5") impossible.

## Consequences

### Positive

- Resumes parsed after this change preserve GPA, academic honors, and skill
  categorisation through the clinic and the exports.
- The renderer's output reflects what the candidate actually wrote, not a
  flattened summary of it.
- Future schema additions for other commonly-dropped fields (relevant
  coursework, hackathons, awards section, languages) follow the same pattern.

### Tradeoffs

- **Stale parsed_profile rows.** Resumes parsed before this change retain
  the old shape. The renderer reads `gpa` / `honors` / `skill_groups` and
  gracefully omits them when absent, so old rows still render as before. To
  get the new fields populated for an existing resume, the user re-uploads
  it; the parser cache (keyed by raw_text hash) returns the cached profile,
  so a true re-parse requires deleting that resume row first or uploading a
  modified file. A migration script is out of scope for this ADR.
- **The parser LLM call grows slightly.** The output schema is two fields
  bigger; the prompt is ~20 lines longer. Sonnet-class model handles this
  fine; cost impact is negligible.

### Neutral

- ADR-066 (Resume Clinic) is untouched — the clinic operates on whatever
  parsed_profile it gets. With the new fields, the clinic's prompt also sees
  GPA / honors / skill groups, which improves the role/track alignment axis
  (alignment knows the candidate's actual GPA and honors signal).

## Non-goals

- Not adding a structured `projects` section. The parsed profile does not
  currently model projects; that is a separate decision (deferred to a
  future ADR if user feedback warrants it).
- Not adding a "Header / Contact details" model beyond what `name |
  headline | email | location` already covers.
- Not adding language / coursework / hackathon fields. Same rationale —
  future additions follow this ADR's additive pattern.

## References

- ADR-066 — Resume Clinic (the surface that exposed these gaps).
- ADR-064 — per-profile discovery (orthogonal but related: the early-career
  profile that hit this bug is the same one ADR-064 enabled).
- The renderer at `app/services/resume_text_renderer.py` (the consumer that
  surfaces these fields in exported files).
