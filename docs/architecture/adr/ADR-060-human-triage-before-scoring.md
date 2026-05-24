# ADR-060: Human triage before scoring - widen discovery, score only the jobs the user keeps

## Status

Accepted (2026-05-23). Resolutions on the four open calls:
1. **Shape: Option A** (out-of-graph / phased), not the in-graph interrupt.
2. **Phase stitching: continue the same `workflow_id`.** All persistence
   (scores, selection, report, llm_calls, run_metrics) is keyed to the original
   `workflow_run_id`, so history shows one run that went discover -> (await
   selection) -> score -> review -> report. Phase 2 re-enters the *same*
   compiled graph via a conditional entry point that jumps straight to
   `score_jobs`; the langgraph checkpoint thread is an implementation detail
   (checkpoints are resumption-only and not read by the UI).
3. **Opt-in flag** `scoring.manual_selection` (default `false`) - accepted.
4. **Article 8 waits for this feature**, then is updated to match the system as
   built (the HITL-approaches diagram gains a curate-before row).

Relates to ADR-052 (reduce MAX_JOBS_PER_RUN as cost control), ADR-054 (every
qualifying job reaches deep review), ADR-055 (on-demand tailoring as an
out-of-graph operation), and ADR-059 (retire in-graph HITL; reserve
interrupt-before for irreversible actions). It revisits the cost lever ADR-052
chose and proposes a different one; it must stay consistent with ADR-059's
"the graph runs end to end with no `interrupt()`" property.

## Context

Today the workflow scores every discovered job. The graph runs:

```
register_run -> discover_jobs -> load_resume -> score_jobs
  -> await_job_selection (auto-select) -> deep_review -> career_advice
  -> interview_prep -> generate_report
```

