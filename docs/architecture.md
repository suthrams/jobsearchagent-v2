# Architecture Diagrams

All diagrams are written in [Mermaid](https://mermaid.js.org/) and render automatically on GitHub.

---

## 0. Solution Architecture — High-Level Overview

The simplest picture of the system: what goes in, what the agent does, and what comes out.

```mermaid
flowchart TD
    subgraph YOU["What You Bring"]
        R["Your Resume PDF"]
        PREFS["Your Preferences\nlocations, salary, tracks"]
    end

    subgraph SOURCES["Job Sources"]
        AZ["Adzuna API\nIncludes Indeed and Glassdoor"]
        LI["LinkedIn URLs\nmanual copy-paste intake"]
    end

    subgraph PIPELINE["Job Search Agent Pipeline"]
        SCRAPE["Scrape\ncollect raw listings"]
        FILTER["Pre-Filter Gate\nremove irrelevant roles cheaply"]
        SCORE["Claude AI — Scoring\nrate each job across 3 career tracks"]
        DB[("SQLite Database\nall jobs and scores")]
        TAILOR["Claude AI — Tailoring\nrewrite resume for chosen role"]
    end

    subgraph OUTPUTS["Your Results"]
        TERM["Terminal Table\nranked results with scores"]
        DASH["Streamlit Dashboard\nbrowse, filter, and trigger tailoring"]
        FILE["Tailored Resume\ntext file ready to customise"]
    end

    R --> SCORE
    R --> TAILOR
    PREFS --> SCRAPE
    PREFS --> SCORE
    AZ --> SCRAPE
    LI --> SCRAPE
    SCRAPE --> FILTER
    FILTER --> SCORE
    SCORE --> DB
    DB --> TERM
    DB --> DASH
    DB --> TAILOR
    DASH --> TAILOR
    TAILOR --> FILE

    style SCORE fill:#dbeafe,stroke:#3b82f6
    style TAILOR fill:#dbeafe,stroke:#3b82f6
    style DB fill:#fef9c3,stroke:#eab308
    style FILTER fill:#fee2e2,stroke:#dc2626
    style FILE fill:#dcfce7,stroke:#16a34a
```

---

## 1. System Architecture — Component Overview

High-level block diagram showing all five layers including the Streamlit dashboard UI.

```mermaid
graph TB
    subgraph UI["User Interfaces"]
        CLI["main.py\nCLI Terminal"]
        DASH["dashboard.py\nStreamlit Browser UI"]
    end

    subgraph AGENTS["Agent Layer"]
        PA["ProfileAgent\nResume to Profile"]
        SA["ScoringAgent\nBatch Scorer"]
        TA["TailoringAgent\nResume Tailoring"]
    end

    subgraph CLAUDE["Claude Layer"]
        CC["ClaudeClient\nAnthropic SDK"]
        PL["PromptLoader\nTemplate Engine"]
        RP["ResponseParser\nJSON Validator"]
    end

    subgraph DATA["Data Layer"]
        LI["LinkedIn Scraper"]
        AZ["Adzuna Scraper\nmulti-location"]
        LA["Ladders Scraper"]
        DB[("SQLite Database")]
        CACHE["Profile Cache\nprofile.json"]
        FILTERS["models/filters.py\nShared Filter Lists"]
    end

    subgraph EXTERNAL["External Services"]
        ANTHROPIC["Anthropic API\nSonnet (parsing/tailoring)\nHaiku (scoring)"]
        ADZUNA_API["Adzuna REST API\nper location × keyword"]
    end

    CLI --> PA
    CLI --> SA
    CLI --> TA
    DASH -->|"reads scores"| DB
    DASH -->|"triggers tailoring"| TA

    PA --> CC
    PA --> CACHE
    SA --> CC
    TA --> CC

    CC --> PL
    CC --> RP
    CC --> ANTHROPIC

    LI --> DB
    AZ --> DB
    LA --> DB
    AZ --> ADZUNA_API

    FILTERS -->|"EXCLUDED_TITLE_KEYWORDS"| AZ
    FILTERS -->|"EXCLUDED_TITLE_KEYWORDS\nTECH_DESCRIPTION_KEYWORDS"| SA
    FILTERS -->|"extract_us_state()"| DB

    SA --> DB
    TA --> DB
    PA --> DB

    style FILTERS fill:#fce7f3,stroke:#db2777
```

---

## 11. Streamlit Dashboard — UI Data Flow

How the browser dashboard reads and displays scored job data.

```mermaid
flowchart TD
    DB[("SQLite Database\njobs.db")]

    DB -->|"WHERE found_at >= run_at\nrun_at captured at run START"| LOAD_NEW["load_new_jobs()\ncached 30 seconds"]
    DB -->|"WHERE status = scored"| LOAD["load_jobs()\ncached 30 seconds"]
    DB -->|"SELECT * FROM runs"| LOAD_RUNS["load_runs()\ncached 30 seconds"]

    LOAD_NEW --> NDF["Pandas DataFrame\nall jobs from latest run\nincluding unscored"]
    LOAD --> DF["Pandas DataFrame\nall scored jobs"]
    LOAD_RUNS --> RDF["Pandas DataFrame\nall run records"]

    NDF --> V0["New Jobs View\nlatest run only\nscored + unscored"]

    DF --> SIDEBAR["Sidebar Controls\nmin score slider\nsearch filter\nstate multiselect\nview selector"]
    SIDEBAR --> FILTER["Filtered DataFrame\n(score + search + state)"]

    FILTER --> V1["Top Matches View\nall tracks ranked by best score\nscore metrics + job cards"]
    FILTER --> V2["IC Track View\nSenior Staff Principal Engineer\nranked by IC score"]
    FILTER --> V3["Architect Track View\nSolutions Principal Architect\nranked by architect score"]
    FILTER --> V4["Management Track View\nDirector VP Head of Eng\nranked by management score"]
    FILTER --> V5["Companies View\nbar chart of top companies\ndrill-down by company"]
    RDF --> V6["Run History View\ncost and token reporting"]

    V5 --> CHART["Plotly Bar Chart\ntop 20 companies\nby best match score"]
    V6 --> COST_CHART["Cost per Run + Cumulative Spend\nbar + line charts"]
    V6 --> TOKEN_CHART["Token Breakdown\nstacked bars by operation\nscoring vs parsing vs tailoring"]
    V6 --> RUN_TABLE["All Runs Table\ntimestamp cost tokens per run"]
    V1 --> CARDS["Job Cards\nexpandable detail view\nClaude summaries + links"]

    CARDS --> TAILOR_UI["Tailor Resume button\ntrack selector per card"]
    TAILOR_UI -->|"load once via cache_resource"| AGENTS["ProfileAgent + TailoringAgent\nAnthropicClient"]
    AGENTS -->|"Claude API call"| TRESULT["Tailored resume\nkeywords + gaps shown inline"]
    TRESULT --> APPLY["Mark as Applied button\nwrites status to SQLite"]

    style DB fill:#fef9c3,stroke:#eab308
    style LOAD_NEW fill:#fee2e2,stroke:#dc2626
    style V0 fill:#fee2e2,stroke:#dc2626
    style CHART fill:#dbeafe,stroke:#3b82f6
    style COST_CHART fill:#dbeafe,stroke:#3b82f6
    style TOKEN_CHART fill:#dbeafe,stroke:#3b82f6
    style CARDS fill:#dcfce7,stroke:#16a34a
    style AGENTS fill:#fce7f3,stroke:#db2777
    style TRESULT fill:#dcfce7,stroke:#16a34a
```

---

## 2. Main Run — Control Flow

End-to-end flow for `python main.py` (the default scrape + score command).

```mermaid
flowchart TD
    START([python main.py]) --> CONFIG[Load and validate config.yaml]
    CONFIG --> BOOT[Bootstrap DB, ClaudeClient, Agents]
    BOOT --> BACKFILL["db.backfill_states()\nfill state column for existing rows\nidempotent — skips rows already populated"]
    BACKFILL --> TIMESTAMP["run_started_at = datetime.now(tz=timezone.utc)\ncaptured BEFORE scraping\nused as run_at in DB so dashboard\nWHERE found_at >= run_at works"]
    TIMESTAMP --> SCRAPE

    subgraph SCRAPE["Scrape Phase"]
        S1["LinkedInScraper\ninbox/linkedin.txt"]
        S2["AdzunaScraper\nper location × keyword call\nAtlanta · Houston · Newark · Seattle"]
        S3["LaddersScraper\nHTML scraping"]
        S1 & S2 & S3 --> MERGE["Merge results — N jobs"]
    end

    MERGE --> DEDUP["Deduplicate by URL and title+company"]
    DEDUP --> INSERT["Insert new jobs — status = NEW\nfound_at = now()"]
    INSERT --> UNSCORED["Query: get_by_status(NEW)"]

    UNSCORED --> ESTIMATE["Estimate API cost and show to user"]
    ESTIMATE --> CONFIRM{User confirms y/N?}
    CONFIRM -- N --> CANCEL([Cancelled])
    CONFIRM -- Y --> PROFILE

    subgraph SCORE["Score Phase"]
        PROFILE["ProfileAgent.load()\nresume.pdf to Profile"]
        PROFILE --> FILTER["Filter via models/filters.py\nstale · no desc · excluded title · non-tech"]
        FILTER --> BATCH["Chunk into batches of 10"]
        BATCH --> CLAUDE["Claude API call\n5 jobs to 3-track scores\ntokens accumulated in ClaudeClient"]
        CLAUDE --> SAVE["db.update_job() — status = SCORED"]
        SAVE --> BATCH
    end

    SAVE --> USAGE["client.get_usage()\nread actual input+output tokens\nper operation"]
    USAGE --> RUN["db.insert_run(run_at=run_started_at)\npersist run stats + token counts\nto runs table"]
    RUN --> DISPLAY["print_scored_jobs()\nRich table + results.txt"]
    DISPLAY --> END([Done])

    style TIMESTAMP fill:#fee2e2,stroke:#dc2626
```

---

## 3. Agentic Pattern: Cache-Aside (ProfileAgent)

Shows how ProfileAgent avoids redundant Claude calls using a file-based cache.

```mermaid
sequenceDiagram
    participant M as main.py
    participant PA as ProfileAgent
    participant FS as File System
    participant PL as PromptLoader
    participant CC as ClaudeClient
    participant API as Anthropic API

    M->>PA: load("resume.pdf")
    PA->>FS: stat(data/profile.json)

    alt Cache is fresh — profile.json newer than resume.pdf
        FS-->>PA: cache mtime > resume mtime
        PA->>FS: read data/profile.json
        FS-->>PA: JSON string
        PA-->>M: Profile — no API call made
    else Cache is stale or missing
        FS-->>PA: cache missing or resume is newer
        PA->>FS: pdfplumber.open(resume.pdf)
        FS-->>PA: extracted text
        PA->>PL: load("parse_resume", resume_text)
        PL-->>PA: rendered system prompt
        PA->>CC: call(system, user, "resume_parsing")
        CC->>API: messages.create(model, tokens, prompt)
        API-->>CC: raw JSON text
        CC-->>PA: response string
        PA->>PA: ResponseParser.parse(raw, Profile)
        PA->>FS: write data/profile.json
        PA-->>M: Profile
    end
```

---

## 4. Agentic Pattern: Parallel Batched Fan-Out (ScoringAgent)

Shows how jobs are chunked into batches of 10, submitted concurrently via `ThreadPoolExecutor`, and merged back with thread-safe DB writes.

```mermaid
sequenceDiagram
    participant SA as ScoringAgent
    participant TPE as ThreadPoolExecutor
    participant CC as ClaudeClient
    participant API as Anthropic API
    participant DB as SQLite

    Note over SA: Pre-filter gate: stale / no desc / excluded title / non-tech
    Note over SA: 30 jobs → 3 batches of 10

    SA->>TPE: submit _run_batch(1, chunk_1)
    SA->>TPE: submit _run_batch(2, chunk_2)
    SA->>TPE: submit _run_batch(3, chunk_3)

    Note over TPE,API: All 3 batches fire concurrently (MAX_PARALLEL_BATCHES=3)

    par Batch 1
        TPE->>CC: call(system[cached], user, "job_scoring")
        CC->>API: messages.create — cache WRITE on first call
        API-->>CC: JSON array[0..9]
        CC-->>TPE: raw response
    and Batch 2
        TPE->>CC: call(system[cached], user, "job_scoring")
        CC->>API: messages.create — cache READ (90% cost reduction)
        API-->>CC: JSON array[0..9]
        CC-->>TPE: raw response
    and Batch 3
        TPE->>CC: call(system[cached], user, "job_scoring")
        CC->>API: messages.create — cache READ
        API-->>CC: JSON array[0..9]
        CC-->>TPE: raw response
    end

    Note over SA,DB: Results collected via as_completed()
    Note over SA,DB: Each batch result serialized to DB under db_lock

    loop Each completed future
        SA->>SA: parse_list(raw, BatchJobScore)
        SA->>SA: score_map keyed by job_index
        SA->>DB: update_job(job) — under db_lock
    end

    Note over SA: last_run_stats: elapsed_score_s, avg_batch_latency_s, jobs_per_second
```

**Thread safety notes:**
- `ClaudeClient._usage` increments are wrapped in `_usage_lock` — prevents lost updates from concurrent `+=`
- `db.update_job()` calls are serialized with `db_lock` — SQLite WAL mode allows concurrent reads but still serializes commits
- System prompt is byte-identical across all batches (`num_jobs` is in the user message only), so all concurrent calls share the same Anthropic prompt cache key

---

## 5. Agentic Pattern: Structured Output Pipeline

How raw Claude text becomes a validated, typed Python object at every agent boundary.

```mermaid
flowchart LR
    A["Claude raw text"] --> B["strip_code_fences\nremove markdown wrapping"]
    B --> C["extract_json\nfind first brace\nwalk to matching close"]
    C --> D["json.loads\nPython dict or list"]
    D --> E["Model.model_validate\nPydantic type check\nconstraint enforcement"]
    E --> F["Typed Python object\nProfile or TrackScores\nor BatchJobScore"]

    style A fill:#ffeeba,stroke:#e0a800
    style F fill:#d4edda,stroke:#28a745

    B --> ERR1["ResponseParseError\nno JSON found"]
    D --> ERR2["ResponseParseError\ninvalid JSON"]
    E --> ERR3["ResponseParseError\nschema mismatch"]

    style ERR1 fill:#f8d7da,stroke:#dc3545
    style ERR2 fill:#f8d7da,stroke:#dc3545
    style ERR3 fill:#f8d7da,stroke:#dc3545
```

---

## 6. Job Lifecycle — Pipeline State Machine

Every job moves through a defined set of states stored in the database.

```mermaid
stateDiagram-v2
    [*] --> NEW : Scraper creates job

    NEW --> SCORED : ScoringAgent scores via Claude
    NEW --> NEW : Re-run skipped by deduplication

    SCORED --> APPLIED : User tailors and confirms application
    SCORED --> REJECTED : User decides not to apply

    APPLIED --> REJECTED : Company rejects or user withdraws
    APPLIED --> OFFER : Company extends offer

    OFFER --> [*]
    REJECTED --> [*]
```

---

## 7. Resume Tailoring — Sequence Diagram

Flow for `python main.py --tailor 42`.

```mermaid
sequenceDiagram
    actor User
    participant M as main.py
    participant DB as SQLite
    participant PA as ProfileAgent
    participant TA as TailoringAgent
    participant PL as PromptLoader
    participant CC as ClaudeClient
    participant API as Anthropic API
    participant FS as File System

    User->>M: python main.py --tailor 42
    M->>DB: get_by_id(42)
    DB-->>M: Job object

    M->>User: Which track? IC, Architect, or Management
    User->>M: Architect

    M->>PA: load("resume.pdf")
    PA-->>M: Profile from cache

    M->>TA: tailor(job, profile, CareerTrack.ARCHITECT)
    TA->>PL: load("tailor_resume", profile, job, track)
    PL-->>TA: rendered system prompt

    TA->>CC: call(system, user, "resume_tailoring")
    CC->>API: messages.create(temperature=0.3)
    API-->>TA: JSON with tailored content

    TA->>TA: extract_json and parse dict
    TA->>FS: write output/resumes/Company_Title_architect.txt
    FS-->>TA: path confirmed

    TA-->>M: TailoredResume with summary, keywords, gaps, path

    M->>User: Show keywords and gaps
    M->>User: Mark as APPLIED? y/n
    User->>M: y
    M->>DB: update_job(status=APPLIED, applied_at=now)
```

---

## 8. Prompt-as-Template Pattern

How a prompt file flows from disk to the Claude API.

```mermaid
flowchart LR
    subgraph FILES["prompts/ directory"]
        F1["parse_resume.md\nvariable: resume_text"]
        F2["score_job.md\nvariables: profile, jobs,\ntracks, salary_min"]
        F3["tailor_resume.md\nvariables: profile, job, track"]
    end

    subgraph LOADER["PromptLoader.load()"]
        L1["Read .md file from disk"]
        L2["Replace placeholders\nwith runtime values"]
        L3["Check for unfilled\nplaceholders — fail fast"]
    end

    subgraph AGENTS["Agents"]
        A1["ProfileAgent"]
        A2["ScoringAgent"]
        A3["TailoringAgent"]
    end

    F1 --> L1
    F2 --> L1
    F3 --> L1
    L1 --> L2 --> L3

    L3 --> CC["ClaudeClient.call\nsystem = rendered prompt\nuser = task message\noperation = named operation"]

    A1 -->|"resume_text = pdf text"| L1
    A2 -->|"profile, tracks, salary_min\n(num_jobs in user message only)"| L1
    A3 -->|"profile, job, track"| L1
```

---

## 9. Pre-Filter Gate Pattern

Four filter stages eliminate irrelevant jobs before any Claude API call is made.
Filter lists (stages 3 & 4) are defined in `models/filters.py` and imported by both
`AdzunaScraper` (scrape-time gate) and `ScoringAgent` (score-time gate).

```mermaid
flowchart TD
    START(["N unscored jobs\nLinkedIn + Adzuna + Ladders"]) --> G1

    G1{"posted more than\n30 days ago?"}
    G1 -- Yes --> SKIP1["Skip — stale listing"]
    G1 -- No --> G2

    G2{"description is\nNone or empty?"}
    G2 -- Yes --> SKIP2["Skip — no content to score"]
    G2 -- No --> G3

    FILTERS["models/filters.py\nEXCLUDED_TITLE_KEYWORDS\nTECH_DESCRIPTION_KEYWORDS"]

    G3{"title contains\nexcluded keyword?\nproperty mgr · leasing · project mgr\nsales eng · civil eng · intern..."}
    FILTERS -->|"imported by ScoringAgent"| G3
    G3 -- Yes --> SKIP3["Skip — irrelevant role"]
    G3 -- No --> G4

    G4{"description contains\nat least one tech keyword?\nsoftware · cloud · api\nkubernetes · python · llm..."}
    FILTERS -->|"imported by ScoringAgent"| G4
    G4 -- No --> SKIP4["Skip — non-tech role"]
    G4 -- Yes --> SCORE["Send to Claude for scoring\nAPI token cost incurred here"]

    style SCORE fill:#d4edda,stroke:#28a745
    style FILTERS fill:#fce7f3,stroke:#db2777
    style SKIP1 fill:#f8d7da,stroke:#dc3545
    style SKIP2 fill:#f8d7da,stroke:#dc3545
    style SKIP3 fill:#f8d7da,stroke:#dc3545
    style SKIP4 fill:#f8d7da,stroke:#dc3545
```

---

## 10. Agentic Patterns Summary

Where each pattern appears in the codebase.

```mermaid
mindmap
  root((Job Search Agent))
    Structured Output
      ResponseParser strips fences
      ResponseParser extracts JSON
      Pydantic validates schema
      Every Claude response typed
    Prompt as Template
      prompts md files
      PromptLoader substitutes vars
      XML tags structure context
      Prompts editable without code
    Cache Aside
      ProfileAgent checks mtime
      profile.json warm cache
      Re-parse only when resume changes
      Saves API calls per run
    Parallel Batched Fan-Out
      10 jobs per Claude call
      ThreadPoolExecutor 3 workers
      Concurrent batches share cache key
      Fault isolated per future
      3x wall-clock speedup
    Pre-Filter Gate
      Stale date check
      Excluded title keywords
      Tech description keywords
      Cheap before expensive
    Pipeline State Machine
      NEW to SCORED to APPLIED
      APPLIED to REJECTED or OFFER
      DB status column queryable
      get_by_status drives workflow
    Retry with Backoff
      tenacity on ClaudeClient
      tenacity on all scrapers
      Exponential 2s 4s 8s
      3 attempts max
    Multi-Track Scoring
      One call scores IC Arch Mgmt
      Active tracks from config
      Null for disabled tracks
      3x cost reduction vs per-track
    Token Accumulation
      ClaudeClient _usage dict
      Protected by threading.Lock
      Keyed by operation name
      reset_usage at run start
      get_usage after scoring
      Persisted to runs table
    Run History and Observability
      runs table in SQLite
      One row per execution
      Actual vs estimated cost
      Per-operation token breakdown
      Phase timing and throughput
      Dashboard Run History view
    Prompt Cache Alignment
      num_jobs in user message only
      System prompt byte-identical
      All batches share cache key
      90 percent cost on cache reads
    Human-in-the-Loop Curation
      Job exclusion from dashboard
      Multi-select or per-card
      Reason recorded in DB
      Excluded filtered from all views
    Timestamp Precision
      run_started_at before scraping
      New Jobs view uses WHERE found_at >= run_at
      Capture order is invariant
    Location Normalisation
      extract_us_state in models/filters.py
      Job model_validator fills state on construct
      All scrapers get state for free
      backfill_states for existing rows
      state column queryable and filterable
    Focused Pipeline Management
      delete_below_threshold hard-deletes low scores
      Applied and offer rows always protected
      dry_run preview before destructive action
      CLI confirmation gate prevents accidents
      Threshold configurable via --threshold flag
```
