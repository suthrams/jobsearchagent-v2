# Resume Clinic — Chat-revise loop, visualised

Companion visualisation for [ADR-068](adr/ADR-068-chat-revise-loop-for-the-resume-clinic.md)
and its [implementation walkthrough](resume_clinic_chat_implementation_walkthrough.md).
Same style as `resume_clinic_strategy.md`, focused only on the agent graph and
data flow the chat-edit feature introduces.

## The two clinic agents at a glance

```mermaid
flowchart LR
    user[Human user] -->|runs clinic once| RR[ResumeReviewerAgent]
    user -->|chats per turn| RC[ResumeChatAgent]

    RR -->|ResumeClinicReview - quality + alignment + overhaul| FID1[Fidelity Reviewer]
    RC -->|ResumeChatTurnResult - reply + revised overhaul| FID2[Fidelity Reviewer]

    FID1 -->|verdict| DB[(resume_clinic_reviews)]
    FID2 -->|verdict| DB

    DB -->|edited_json or overhaul_json plus decision| RENDER[Resume renderer]
    RENDER -->|md / txt / html / json / docx / pdf| user
```

- `ResumeReviewerAgent` lives at `app/agents/resume_reviewer.py`. One call per
  clinic run.
- `ResumeChatAgent` lives at `app/agents/resume_chat.py`. One call per chat turn.
- `Fidelity Reviewer` (`app/agents/fidelity_reviewer.py`) runs after both, with
  the same evidence-binding prompt.
- The renderer is `app/services/resume_text_renderer.py::compose_resume`.

The reviewer and the chat agent are **separate** — different prompts, different
output schemas, separate model pins, separate cost rows. They **share** the
output overhaul shape so the renderer and the Fidelity Reviewer translation
glue are reused unchanged.

## One chat turn, end to end

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant API as ClinicRouter
    participant CHAT as ResumeChatAgent
    participant FID as FidelityReviewer
    participant REPO as ClinicRepo
    participant RENDER as Renderer

    U->>API: POST /chat with message, section, history
    API->>API: load clinic row and resume
    API->>API: build current_overhaul from edited_json or overhaul_json
    API->>CHAT: parsed_profile, current_overhaul, history, section, message
    CHAT-->>API: reply, overhaul, changed_sections
    alt overhaul has rewrites
        API->>FID: translated context via build_fidelity_context_for_overhaul
        FID-->>API: verdict (or 502 returns null)
    end
    API->>REPO: set_edited(clinic_id, new_overhaul, fidelity)
    Note over REPO: decision UNCHANGED. edited_json and fidelity_review_json updated.
    API-->>U: reply, overhaul, fidelity_review, changed_sections
    U->>RENDER: compose_resume(profile, overhaul, edited, decision)
    Note over RENDER: edited wins regardless of decision (except reject).
    RENDER-->>U: live markdown preview
```

## The state machine for `edited_json` × `decision`

The composer's rule from ADR-068: **prefer `edited_json` whenever populated, except on `reject`**.

```mermaid
stateDiagram-v2
    [*] --> NoEditYet : clinic runs

    NoEditYet --> ChatEditing : chat turn writes edited_json
    NoEditYet --> Approved    : Approve button
    NoEditYet --> Revising    : Revise button
    NoEditYet --> Rejected    : Reject button

    ChatEditing --> ChatEditing : another chat turn
    ChatEditing --> FinalEdit   : Save final edit sets decision=edit
    ChatEditing --> NoEditYet   : Discard chat edits
    ChatEditing --> Rejected    : Reject button

    Approved --> ChatEditing : chat turn (decision stays approve)
    Revising --> ChatEditing : chat turn (decision stays revise)
    FinalEdit --> [*]
    Rejected  --> [*] : composer renders ORIGINAL resume

    note right of NoEditYet : edited_json null, decision null - render overhaul_json with preview banner.
    note right of ChatEditing : edited_json populated, decision unchanged - render edited_json with editing banner.
    note right of FinalEdit : edited_json populated, decision edit - render edited_json with no banner.
    note right of Rejected : decision reject - composer falls back to the ORIGINAL resume regardless of edits.
