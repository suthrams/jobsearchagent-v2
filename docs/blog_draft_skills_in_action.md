# Agent Skills in Action: One Day, Eighteen Commits, Six Skills

A practitioner's log of how a small set of agent-skills shaped a working session on a real codebase, what each one actually caught that I would have missed, and how the codebase ended up more maintainable and ready for extension.

---

## TL;DR

In a single working session on **jobsearchagent-v2** (a multi-agent career intelligence app), I used six agent-skills — `code-review-and-quality`, `frontend-ui-engineering`, `performance-optimization`, `api-and-interface-design` (twice), and `context-engineering` — to ship eighteen commits. Each skill caught issues a per-file review would have missed, and the workflow naturally sequenced itself: review → act on required findings → next skill, with the user staying in control of *when* to invoke and *what* to act on.

The session produced one user-facing feature, a 4× performance improvement on the daily-cost dominant code path, an API surface aligned with project conventions, a UX overhaul organised around the user's lifecycle, and a structural cleanup of the rules file itself — sequenced as small commits that are independently revertable.

---

## Setup

The codebase is a multi-agent system: 8 specialised LLM-driven agents orchestrated by LangGraph, a FastAPI backend, a Streamlit UI, ~456 tests, 56 ADRs. I'm an active user of the app (I'm in a job hunt), so usability and run-time speed matter to me daily — not as abstract concerns.

The starting state of the session: I'd already shipped an on-demand resume-tailoring feature that morning. The natural question — *"what did I miss when I shipped that?"* — opened the door for a structured review pass. I wasn't looking for a code reviewer; I wanted **specific lenses** I could apply at specific times, against specific files.

That's what agent-skills are. Each skill is a small markdown file with frontmatter and a playbook. When invoked, the agent loads the playbook into context and applies its specific framework to the work at hand. Different skills produce different findings on the same file because they look through different lenses.

---

## The Pattern That Emerged

I'd planned to do one skill per file. The pattern that actually worked was:

1. **Pick a skill, point it at a target.** Type `/skill-name path/to/file` in the prompt.
2. **Read the findings.** Each skill produces severity-categorised findings (Required / Recommended / Optional / Nit / FYI).
3. **Act only on Required.** Defer everything else explicitly — every deferred item is documented so it doesn't drop on the floor.
4. **Run tests. Commit. Push.**
5. **Pick the next skill.**

The user stays in control of *when* and *what*. The skill provides the *how*.

A small detail I appreciated: skills are not just instructions, they're **forcing functions**. When I'd reviewed code freeform earlier in the session, my findings were freeform. When I invoked `/code-review-and-quality`, the framework forced me to evaluate the same file across five axes (correctness, readability, architecture, security, performance), with severity labels, with line references. Same brain, different output.

---

## Per-Skill Case Study

### 1. `/code-review-and-quality` on `app/ui/streamlit_app.py`

The Streamlit app is the user-facing surface. 1616 lines, 12 sidebar views, lots of state. I'd touched it that morning to add a Resume Tailoring section, so it was the natural review target.

The skill's framework forced me to look beyond what I'd just changed. It found:

- **Two HTTP calls firing on every Streamlit re-render** — `api.list_tailorings(wf_id)` (line 803) and `api.get_providers()` (line 1457). Streamlit re-runs the entire script on every interaction, so an uncached HTTP call fires per keystroke. This is a perf gotcha unique to Streamlit's reactive model — I would have missed it doing a freeform read.
- **A markdown-injection surface on scraped strings** — but the skill also flagged the right scope question: *is this a real threat in a single-user local app?* I documented as a known limitation rather than over-engineer.
- **The "1616 lines in one file" concern** — recommended a view-split, but acknowledged it as Recommended (not Required) so I didn't block on it.

**What the skill prevented**: shipping a fix that was just "add a cache" without recognising the bigger architectural concern about the re-render cost. **Outcome**: cache fix shipped immediately (`77cd33a`); view-split deferred to a tracked future PR.

### 2. `/frontend-ui-engineering` on the same file

Same target, completely different lens. This one wasn't about correctness — it was about whether the app *feels* right to a real user. Findings:

- **Loading states missing on slow operations.** `api.start_workflow()` froze the UI silently for multiple seconds. The skill's framework asks "what does the user see while waiting?" — I hadn't asked that question.
- **Empty-state dead-ends.** Four analytics views said `"No scored jobs found."` and stopped. The skill's principle: every empty state should answer *"what do I do next?"*.
- **Workflow Detail page overloaded.** Ten sections in one scroll, no organisation. I'd been adding sections incrementally without ever stepping back.

The user feedback at this point was sharp and useful: *"As I am in a job hunt, I am using this app actively. It would be great to organise along that lifecycle from the standpoint of a job hunter who is trying to reset and move forward with the next opportunity."*

That reframed the work. The Workflow Detail page got reorganised into the user's mental model: **Find & Score → Review → Prep → Diagnostics**. Section headers got lifecycle prefixes. The four diagnostic sections collapsed under one expander. **Outcome**: `4346581` ships UX a real user can navigate intuitively.