`score_jobs` runs **ResearchAgent + ScoringAgent for every normalised job** -
up to two successful LLM calls per job (the node's own comment: "Each job costs
at most 2 successful LLM calls (research + scoring)"). Only after scoring does
`await_job_selection` (`auto_select_jobs`) keep the top `MAX_SELECTED_JOBS = 3`
jobs whose best track score clears `min_match_score` (default 75).

The cost observation that prompted this ADR: **we pay the research+score cost
on every discovered job, including the many that the user would never pursue
and that will fall below threshold anyway.** Scoring exists to rank jobs the
human cannot easily judge - but a large share of a raw scrape is judged
trivially by a human from the title, company, location, and snippet alone
("not this company", "wrong level", "wrong country"). For those, two LLM calls
per job is spend with no decision value.

Three facts shape the design:

1. **The blunt control is already in place and it hurts coverage.** ADR-052
   reduced `MAX_JOBS_PER_RUN` to 10 specifically because scoring all discovered
   jobs was too expensive. That caps cost by *narrowing the net* - the user
   cannot cast widely without paying to score everything caught. The lever this
   ADR proposes (score only what the human keeps) would let discovery widen
   again without the cost scaling with the scrape size.

2. **A cheap deterministic filter exists but cannot capture relevance.**
   `JobDiscoveryService` already drops titles matching `EXCLUDED_TITLE_KEYWORDS`
   before anything is scored. That removes rule-expressible junk, but "jobs I
   don't care about" is largely subjective and contextual - it is exactly the
   judgment a keyword list cannot make and a human makes in seconds.

3. **Scoring is pinned cheap, so the per-job cost is small but linear.**
   Research and scoring run on the cost-cap cheap-only allowlist. At 10 jobs the
   waste is modest; the lever only becomes material once discovery widens (50,
   100), which is precisely the use case the user wants to enable.

There is a chicken-and-egg to respect: the human triages on *raw* fields, the
scoring agent adds *fine-grained* track fit (ic / architect / management). The
two are complementary - the human removes obvious nos, scoring ranks the
plausible remainder. The design must not push the human into doing the scoring
agent's fine-grained job.

Finally, the tension with ADR-059. ADR-059 retired interrupt-before and
reserved it for irreversible, side-effecting actions, because the only artifact
the system gated (a tailored resume) is reversible. Inserting a human step
*before scoring* is a human-in-the-loop addition - but its motive is **cost and
relevance triage, not guarding an irreversible action.** That is a legitimate
second reason to involve a human that ADR-059's irreversibility framing did not
consider. To avoid contradicting ADR-059's hard-won "no `interrupt()` in the
graph" property, the triage should be modelled as an out-of-graph, phased
operation (ADR-055's shape), not as an in-graph pause.

## Decision (proposed)

Insert an **optional human triage step between discovery and scoring**: scrape
broadly, present the raw jobs, let the user select which to score, and run
research+scoring only on the selected set. Downstream
(`auto_select_jobs` -> `deep_review` -> ...) is unchanged and still applies to
the scored subset.

Specifics proposed for review:

1. **Opt-in, not a forced UX change.** A per-run config flag
   (`scoring.manual_selection`, default `false`). Off = today's behavior
   (auto-score all discovered jobs, capped). On = discover-wide, present,
   human-select, score-selected. This keeps the default path and its tests
   intact and makes the feature reversible.

2. **Out-of-graph / phased shape (preserves ADR-059).** When manual selection
   is on, the graph runs phase 1 only - `register_run -> discover_jobs ->
   load_resume` - persists the discovered jobs *unscored*, and ends in an
   `awaiting_scoring_selection` status. The UI lists the raw jobs. A user
   action (`POST /workflows/{wf}/scoring` with the selected job ids) triggers
   phase 2 - `score_jobs` (on the selected set only) -> `auto_select_jobs` ->
   downstream -> `generate_report`. No `interrupt()` is introduced; the human
   choice sits between two phases, exactly as the tailoring decision sits
   outside the graph (ADR-055).

3. **Widen the discovery cap for this mode.** Introduce a separate
   `MAX_DISCOVERED_JOBS` (the wide net, e.g. 50) distinct from the existing
   per-run scoring exposure. `MAX_JOBS_PER_RUN` (the scored cap, ADR-052)
   continues to bound how many jobs the user can send to scoring in one phase-2
   trigger, so the cost ceiling ADR-052 protects is preserved.

4. **Keep the deterministic pre-filter.** `EXCLUDED_TITLE_KEYWORDS` still runs
   at discovery, so the human triages a list already stripped of
   rule-expressible junk. Human triage is the subjective layer on top, not a
   replacement.

## Options considered

- **A. Out-of-graph / phased (recommended).** Discovery and scoring become two
  phases stitched by the workflow-run record, with the human selection in
  between via REST. Pro: no `interrupt()`, consistent with ADR-055/ADR-059,
  cleanly reversible (opt-in). Con: requires splitting the graph into two
  invocations on one run and a new phase-2 entry point - the main implementation
  cost and the one genuinely open design question (how to stitch two graph runs
  on a single `workflow_run_id` vs. running phase 2 as a fresh graph that reads
  the persisted unscored jobs).

- **B. In-graph interrupt-before-scoring.** `discover_jobs -> interrupt() ->
  score_selected`. Pro: conceptually simplest, single run. Con: reintroduces
  the checkpoint/resume machinery ADR-059 deliberately removed and makes the
  "no `interrupt()`" property (and the article built on it) false. Rejected
  unless A proves impractical.

- **C. Deterministic-only, no human step.** Tighten `EXCLUDED_TITLE_KEYWORDS`
  and the Adzuna query so fewer irrelevant jobs are scraped at all. Pro: zero
  new gate, zero UI, zero LLM, fits the "keep it deterministic" principle
  (the Article-10 / when-not-to-agentize lens). Con: cannot express subjective
  relevance; the user's actual filter ("don't care about this one") is not
  rule-shaped. Adopted *alongside* A (keep the filter), not instead of it.

- **D. Cheap LLM pre-screen.** A single cheap keep/drop classification call per
  job before the full research+score. Con: still an LLM call per job, so it only
  partially avoids the cost it is meant to save, and it puts a model back in the
  relevance-judgment seat the user wants to own. Rejected.

## Rationale

- **Spend attention and tokens where they have decision value.** Research+score
  is wasted on jobs a human discards on sight. Moving that selection to the
  human is the cheapest possible filter for the subjective layer.
- **Targeted cap beats blunt cap.** ADR-052 controlled cost by narrowing
  discovery; this controls cost by narrowing *scoring*, which lets the user
  widen discovery again - better coverage at the same or lower spend.
- **A new, honest reason for a human step.** The system's gating rule has been
  "gate the irreversible." This adds "also involve the human where their cheap
  judgment avoids expensive agent work" - cost/relevance triage. Modelled
  out-of-graph, it adds a human-in-the-loop step without reintroducing
  interrupt-before, so ADR-059 stands.
- **Filter input, in scope.** Selecting which jobs to score is a *signal the
  user gives the system* (a filter input), not outcome tracking - squarely
  inside the project's filter-vs-tracker boundary.

## Consequences

### Positive

- Lower cost per run when the user triages, and the ability to cast a much
  wider discovery net without the cost scaling with it.
- The human gets agency over what is worth the expensive treatment, which is
  closer to how a person actually job-searches.
- Backwards compatible: default `manual_selection=false` preserves today's
  behavior and tests.

### Tradeoffs

- Real implementation cost: splitting the graph into two phases and adding a
  phase-2 trigger + a selection UI. The phase-stitching approach is an open
  design question to settle before coding (A's main risk).
- A second discovery/scoring path to maintain and test (auto vs. manual).
- The human triages on raw fields only; a job that would have scored well but
  reads unpromising can be cut before it is ever scored (a self-inflicted
  version of the article's "invisible discard pile" - worth surfacing the
  count of un-scored jobs so the user sees what they skipped).

### Neutral

- Schema/persistence: discovered-but-unscored jobs must be persisted and
  readable by the UI read-path (`db_reader.py`), per the persistence rule.
- New limit `MAX_DISCOVERED_JOBS` and config key `scoring.manual_selection`
  to document in `config_model.md` and `CLAUDE.md`.
- **Article sequencing.** Article 8 ("Gate the irreversible, not everything")
  states the system uses no interrupt-before. Option A keeps that literally
  true, but it adds a *curate-before-scoring* pattern not shown in the article's
  HITL-approaches diagram. If this ships before the article publishes, the
  diagram gains a sixth row (curate-before: triage which work to spend on) and
  the prose gains a line; if it ships after, the article remains an accurate
  snapshot of the system at publish time.

## Implementation notes (high level - for after acceptance)

- `app/workflows/limits.py` - add `MAX_DISCOVERED_JOBS`; keep
  `MAX_JOBS_PER_RUN` as the scored-per-phase cap.
- `app/workflows/workflow_graph.py` - gate the `load_resume -> score_jobs`
  edge on `manual_selection`; in manual mode end phase 1 after `load_resume`
  with `awaiting_scoring_selection`. Settle the phase-2 entry approach
  (continue same run vs. fresh graph over persisted unscored jobs).
- New `POST /workflows/{wf}/scoring` (or similar) accepting selected job ids;
  validate ids belong to the run; trigger phase 2.
- Persist discovered-unscored jobs; mirror in `db_reader.py`; add a
  selection UI listing raw jobs (title/company/location/snippet/url) with a
  skipped-count readout.
- Tests: a manual-mode path (discover -> select -> score subset -> downstream)
  plus the unchanged auto-mode path; an invariant that scoring runs only on
  selected ids in manual mode.

## References

- ADR-052 - Reduce MAX_JOBS_PER_RUN as a cost control (the blunt cap this
  revisits).
- ADR-054 - Allow deep review for all qualifying jobs (downstream unchanged).
- ADR-055 - On-demand tailoring as an out-of-graph operation (the shape reused).
- ADR-059 - Retire in-graph HITL; reserve interrupt-before for irreversible
  actions (the property this must not break).