```

## Where each piece lives

```mermaid
flowchart TD
    subgraph Schemas
        S1[app/schemas/resume_clinic.py - ResumeOverhaul, RewriteSuggestion, Reorganization, ResumeClinicReview]
        S2[app/schemas/resume_chat.py - ResumeChatTurnResult]
    end

    subgraph Agents
        A1[app/agents/resume_reviewer.py]
        A2[app/agents/resume_chat.py]
        A3[app/agents/fidelity_reviewer.py]
    end

    subgraph Prompts
        P1[app/prompts/agents/resume_reviewer.txt]
        P2[app/prompts/agents/resume_chat.txt]
        P3[app/prompts/agents/fidelity_reviewer.txt]
    end

    subgraph Runtime
        RU[app/services/resume_clinic_runner.py]
        REN[app/services/resume_text_renderer.py]
    end

    subgraph Endpoints
        E1[POST users id resume-clinic]
        E2[POST resume-clinic id decisions]
        E3[POST resume-clinic id chat]
        E4[POST resume-clinic id discard-edits]
        E5[GET resume-clinic id export]
        E6[GET users id resume-clinic]
    end

    subgraph Persistence
        D1[(resume_clinic_reviews)]
    end

    subgraph UI
        U1[Quality scorecard]
        U2[Decision controls]
        U3[Refine with feedback panel]
        U4[Export panel]
    end

    A1 --> P1
    A2 --> P2
    A3 --> P3

    E1 --> RU
    RU --> A1
    RU --> A3
    RU --> D1

    E3 --> A2
    E3 --> A3
    E3 --> D1
    E4 --> D1
    E2 --> D1
    E5 --> REN
    E5 --> D1
    E6 --> D1

    U1 --> E1
    U2 --> E2
    U3 --> E3
    U3 --> E4
    U3 --> REN
    U4 --> E5
```

Endpoint nodes drop the slashes and curly braces so the Mermaid parser doesn't
treat them as syntax. Full URLs are:

- `POST /users/{id}/resume-clinic`
- `POST /resume-clinic/{id}/decisions`
- `POST /resume-clinic/{id}/chat`
- `POST /resume-clinic/{id}/discard-edits`
- `GET  /resume-clinic/{id}/export?format=...`
- `GET  /users/{id}/resume-clinic`

## What the chat agent CAN and CANNOT do

```mermaid
flowchart LR
    in[User message plus section focus] --> RC[ResumeChatAgent]

    RC --> A1[Revise rewrites in the targeted section]
    RC --> A2[Reorder sections and record moves]
    RC --> A3[Decline off-topic asks and return overhaul unchanged]
    RC --> A4[Use placeholders like N or X percent when a metric is unstated]

    RC --> X1[Touch sections OTHER than the targeted one]
    RC --> X2[Fabricate experience, metrics, scopes, certifications]
    RC --> X3[Empty supporting_evidence]
    RC --> X4[Change the decision field, only Save and Reject buttons can]

    classDef can fill:#eef7ed,stroke:#2f8132,color:#1d4f1f
    classDef cant fill:#fde7e7,stroke:#b22a2a,color:#6a1818
    class A1,A2,A3,A4 can
    class X1,X2,X3,X4 cant
```

The "CAN" branch above is enforced by the chat agent's prompt. The "CANNOT"
branch is enforced by three layers stacked: the prompt, the Pydantic schema
(Literal enums + `min_length=1` on `supporting_evidence`), and the Fidelity
Reviewer's evidence-binding check on every turn.

## The cost shape per session

Tabular rather than a Mermaid diagram — costs are linear and a table reads
cleaner than a flowchart of subgraphs with dotted arrows.

| Phase | LLM calls | Approx cost | Notes |
|---|---|---|---|
| Initial clinic (once per resume) | 1 × `ResumeReviewerAgent` + 1 × `FidelityReviewer` | **~$0.10 fixed** | Runs when the user first opens the Resume Clinic on a profile. |
| Per chat turn (iterative) | 1 × `ResumeChatAgent` + 1 × `FidelityReviewer` | **~$0.017 per turn** (uncached) | Triggered by the user pressing **Send feedback**. |
| Typical session | 3 to 6 chat turns | **$0.15 – $0.25 total** | Initial clinic + chat refinements added together. |

The parsed profile is **cached in the second prompt block**, so subsequent
chat turns hit the 10% pricing tier on that block — real session cost runs
lower than the headline numbers above. Cost attribution flows through one
correlation id: every chat-turn `llm_calls` row is tagged with the clinic's
`workflow_run_id` (the lightweight `workflow_type="resume_clinic"` row the
original clinic runner wrote), so the per-profile **Cost Dashboard**
attributes the whole session — reviewer + every chat turn + every fidelity
call — to the same profile under one bucket.

## References

- [ADR-068](adr/ADR-068-chat-revise-loop-for-the-resume-clinic.md) — the
  decision and its tradeoffs.
- [Chat-revise implementation walkthrough](resume_clinic_chat_implementation_walkthrough.md) —
  the per-file plan that produced the code these diagrams describe.
- [ADR-066](adr/ADR-066-standalone-resume-clinic.md) — the standalone Resume
  Clinic this loop iterates on.
- [ADR-059](adr/ADR-059-retire-in-graph-hitl-and-add-human-edit-decision.md) —
  human-as-final-author; "Save final edit" submits a human draft that is
  trusted as-is.
- [`resume_clinic_strategy.md`](resume_clinic_strategy.md) — the original
  clinic strategy doc this companion follows in style.