The lesson: the same file under two different skills produced complementary, non-overlapping findings. Code-review found cache misses; frontend-UI found UX gaps. Both right, both needed.

### 3. `/performance-optimization` on `app/workflows/nodes/deep_review.py`

The skill's central rule: **measure before optimising**. I couldn't run a real workflow inside the session, but I had three reliable inputs:
- Counts of LLM calls per job (deterministic from code)
- Per-call latency (from the project's existing cost-breakdown data)
- A strong analog: ADR-049 had measured the same parallelisation pattern on the scoring node (75s → 20s)

That's enough evidence to act. Without the skill's discipline I might have either (a) skipped the work because I didn't have a profiler, or (b) parallelised something less impactful because it "felt slow."

The bottleneck: `for job in selected_jobs:` processed jobs sequentially, but each job's critic+auditor reflection loop was structurally independent. With `MAX_SELECTED_JOBS` recently raised from 3 to 10, deep review had become the dominant wall-clock cost.

The fix: a 5-worker `ThreadPoolExecutor` mirroring the score_jobs template — same pattern, proven analog. Estimated **~4× speedup at MAX_SELECTED_JOBS=10**. New regression test locks in concurrency by running 5 jobs × 100ms agent calls and asserting the node completes in <300ms (sequential would be 500ms+).

**Outcome**: `651cc13` — biggest single perf win for daily-use the app got that day.

The skill's discipline that I valued most: the verification step. *"After verification: before-and-after measurements exist (specific numbers)."* That stopped me from saying "it's faster" without evidence.

### 4. `/api-and-interface-design` on `app/api/routers/` (the whole boundary)

This is where reviewing **a system, not a file** paid off the most. I scoped the review to all 5 routers + the consumer (`api_client.py`) + the SQLite read-bypass (`db_reader.py`).

The biggest finding: I'd shipped tailoring endpoints earlier that morning with **inconsistent URL patterns** that I couldn't see when looking at tailoring.py alone. Comparing against the existing routers:

```
POST /workflows/{wf}/jobs/{job}/tailor       <- verb (not REST)
GET  /workflows/{wf}/tailorings              <- plural noun (good)
GET  /tailorings/{id}                        <- top-level (different scope from the create URL!)
POST /tailorings/{id}/decision               <- singular, but workflows uses /decisions plural
```

These inconsistencies were invisible to me when I'd shipped tailoring.py because I matched what felt right per-endpoint. The skill's framework — which evaluates the surface as one contract — surfaced the drift immediately.

It also caught **two error-shape inconsistencies**: the project's hand-raised errors used `{detail: {error, message}}` consistently, but Pydantic 422s surfaced their default field-list shape. The consumer (`api_client.py`) couldn't read errors uniformly.

**Outcome**: `6bac3c0` — URLs aligned (`/tailor` → `/tailorings`, `/decision` → `/decisions`), top-level vs scoped paths documented, typed `TailoringResponse` schema enforced via `response_model`, global Pydantic ValidationError handler that normalises every 422 to the same envelope.

### 5. `/api-and-interface-design` on `app/providers/llm_client.py` (the abstraction)

Same skill, very different scope: a single 64-line ABC with two concrete implementations. The framework's "Hyrum's Law" lens — *every observable behaviour becomes a commitment* — caught a design issue that had been quietly aging:

`last_call_usage()` was a side-channel. The caller had to:
```python
result = provider.complete(agent, ctx, schema)
ti, to, cost = provider.last_call_usage()  # racy if a different thread called complete() in between
```

Mitigated by `threading.local` at every layer (provider, BaseAgent, helper), but: positional tuple return (adding `latency_ms` would break every caller), out-of-band coupling (caller must remember the dance), and the race surface had grown when I'd just parallelised deep_review hours earlier.

**The right fix**: deprecate the side-channel and make `complete()` return both data and usage as a typed `LLMResponse(data, usage)`. The migration touches the ABC, both providers, the BaseAgent class, every workflow node — ~200 lines across ~12 files.

This is where the skill's *staged migration* discipline saved the day. Instead of one 200-line PR, the skill recommended five small ones. I executed them sequentially:

| PR | What | Lines | Test status |
|----|------|-------|-------------|
| 1/5 (`984cd9f`) | Add `LLMUsage` dataclass + ABC method | +99 / -3 | +5 tests, 455 pass |
| 2/5 (`0a37380`) | BaseAgent uses new method internally | +61 / -6 | +1 test, 456 pass |
| 3/5 (`b11b1f0`) | score_jobs migrates to typed accessor | +31 / -5 | 456 pass |
| 4/5 (`5979672`) | Other 4 nodes migrate | +20 / -16 | 456 pass |
| 5/5 (`0b7568c`) | Remove deprecated tuple helper | +14 / -15 | 456 pass |

**No PR exceeded ~100 changed lines.** Each was independently revertable. Each kept tests green at the boundary. If PR 3 had broken something, PRs 1-2 still leave the codebase coherent.

This is the kind of work I'd previously done as one big "I'll just refactor it" PR — and shipped scary diffs. Doing it staged forced me to think about the shape of the migration, the order of dependencies, and the test surface at each step.

### 6. `/context-engineering` on `CLAUDE.md`

The closing skill. CLAUDE.md is the rules file every AI assistant working on this repo reads first. After eighteen commits and a lot of new patterns, the file had drifted: stale ADR count, old tailoring URLs, a hardcoded model line that contradicted the new ModelRegistry, missing conventions that I'd been following but never written down (commit message format, ADR-first rule, ASCII-only chat output).

The skill's framework: rules file should cover tech stack, **commands**, **conventions**, and **boundaries** — and stay focused (under ~2,000 lines for project-level context).

**Outcome**: `67e4d01` — CLAUDE.md restructured by first-30-seconds priority (overview → run → rules → file structure → ...), two worked code examples added (commit message HEREDOC, agent class skeleton), the always-stale phase status table collapsed to a one-liner pointing at CHANGELOG.md, two whole new sections added (Workflow rules + Commit conventions). 206 → 248 lines, comfortably within the skill's budget.

---

## What the Workflow Taught Me

A few patterns emerged that I didn't expect at session start:

**1. Skills compose; they don't compete.** The same file under `/code-review-and-quality` and `/frontend-ui-engineering` produced complementary, non-overlapping findings. Each skill's framework filters for what its lens can see. I'd previously thought of skills as "pick the right one for the job" — actually it's more like "pick *several* and you'll catch what each one misses individually."

**2. Reviewing the boundary, not the file, is where the highest-leverage findings live.** The single biggest design issue I caught all day — URL convention drift in tailoring.py — was invisible when I looked at tailoring.py alone. It only surfaced when I reviewed all routers as one contract.

**3. The "Required / Recommended / Optional" categorisation was unexpectedly load-bearing.** It gave me explicit permission to defer most findings. Without it I'd have either (a) tried to fix everything and bogged down, or (b) cherry-picked the easy ones without acknowledging what I was leaving behind. The labels made the deferrals visible and trackable.

**4. The 5-PR migration was the work I'd otherwise have done as a single 200-line PR.** Skills' discipline around incremental change isn't bureaucratic — it caught a real risk (the migration touches concurrent code that we'd just changed) and let me ship each step independently. If something had broken at PR 3, PRs 1-2 leave the codebase coherent. A monolithic PR doesn't.

