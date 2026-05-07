# Per-Agent Model Recommendations

The system supports per-agent provider/model selection (ADR-053).
Choosing the right model per agent is the **single biggest cost lever** —
the same workflow can run for $0.17 or $1.40 depending on assignment.
This document captures the recommended baseline plus the override
playbook.

> **Set this in the UI:** Settings → Agent Models → pick provider +
> model per agent → Save → restart the backend (`uvicorn` must
> re-init `ModelRegistry` for changes to take effect).

> **Single source of truth in code:**
> [`app/providers/model_registry.py::DEFAULT_AGENT_ASSIGNMENT`](../app/providers/model_registry.py).

---

## Recommended baseline

| Agent | Model | Why |
|---|---|---|
| `research_agent` | `claude-haiku-4-5-20251001` | High-volume per-job extraction (~10/run). Rubric is deterministic — Haiku handles it. |
| `scoring_agent` | `claude-haiku-4-5-20251001` | Same as research — per-job, structured output, multi-track. |
| `resume_critic` | `claude-haiku-4-5-20251001` | The Review Auditor loop polices critic quality; let it do its job. Sonnet here was 80% of run cost in observed data. |
| `review_auditor` | `claude-haiku-4-5-20251001` | Pure quality-checking task — reads critic output, applies criteria, scores. No generation needed. |
| `career_advisor` | `claude-sonnet-4-6` | User-facing positioning prose. Output goes into the report and shapes how the candidate thinks about the opportunity. Worth Sonnet on 3 calls/run. |
| `interview_coach` | `claude-sonnet-4-6` | 7-day plan synthesis benefits from Sonnet's coherence; "areas to defend" needs cross-job awareness. |
| `tailoring_agent` | `claude-sonnet-4-6` | **THE load-bearing quality lever.** Page-budget contract (ADR-056) + JD-anchoring + section_label all need precision Haiku struggles with. Don't downgrade. |
| `fidelity_reviewer` | `claude-haiku-4-5-20251001` | Pattern-matching validation — does the suggested text quote the evidence, stay within word limits, declare a real section_label? Checking, not generating. |
| `resume_parser` (one-shot) | `claude-sonnet-4-6` | Cached after first parse via `raw_text_hash`. Quality matters because every downstream agent consumes the parsed `ResumeProfile`. One-time cost amortizes across all subsequent runs. |
| `custom_url_extractor` (conditional) | `claude-sonnet-4-6` | Variable HTML shapes across job boards need strong extraction. Rare invocation; cost-marginal. |

This baseline matches the current `DEFAULT_AGENT_ASSIGNMENT` in
`model_registry.py` after the cost cuts shipped 2026-05-05.

---

## Estimated cost per run with this baseline

Under current limits (`MAX_JOBS_PER_RUN=10`, `MAX_SELECTED_JOBS=3`,
`MAX_REVIEW_ROUNDS=2`):

| Phase | Model mix | Approximate cost |
|---|---|---|
| Discovery (10 jobs × research + scoring, both Haiku) | Haiku × 20 calls | ~$0.08 |
| Deep review (3 jobs × up to 2 rounds, Haiku critic + auditor) | Haiku × ~12 calls | ~$0.12 |
| Career advice (3 jobs, Sonnet) | Sonnet × 3 | ~$0.06 |
| Interview prep (3 jobs, Sonnet) | Sonnet × 3 | ~$0.06 |
| **Subtotal — full run, no tailoring** | | **~$0.32** |
| Per tailored draft (Sonnet tailoring + Haiku fidelity) | Sonnet + Haiku | ~$0.03 |

**$25 budget supports ~70-80 runs** at this baseline.

> **Updated 2026-05-07:** prior projections of ~$0.17/run and ~110-130
> runs/budget were computed against an outdated Haiku 4.5 rate
> ($0.25/$1.25 per million); the rate table below has the corrected
> $1.00/$5.00 figures. Re-baseline against your **Cost Dashboard** after
> the next run.

Verify your numbers in **Cost Dashboard** after the next run; if the
totals match within 20% you're on the baseline.

---

## Cost rates (May 2026, per 1M tokens)

