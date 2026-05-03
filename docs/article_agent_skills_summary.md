# Six Agent Skills, One Working Day, A Measurably Better Codebase

The skills aren't magic. They're forcing functions. And they shaped a working session in ways I didn't expect.

---

I spent a single working session on **jobsearchagent-v2** — a multi-agent career intelligence app I use daily as part of my job hunt. By the end of the day I'd shipped 18 commits including a 4× performance improvement on the dominant code path, a UX overhaul organised around the user's lifecycle, an API surface aligned with project conventions, and a 5-PR refactor that would normally have been a 200-line monolith.

I used six agent-skills to get there. Here's what they actually did.

---

## What's an Agent Skill?

A skill is a small markdown file with frontmatter and a playbook. When you invoke `/skill-name path/to/file` in your AI tooling, the agent loads the playbook into context and applies the skill's framework — its specific lens — to the file you pointed at.

I used skills from [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills), a community pack of 21 covering the agentic AI development lifecycle: code review, frontend UI, performance, API design, security, testing, and so on.

Six of them shaped the day:

| Skill | What it does | Best applied to |
|-------|--------------|-----------------|
| `code-review-and-quality` | Five-axis review (correctness / readability / architecture / security / performance) with severity labels | Any file before merge |
| `frontend-ui-engineering` | UX-lens: loading states, empty states, accessibility, AI aesthetic check | User-facing UI files |
| `performance-optimization` | Measure-first discipline; identifies bottlenecks vs guesswork | Hot paths with evidence of slowness |
| `api-and-interface-design` | Hyrum's Law lens; URL conventions, error envelopes, type contracts | REST surfaces and abstractions |
| `context-engineering` | Rules-file design: what to load, when, in what hierarchy | CLAUDE.md / cursor rules / similar |

The pattern that worked: pick a skill, point it at a target, read the findings, **act only on Required**, defer the rest explicitly, run tests, commit, push, pick the next skill.

---

## Three Wins That Wouldn't Have Happened Without the Skills

### 1. The performance win came with discipline I'd otherwise have skipped

`/performance-optimization` applied to `app/workflows/nodes/deep_review.py` could have been a freeform "this loop should be parallel." Instead the skill forced me to **measure first** — even synthetically, even without a profiler.

I gathered three concrete inputs: deterministic LLM call counts, per-call latency from the project's own cost-breakdown data, and a strong analog (the same parallelisation had been done on the scoring node months earlier with measured 75s → 20s gain). That was enough evidence to act.

The fix: a 5-worker `ThreadPoolExecutor` mirroring the proven analog. Estimated **~4× wall-clock speedup at 10 deep-reviewed jobs**. New regression test locks in concurrency by running 5 jobs × 100ms agent calls and asserting the node completes in <300ms (sequential would be 500ms+).

The skill's discipline I valued most: it forced me to write the verification test. Without that, I'd have shipped a "trust me, it's faster" change.

### 2. The biggest API design issue was invisible at the file level

Earlier in the day I'd shipped four new tailoring endpoints. Each looked fine when I wrote it. When `/api-and-interface-design` reviewed the **boundary** (5 routers + the consumer + the SQLite read-bypass), the inconsistencies were obvious in 30 seconds:

```
POST /workflows/{wf}/jobs/{job}/tailor       <- verb in URL (not REST)
POST /tailorings/{id}/decision               <- singular, but the rest of the API uses /decisions plural
```

Both invisible to me when I'd shipped them, because I matched what felt right per-endpoint without seeing the surface as one contract.

The same skill also caught two error-shape inconsistencies (Pydantic 422s differed from hand-raised errors), one Hyrum's Law concern, and four backwards-compatible recommendations.

The lesson: **review the boundary, not the file**. Highest-leverage findings live where things connect.

### 3. The 5-PR refactor would otherwise have been a 200-line monolith

`/api-and-interface-design` applied to a 64-line ABC (`LLMClient`) caught a side-channel that had been quietly aging. Fixing it properly meant touching the abstraction, both providers, the BaseAgent class, every workflow node — ~200 lines across ~12 files.

I'd previously have done this as one PR. The skill's *staged migration* discipline split it into five:

1. Add the new typed dataclass + ABC method (additive, default impl)
2. BaseAgent uses the new method internally (no consumer change)
3. One call site migrated (`score_jobs`)
4. Other 5 call sites migrated
5. Remove the deprecated helper

**No PR exceeded ~100 changed lines.** Each was independently revertable. Each kept tests green at the boundary. If PR 3 had broken something, PRs 1-2 leave the codebase coherent. A monolithic PR doesn't have that property.

---

## The Surprising Pattern

I'd planned one skill per file. The pattern that emerged:

**Skills compose; they don't compete.** Same file under `/code-review-and-quality` and `/frontend-ui-engineering` produced complementary, non-overlapping findings. Code-review caught two HTTP calls firing on every Streamlit re-render. Frontend-UI caught that the page had ten sections in one scroll with no lifecycle organisation. Both right, both needed.

**Severity labels are load-bearing.** Each skill produces Required / Recommended / Optional / Nit / FYI. That gave me explicit permission to defer most findings without dropping them on the floor. The deferrals stay visible.

**The user staying in control was essential.** I never invoked a skill without typing the slash command (or asking and getting confirmation). The skills are powerful — that's exactly why they shouldn't be applied without intent.

---

## Concrete Outcomes

| What got better | Before | After |
|-----------------|--------|-------|
| Daily run wall-clock | ~100s for 10 jobs | ~25s (~4×) |
| LLM-usage abstraction | Racy positional tuple side-channel | Typed dataclass returned natively |
| REST URL conventions | Verb + plural/singular drift | Plural-noun consistency |
| Validation error shape | Two different shapes | One normalised envelope |
| Streamlit per-render HTTP | Two uncached calls per interaction | Both cached + invalidated |
| Workflow Detail UX | 10 unsorted sections | Lifecycle-organised |
| CLAUDE.md (project rules) | 4 stale facts, 2 missing sections | Restructured, fact-current |

Plus 6 new tests (one of which actively prevents the deep_review concurrency from regressing). 18 commits. Six skills.

---

## How to Try This

Drop the [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) pack in a `skills/` folder at your repo root. Most agent tools will discover it. Invoke with `/skill-name path/to/file`. Read the findings. Act on Required. Defer the rest with intent.

The pattern is the point: small skills, applied at intentional times, against specific files, with the human staying in control. One working day, six skills, a codebase measurably more maintainable and ready for extension.

---

*Code: [`suthrams/jobsearchagent-v2`](https://github.com/suthrams/jobsearchagent-v2). Skills: [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills). Tooling: Claude Code. Full session log: see `docs/blog_draft_skills_in_action.md` for the longer practitioner version.*