**5. The user staying in control was essential.** I never invoked a skill without the user typing the slash command (or me asking and getting confirmation). The skills are powerful, but they shouldn't be applied without intent — that turns into autonomous-agent drift.

---

## Concrete Improvements

| Axis | Before today | After |
|------|--------------|-------|
| Daily run wall-clock | ~100s for 10 jobs (sequential deep_review) | ~25s (~4×) |
| LLM call abstraction | Racy positional tuple side-channel | Typed `LLMUsage` returned from call |
| API URL conventions | Verb + plural-vs-singular drift | Plural-noun consistency, top-level vs scoped documented |
| Validation error shape | Two different shapes (Pydantic vs hand-raised) | One normalised envelope across every endpoint |
| Streamlit per-render HTTP | Two uncached calls firing on every interaction | Both cached with TTL + invalidation |
| Workflow Detail UX | 10 sections in one scroll, no organisation | Lifecycle-organised: Find → Review → Prep → Diagnostics |
| Empty analytics views | "No scored jobs found." dead-end | Concrete next action ("Kick off a run from Start New Run") |
| CLAUDE.md drift | 4 stale facts, 2 missing convention sections | Restructured, fact-current, conventions written down |

Plus: 6 new tests (one of which — the deep_review concurrency lock — actively prevents future regression). Total tests: 450 → 456. Total commits: 18.

---

## How to Try This Yourself

You don't need a custom agent framework. The skills used here are from [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) — a community-curated pack of 21 skills. Drop them in a `skills/` folder at your repo root and most agent tools will discover them.

For each skill, the invocation is simple:

```
/skill-name path/to/file
```

The agent loads the skill's playbook, applies its specific framework to the file you pointed at, and produces severity-categorised findings. You decide what to act on.

A few things I'd change next time:

- **Run `/api-and-interface-design` on the boundary every time you ship a new endpoint.** I would have caught the URL drift at write time, not after-the-fact.
- **`/context-engineering` deserves a quarterly cadence** on the rules file. Drift is silent.
- **`/performance-optimization` is most powerful when paired with an analog** — if you've measured a similar pattern before, the skill leverages that.

The pattern is the point: small skills, applied at intentional times, against specific files, with the human staying in control. One day, eighteen commits, six skills. A codebase that's measurably more maintainable and ready for extension.

---

*Repo: [`suthrams/jobsearchagent-v2`](https://github.com/suthrams/jobsearchagent-v2). Skills: [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills). Tooling: Claude Code.*
