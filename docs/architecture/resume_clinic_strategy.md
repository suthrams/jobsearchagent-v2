# Resume Clinic — Strategy & Build Plan

> Visual companion to **ADR-066** (the decision record). This is the at-a-glance
> strategy to pick up when the feature comes off the shelf. Status: **Accepted,
> not yet built** — sequenced as option (a) (LLM-only v1, role-data grounding as a
> fast-follow).

## The reframe in one line

The app is a **job-search funnel**; every resume-facing agent is gated behind
discovering and scoring a job. That fails early-career users (the scoring rubric is
senior-tuned). The **Resume Clinic** is a **second surface**: it works on the
resume alone — no discovery, no scoring — to review, align to a target role, and
reorganize/overhaul.

```mermaid
flowchart LR
    R["Profile resume<br/>(per-profile, ADR-062)"]

    subgraph FUNNEL["Job-search funnel (existing)"]
      direction TB
      D["Discover"] --> SC["Score"] --> DR["Deep review"] --> AD["Advice"] --> T["Tailor"] --> IP["Interview prep"]
    end

    subgraph CLINIC["Resume Clinic (new - out-of-graph)"]
      direction TB
      Q["Quality review<br/>(always)"]
      AL["Role / track alignment<br/>(when a target is set)"]
      OV["Reorganize / overhaul<br/>(evidence-bound)"]
    end

    R --> FUNNEL
    R --> CLINIC
    FUNNEL -. "senior-tuned;<br/>fails grads" .-> X(("low value<br/>for grads"))
    CLINIC -. "resume help<br/>without a job" .-> V(("direct value"))
```

## What a clinic run produces

| Output | When | Shape |
|---|---|---|
| **Quality scorecard** | always | per-dimension rating (strong / adequate / needs-work) + findings + fixes; qualitative, no fake-precise score |
| **Role / track alignment** | when a target role/track is set | fit summary, missing skills/keywords, suggested certs/projects, what to emphasize |
| **Reorganize / overhaul** | always | section-reorder plan + per-bullet rewrites in the **tailoring claim-type shape** (reword / emphasize / gap / remove), each evidence-bound |

Dimensions graded: structure/ordering, impact/quantification, clarity, ATS/formatting,
consistency, length-fit, and seniority framing (the **seniority toggle** tunes this).

## How a run works

```mermaid
flowchart TD
    UI["Resume Clinic view<br/>resume + target role/track + seniority toggle"]
    UI -->|"POST /users/{id}/resume-clinic"| API["resume_clinic router"]
    API --> RUN["resume_clinic_runner.run_clinic()"]
    RUN --> WR["register a lightweight workflow_runs row<br/>(type=resume_clinic) - for per-profile cost"]
    WR --> LOAD["load resume: parsed profile + raw_text"]
    LOAD --> RDP{"RoleDataProvider<br/>configured?"}
    RDP -->|"yes"| GROUND["inject occupation skills / tools / certs"]
    RDP -->|"no / fail (v1 default)"| LLMONLY["LLM knowledge only"]
    GROUND --> REV["ResumeReviewerAgent"]
    LLMONLY --> REV
    REV --> OUT["ResumeClinicReview<br/>quality + alignment + reorg + rewrites"]
    OUT --> FID["FidelityReviewer<br/>(rewrites vs raw_text - no fabrication)"]
    FID --> DB[("resume_clinic_reviews")]
    DB --> RENDER["UI: scorecard + alignment +<br/>tailoring diff renderer"]
    RENDER --> DEC{"human decision"}
    DEC -->|"approve / edit / reject"| DB
```

The Fidelity Reviewer is **always** in the loop on the rewrites — the
evidence-binding invariant (ADR-015/056/059) holds with no job. A human `edit` is
owner-authored and not re-reviewed.

## Data model addition

```mermaid
erDiagram
    users ||--o{ resumes : owns
    users ||--o{ resume_clinic_reviews : owns
    resumes ||--o{ resume_clinic_reviews : reviewed
    resume_clinic_reviews {
        text id PK
        text user_id FK
        text resume_id FK
        text target_role
        text target_track
        int  seniority_aware
        text review_json
        text alignment_json
        text overhaul_json
        text fidelity_review_json
        text decision
        text edited_json
        text created_at
    }
```

Per-profile and repeatable (runs accumulate). A dedicated table — not overloading
the job-keyed `resume_reviews` / `tailored_resumes` with a null `job_id`.

## The role-data seam (counters stale LLM knowledge)

```mermaid
flowchart TD
    RUN["run_clinic"] --> IFACE[["RoleDataProvider.lookup(role, track)"]]
    IFACE --> NUL["NullRoleDataProvider<br/>v1 default -> None"]
    IFACE -. "fast-follow" .-> ESCO["EscoRoleDataProvider<br/>(free, no key, EU taxonomy)"]
    IFACE -. "fast-follow" .-> ONET["OnetRoleDataProvider<br/>(CareerOneStop token, US, + certs)"]
    NUL --> FB["LLM knowledge only"]
    ESCO --> GR["ground the alignment prompt<br/>with current required skills"]
    ONET --> GR
```

Division of labor: the **API says what the occupation requires** (current,
authoritative); the **LLM does resume craft + early-career/seniority positioning**.
Always degrades gracefully to LLM-only.

## Build sequence (gated; suite green at each phase)

```mermaid
flowchart LR
    P1["1 - Schema + repo<br/>resume_clinic_reviews"] --> P2["2 - ResumeReviewer agent<br/>+ output schema + prompt"]
    P2 --> P3["3 - Out-of-graph runner<br/>+ RoleDataProvider seam"]
    P3 --> P4["4 - API<br/>/users/{id}/resume-clinic"]
    P4 --> P5["5 - UI<br/>Resume Clinic view"]
    P5 --> P6["6 - Tests + docs"]
    P3 -. "fast-follow" .-> FF["ESCO then O*NET providers"]
```

## What we reuse (low net-new surface)

```mermaid
flowchart LR
    NEW["Resume Clinic"]
    NEW --> A["tailoring claim-type schema<br/>+ section/diff renderer"]
    NEW --> B["Fidelity Reviewer<br/>(evidence guard)"]
    NEW --> C["tailoring decision model<br/>(approve / edit / reject)"]
    NEW --> D["ResumeRepository<br/>(per-profile resume)"]
    NEW --> E["ModelRegistry + observability"]
    NEW --> F["out-of-graph runner pattern<br/>(ADR-055)"]
```

Genuinely new: one `ResumeReviewerAgent` + prompt, the `resume_clinic_reviews`
table + repo, the runner, one router, one UI view, and the `RoleDataProvider`
interface (+ `Null` default).

## Status & when we resume

- **Accepted, build deferred.** Tabled to make room for Article 9; pick up at
  Phase 1 when the feature returns.
- Validate the **Resume Reviewer prompt** against a real entry-level resume early —
  that prompt is where most of the output quality lives.
- See **ADR-066** for the full decision rationale and references.
