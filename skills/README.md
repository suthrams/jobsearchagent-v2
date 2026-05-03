# Project Skills — jobsearchagent v2

Curated agent-skills pack for this project. Each skill is a small set of instructions Claude Code (and other compatible agents) loads on demand. The full pack comes from [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) and is pinned via `skills-lock.json` at the project root.

This README maps the 21 skills to **where they apply in the jobsearchagent-v2 workflow**, so contributors (human and AI) know which skill to invoke at each stage.

## How to invoke

Type `/<skill-name>` in Claude Code, or ask Claude to use it (`"use the code-review-and-quality skill before merging"`). Claude Code will also auto-suggest a skill when the trigger conditions in its frontmatter match the work you're doing.

---

## When to use which skill

### Before starting a task

| Skill | When |
|-------|------|
| `/spec-driven-development` | Starting a new feature or significant change with no spec yet |
| `/idea-refine` | Working through a vague idea before committing to an approach |
| `/planning-and-task-breakdown` | A task feels too large to start; needs ordered subtasks |
| `/context-engineering` | Starting a new session or output quality is degrading |

### While implementing

| Skill | When |
|-------|------|
| `/test-driven-development` | Implementing any logic, fixing any bug — the Prove-It Pattern |
| `/incremental-implementation` | A change touches >1 file; avoid the "all-at-once" failure mode |
| `/source-driven-development` | Working with a framework where correctness matters; want authoritative citations |
| `/api-and-interface-design` | Designing REST endpoints, module boundaries, frontend↔backend contracts |
| `/frontend-ui-engineering` | Building or modifying Streamlit views — check before adding new sections |
| `/security-and-hardening` | Anything that handles user input, auth, storage, or external integrations |
| `/debugging-and-error-recovery` | Tests fail, builds break, behavior diverges from expectations |
| `/code-simplification` | Code works but feels harder to read/maintain than it should |

### Before merging or shipping

| Skill | When |
|-------|------|
| `/code-review-and-quality` | Before merging any change — multi-axis review |
| `/git-workflow-and-versioning` | Committing, branching, resolving conflicts, organising parallel work |
| `/ci-cd-and-automation` | Setting up or modifying build/deploy pipelines (none here yet) |
| `/shipping-and-launch` | Pre-launch checklist; staged rollout / rollback strategy |
| `/performance-optimization` | Performance regressions, Core Web Vitals, profiled bottlenecks |
| `/browser-testing-with-devtools` | Verifying a Streamlit feature works in a real browser |

### Maintenance

| Skill | When |
|-------|------|
| `/deprecation-and-migration` | Removing old systems / APIs; migrating users between implementations |
| `/documentation-and-adrs` | Architectural decisions, public API changes, recording context (this repo's `docs/architecture/adr/`) |

### Meta

| Skill | When |
|-------|------|
| `/using-agent-skills` | Discovering and invoking skills (start of a new session) |

---

## Project-specific guidance

### When working on this codebase, always

- Follow `CLAUDE.md` invariants (execution limits, evidence-bound generation, fidelity reviewer pairing).
- Update ADRs and impacted docs **before** implementing architectural changes (per the project feedback rule).
- Run `python -m pytest tests/` before committing — 448 tests pass on `main`.
- Use **HEREDOC** for commit messages with the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

### Strong skill+task pairings for this project

| Project task | Skills to invoke |
|--------------|------------------|
| Adding a new agent | `/spec-driven-development` → `/test-driven-development` → `/code-review-and-quality` |
| Tweaking a prompt | `/source-driven-development` (cite Anthropic docs) → `/test-driven-development` (regression on schema) |
| Adding a workflow node | `/planning-and-task-breakdown` → `/incremental-implementation` → `/code-review-and-quality` |
| New API endpoint | `/api-and-interface-design` → `/test-driven-development` → `/security-and-hardening` |
| Streamlit UI change | `/frontend-ui-engineering` → `/browser-testing-with-devtools` |
| Architectural change | `/documentation-and-adrs` first (write the ADR), then implement |
| Cost / latency regression | `/performance-optimization` → `/debugging-and-error-recovery` |
| Schema or DB migration | `/deprecation-and-migration` → review backward compatibility |

---

## Other skill sources (not in this folder)

Claude Code also loads skills from two other locations — these are **personal**, not part of this project:

- **`~/.claude/skills/`** — your user-level skills (currently `find-skills` only).
- **`~/.claude/plugins/marketplaces/.../skills/`** — skills from installed plugins (e.g. `init`, `review`, `security-review`, `update-config`, `keybindings-help`, `simplify`, `fewer-permission-prompts`, `loop`, `schedule`, `claude-api`).

Project skills (this folder) take precedence over user-level skills with the same name.

---

## Updating the pack

`skills-lock.json` records the source repo, the skill path, and a SHA-256 hash of every SKILL.md. To pull updates from `addyosmani/agent-skills`, re-run whichever skill manager produced the lockfile, then commit both `skills/` and `skills-lock.json` together.

To **add** a project-specific skill, drop a `skills/<name>/SKILL.md` with frontmatter (`name`, `description`) — the description is what triggers Claude to surface the skill, so be specific about when to use it.