| Provider | Model | Input | Output | vs Sonnet (input) |
|---|---|---:|---:|---|
| Anthropic | `claude-haiku-4-5-20251001` | $1.00 | $5.00 | **3× cheaper** |
| Anthropic | `claude-sonnet-4-6` | $3.00 | $15.00 | baseline |
| Anthropic | `claude-opus-4-7` | $15.00 | $75.00 | **5× more expensive** |
| OpenAI | `gpt-4o-mini` | $0.15 | $0.60 | **20× cheaper** |
| OpenAI | `gpt-4o` | $2.50 | $10.00 | similar |
| OpenAI | `o1` | $15.00 | $60.00 | **5× more expensive** |

Rates live in
[`app/providers/claude_provider.py::_PRICING`](../app/providers/claude_provider.py)
and
[`app/providers/openai_provider.py::_PRICING`](../app/providers/openai_provider.py).
Update both files when provider rates change.

### Prompt-cache modifiers (Anthropic ephemeral, 5-min)

Cached calls are not billed at the flat input rate. The provider applies
these multipliers to the per-model input rate when computing per-call cost:

| Token category | Multiplier on input rate |
|---|---:|
| `cache_creation_input_tokens` (write) | 1.25× |
| `cache_read_input_tokens` (read) | 0.10× |
| Regular input tokens | 1.00× |

Constants live in `app/providers/claude_provider.py`
(`_CACHE_WRITE_MULTIPLIER`, `_CACHE_READ_MULTIPLIER`).

---

## Escalation order if budget pressure mounts

Apply the cheapest quality-cost cut first. Each step is independent;
stop when you hit a budget you can live with.

| # | Action | Saves per run | Quality cost |
|---|---|---:|---|
| 1 | Move `interview_coach` to Haiku | ~$0.045 | Prep plan reads more generic; less defensible-areas insight |
| 2 | Move `career_advisor` to `gpt-4o-mini` (needs `OPENAI_API_KEY`) | ~$0.045 | Positioning prose less polished; same factual content |
| 3 | Lower `MAX_SELECTED_JOBS` to 2 in `app/workflows/limits.py` | ~$0.07 | Only top-2 qualifying jobs reach deep review per run |
| 4 | Lower `MAX_REVIEW_ROUNDS` to 1 | ~$0.015 | Critic doesn't iterate; first-pass review only |

**Do not cut `tailoring_agent`.** That's the artifact users actually
paste into their resume — cheap tailoring is worse than no tailoring.
Quality erosion compounds across drafts because the user iterates.

---

## When to upgrade

| Symptom | Lever |
|---|---|
| Critic consistently produces generic gaps; `review_rounds.audit_score` stuck below 75 with `stop_reason="max_rounds"` | Move `resume_critic` back to Sonnet OR raise `MAX_REVIEW_ROUNDS` to 3 |
| Tailoring drafts you'd actually use are <50% (you keep clicking Reject) | Try `claude-opus-4-7` for one tailoring session, measure quality, decide if the cost premium is worth it for THIS job |
| Career advice reads as table-stakes / not insightful | Try `gpt-4o` instead of `claude-sonnet-4-6` — different generative voice, sometimes lands better |

**Never put any agent on `claude-opus-4-7` as a default.** Opus is ~5×
the cost of Sonnet — fine for one-off "I really care about this job"
overrides, terrible as a baseline.

---

## When to use OpenAI instead of Claude

`OPENAI_API_KEY` enables OpenAI providers in the model picker.
Reasonable cases for switching:

- **Claude rate-limited.** Move `research_agent` and `scoring_agent`
  (the per-job high-volume agents) to `gpt-4o-mini` to keep workflows
  running while Claude cools off. They're already cheap on Haiku;
  gpt-4o-mini is even cheaper (~40%).
- **Cost optimization beyond Tier 1 cuts.** `gpt-4o-mini` is the
  cheapest model in the registry. Best fit for high-volume agents that
  are already on Haiku.
- **Different generative voice.** `gpt-4o` for `career_advisor` /
  `interview_coach` if Sonnet's prose feels off for your domain.

OpenAI providers gracefully no-op (default falls back to Claude) when
`OPENAI_API_KEY` is unset — see
[`app/providers/model_registry.py::ModelRegistry.build`](../app/providers/model_registry.py).

---

## Verifying your assignment in production

After applying changes and restarting the backend, run a clean test
workflow with the same search criteria as a recent run.

1. **Open Cost Dashboard.** Total spend for the new run should land
   in the predicted range from the table above.
2. **Open Workflow Detail → 💰 Cost breakdown.** Per-agent bar chart
   should show the agent + model combinations you set.
3. **Compare against the prior run.** The Workflow History Cost
   column reads from the `llm_calls` audit trail (see
   `cost_troubleshooting.md` Step 4) so the run-over-run comparison
   is meaningful.

If the new run's per-agent attribution doesn't match what you set,
either the backend wasn't restarted (model assignment changes require
a process restart per ADR-053) or the override didn't save — check
**Settings → Agent Models** again.

---

## Per-agent rationale (longer notes)

For readers who want the reasoning behind each pick.

### research_agent — Haiku

ReAct loop is bounded at `MAX_RESEARCH_STEPS=2`. Output is a structured
`ResearchContext` (company stage, tech stack, role shape). Quality
requirement is "extract from JD without hallucinating." Haiku is
fine. Volume is 1 call per discovered job (~10/run typically).

### scoring_agent — Haiku

Multi-track scoring against a deterministic rubric. The score values
end up driving threshold filters; precision matters more than
generative flourish. Haiku produces reliable structured output and is
12× cheaper than Sonnet. Volume is 1 call per discovered job.

### resume_critic — Haiku (changed 2026-05-05)

Was Sonnet — observed data showed it was 80% of run cost (16 calls × ~$0.025
in the credit-blow run). The Review Auditor loop runs after the
critic and scores its output; if quality drops below
`AUDIT_QUALITY_THRESHOLD=75`, the critic re-runs with the auditor's
feedback (within `MAX_REVIEW_ROUNDS` cap). The loop is the safety net,
so the critic itself can be cheaper.

If you observe consistent `audit_score < 75` with
`stop_reason="max_rounds"`, that's the signal to move back to Sonnet
or raise the round cap.

### review_auditor — Haiku

Reads the critic's output and applies a checklist: are gaps
specific? Is feedback generic? Are claims supported? This is a
checking task, not a generative one. Haiku does it well.

### career_advisor — Sonnet

The output appears in the user-facing report and the per-job advice
panel. The candidate uses this to decide whether to apply, how to
position themselves, what to talk to in cover letters. Cheap-feeling
advice undermines the user's trust in the whole tool. Worth Sonnet on
3 calls per run (~$0.06).

### interview_coach — Sonnet

7-day prep plan, likely topics, areas to defend. Sonnet's coherence
across multi-section synthesis is noticeable here. Could move to
Haiku for $0.045/run savings if budget really pressed; quality drops
but doesn't break.

### tailoring_agent — Sonnet (do not change)

Tailoring is the most precision-sensitive agent. The contract requires:
- Page-budget word count within `0.85x..1.05x` of original (ADR-056)
- `section_label` matching a real resume section
- `impact_rationale` referencing a concrete JD signal
- `claim_type` correctly classified (`reword` / `emphasize` / `gap` / `remove`)

Haiku struggles with this much structural precision. The Fidelity
Reviewer rejection rate jumps materially on Haiku tailoring outputs.
Net cost INCREASES because users generate more drafts. Keep Sonnet.

### fidelity_reviewer — Haiku

Pure validation: does suggested_text quote supporting_evidence? Is the
word count in band? Is section_label valid? These are deterministic
checks, not generation. Haiku is the right call.

### resume_parser — Sonnet (one-shot)

Parses the PDF into `ResumeProfile` (name, headline, summary,
experience entries, skills, education). Every downstream agent
consumes this — bad parsing cascades. The `raw_text_hash` cache means
this fires once per resume, then never again. The one-time cost is
amortized across all subsequent runs.

### custom_url_extractor — Sonnet (conditional)

Heuristics try first (JSON-LD, OpenGraph, article tags). Only when
heuristics fail does this agent run. HTML shapes vary across job
boards; cheaper models fail more often. Better to use Sonnet on the
rare fallback than fail extraction and lose the URL entirely.

---

## References

- [`docs/cost_troubleshooting.md`](cost_troubleshooting.md) — diagnosing
  per-run cost, reconciling against the provider console, the lever
  decision matrix.
- [`docs/architecture/adr/ADR-053-pluggable-per-agent-provider-and-model-selection.md`](architecture/adr/ADR-053-pluggable-per-agent-provider-and-model-selection.md)
  — the architectural foundation that makes per-agent assignment
  possible.
- [`docs/architecture/adr/ADR-056-tailoring-page-budget-and-section-grouping.md`](architecture/adr/ADR-056-tailoring-page-budget-and-section-grouping.md)
  — the contract that explains why `tailoring_agent` cannot be cheap.
- [`CHANGELOG.md`](../CHANGELOG.md) entry for 2026-05-05 — observability
  fix and cost cuts that established the current baseline.
