"""Pure formatting helpers for the v2 Streamlit UI.

Phase 1 of the UI refactor (docs/architecture/ui_refactor_plan.md). Extracted from
streamlit_app.py: every function here is pure - no ``st.*``, no I/O, no
session-state - so it is unit-testable and importable without a Streamlit runtime.
Render components (anything that calls ``st.*``) stay out of this module.
"""
from __future__ import annotations

import pandas as pd


def _fmt_ts(raw) -> str:
    """Format an ISO 8601 string as 'YYYY-MM-DD HH:MM:SS' for display."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "—"
    s = str(raw).replace("T", " ")
    return s[:19] if len(s) >= 19 else s


def _checked(flag) -> str:
    return "✅" if pd.notna(flag) and flag else "—"


def format_posting_age(posted_at, *, now=None) -> str:
    """Human 'Posted N days ago' label for a posting (ADR-080).

    Returns "" when the date is unknown/unparseable (so the UI shows nothing
    rather than a wrong age). Pure given an injected ``now`` -> unit-testable.
    """
    from app.services.posting_age_filter import posting_age_days
    age = posting_age_days(posted_at, now=now)
    if age is None:
        return ""
    if age <= 0:
        return "Posted today"
    if age == 1:
        return "Posted 1 day ago"
    return f"Posted {age} days ago"


def format_posting_age_short(posted_at, *, now=None) -> str:
    """Compact age label for a table cell (ADR-080): "today" / "12d" / "" (unknown)."""
    from app.services.posting_age_filter import posting_age_days
    age = posting_age_days(posted_at, now=now)
    if age is None:
        return ""
    return "today" if age <= 0 else f"{age}d"


def build_discovered_rows(normalized_jobs, scored_jobs, *, now=None) -> list[dict]:
    """Compact rows for the 'discovered jobs' table (ADR-080 surfacing).

    Every job that survived discovery (state.normalized_jobs), flagged scored vs
    not by cross-referencing state.scored_jobs status. Pure -> unit-testable.
    Posted is the leading field so freshness reads first.
    """
    status_by_id: dict = {}
    for sj in (scored_jobs or []):
        jid = sj.get("job_id") or sj.get("id")
        if jid:
            status_by_id[jid] = sj.get("status")
    rows: list[dict] = []
    for j in (normalized_jobs or []):
        jid = j.get("id") or j.get("job_id")
        status = status_by_id.get(jid)
        rows.append({
            "Posted": format_posting_age_short(j.get("posted_at"), now=now),
            "Title": j.get("title") or "(untitled)",
            "Company": j.get("company") or "—",
            "Location": j.get("location") or "—",
            "Status": "✅ scored" if status == "scored" else (status or "not scored"),
        })
    return rows


def discovery_funnel_summary(stats) -> str:
    """One-line 'filtered out' summary from discovery_stats (ADR-080 surfacing).

    Lists only the non-zero drop stages so the user sees WHERE discovered jobs went
    (incl. age + relevance filters). Empty string when nothing was dropped. Pure.
    """
    s = stats or {}
    pairs = [
        ("title", "title_filter_dropped"),
        ("experience", "experience_filter_dropped"),
        ("age", "age_filter_dropped"),
        ("relevance", "relevance_dropped"),
        ("dedup", "dedup_total_dropped"),
        ("over-cap", "max_jobs_truncated"),
    ]
    parts = [f"{label} {int(s.get(key) or 0)}" for label, key in pairs if int(s.get(key) or 0)]
    return ("Filtered out before scoring — " + ", ".join(parts)) if parts else ""


def _get_nested(d: dict, keys: list[str]):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _label_with_cost(model_id: str, entries: list[dict]) -> str:
    for e in entries:
        if e["id"] == model_id:
            return f"{model_id}  ·  ${e['input_per_m']:.2f}/M in · ${e['output_per_m']:.2f}/M out"
    return model_id


# Friendly display labels for workflow stage names and progress strings.
# Keep this in sync with the actual current_step values written by workflow nodes.
_STAGE_LABEL = {
    "initialized":             "Starting up",
    "registered":              "Starting up",
    "job_discovery":           "Finding jobs",
    "load_resume":             "Loading resume",
    "scoring":                 "Scoring jobs",
    "score_jobs":              "Scoring jobs",
    "deep_review_in_progress": "Deep review",
    "review_completed":        "Computing advice",
    "no_qualifying_jobs":      "No matches above threshold",
    "career_advice":           "Generating career advice",
    "interview_prep":          "Generating interview prep",
    "tailoring":               "Tailoring resume",
    "completed":               "Done",
    "completed_with_errors":   "Done (with errors)",
    "failed":                  "Failed",
}


def _friendly_stage(current_step: str | None) -> str:
    if not current_step:
        return "—"
    return _STAGE_LABEL.get(current_step, str(current_step).replace("_", " ").title())


def _safe_int(value, default: int = 0) -> int:
    """Coerce DataFrame-origin values (None, NaN, '', numeric strings) to int."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _word_count(text: str | None) -> int:
    return len((text or "").split())


def _stage_progress(row: dict) -> str:
    """Build a 'where exactly is this run' string from a workflow_runs row.

    Examples:
      "5 / 10 scored"            during scoring
      "review 1 / 2 selected"    during deep review
      "8 jobs · 3 reviewed"      after completion
      ""                         when nothing meaningful to show
    """
    step = row.get("current_step") or ""
    status = row.get("status") or ""
    scored = _safe_int(row.get("jobs_scored"))
    max_jobs = _safe_int(row.get("max_jobs")) or None
    selected = _safe_int(row.get("selected_count"))
    rounds = _safe_int(row.get("review_rounds_count"))
    normalized = _safe_int(row.get("normalized_count"))

    if status in ("completed", "completed_with_errors"):
        bits = []
        if scored:
            bits.append(f"{scored} scored")
        if selected:
            bits.append(f"{selected} reviewed")
        return " · ".join(bits) or "—"
    if status == "failed":
        return "halted"

    # Running: derive progress from the current step
    if step in ("job_discovery", "registered", "initialized"):
        return f"{normalized} found" if normalized else "discovering…"
    if step in ("load_resume",):
        return "parsing resume…"
    if step in ("scoring", "score_jobs"):
        if max_jobs:
            return f"{scored} / {max_jobs} scored"
        if normalized:
            return f"{scored} / {normalized} scored"
        return f"{scored} scored"
    if step == "deep_review_in_progress":
        if selected:
            return f"review {min(rounds, selected)} / {selected} jobs"
        return f"{rounds} review rounds"
    if step in ("career_advice", "interview_prep", "tailoring", "review_completed"):
        return f"{selected} job(s) advanced"
    return ""


# ── Track-impact heuristic (per-track directional lift, no LLM call) ─────────
# Curated keyword buckets for the three career tracks the ScoringAgent grades.
# These are intentionally narrow — generic verbs like "delivered" only land in a
# track if their context is unambiguous. The heuristic counts how many tokens
# appear in suggested_text but NOT in original_text per track, then maps the
# count to a directional signal. This is deliberately structural, not predictive:
# we are answering "which tracks are these suggestions moving toward" — NOT
# "what number will the ScoringAgent return after the rewrite". Re-scoring with
# the rubric-trained agent would create a self-fulfilling prophecy (ADR-056
# addendum #3).
_TRACK_KEYWORDS: dict[str, frozenset[str]] = {
    "technical": frozenset({
        "kubernetes", "k8s", "docker", "aws", "gcp", "azure", "terraform",
        "ansible", "helm", "argocd",
        "python", "go", "golang", "rust", "java", "typescript", "javascript",
        "node", "react", "vue", "angular", "fastapi", "django", "flask", "spring",
        "postgres", "postgresql", "mysql", "redis", "kafka", "rabbitmq",
        "elasticsearch", "mongodb", "dynamodb", "snowflake", "bigquery",
        "graphql", "grpc", "rest", "openapi", "websocket",
        "jenkins", "circleci", "github-actions",
        "prometheus", "grafana", "datadog", "opentelemetry", "otel",
        "linux", "bash", "git",
    }),
    "architecture": frozenset({
        "architected", "designed", "scaled", "throughput", "latency", "sla",
        "slo", "p99", "p95", "uptime",
        "ha", "high-availability", "multi-region", "multi-az", "redundancy",
        "fault-tolerant", "load-balancing", "sharding", "replication",
        "consistency", "idempotent",
        "microservices", "monolith", "event-driven", "saga", "cqrs",
        "domain-driven", "ddd", "service-mesh", "api-gateway",
        "queue", "stream", "pipeline", "etl", "elt",
        "platform", "infrastructure", "system",
    }),
    "leadership": frozenset({
        "led", "managed", "mentored", "coached", "hired", "interviewed",
        "promoted", "team", "cross-functional", "stakeholder", "roadmap",
        "vision", "strategy", "owned", "ownership", "accountable",
        "delivered", "shipped", "report", "reports", "manager", "lead",
        "principal", "people", "headcount", "org", "organization",
    }),
}


def _tokenize(text: str | None) -> set[str]:
    """Lowercase, strip punctuation except hyphens (so 'multi-region' survives)."""
    if not text:
        return set()
    out: set[str] = set()
    for raw in text.lower().split():
        # keep alphanumerics + hyphens + slashes; drop everything else
        cleaned = "".join(c for c in raw if c.isalnum() or c in "-/")
        if cleaned:
            out.add(cleaned)
    return out


def _estimate_track_impact(draft: dict) -> dict:
    """Pure structural derivation of which career tracks the draft is moving toward.

    For each reword/emphasize bullet across headline + summary + experience:
      - tokenize original_text and suggested_text
      - for each track, count tokens added (in suggested but not original)
        that fall in the track's keyword set
    For each skills_section_suggestions string, count it as a +1 token
    in whichever track keyword set contains it (mostly technical).

    Returns:
      {
        "technical":    {"signal": "...", "added": [...], "n_bullets": int},
        "architecture": {...},
        "leadership":   {...},
        "freed_bullets":  int,   # remove suggestions
        "open_gaps":      int,   # gap suggestions
      }
    signal is one of: "neutral" | "small_lift" | "likely_lift".
    """
    track_added: dict[str, list[str]] = {"technical": [], "architecture": [], "leadership": []}
    track_bullet_count: dict[str, set[int]] = {"technical": set(), "architecture": set(), "leadership": set()}
    freed = 0
    gaps = 0

    bullets: list[dict] = []
    for key in ("headline_suggestions", "summary_suggestions", "experience_bullet_suggestions"):
        for b in draft.get(key) or []:
            if isinstance(b, dict):
                bullets.append(b)

    for idx, b in enumerate(bullets):
        claim = b.get("claim_type") or "reword"
        if claim == "remove":
            freed += 1
            continue
        if claim == "gap":
            gaps += 1
            continue
        orig = _tokenize(b.get("original_text"))
        sug = _tokenize(b.get("suggested_text"))
        new_tokens = sug - orig
        for track, kws in _TRACK_KEYWORDS.items():
            hits = new_tokens & kws
            if hits:
                track_added[track].extend(sorted(hits))
                track_bullet_count[track].add(idx)

    # Skills additions: each appended skill is a +1 token; classify by membership.
    for s in draft.get("skills_section_suggestions") or []:
        if not isinstance(s, str):
            continue
        toks = _tokenize(s)
        for track, kws in _TRACK_KEYWORDS.items():
            hits = toks & kws
            if hits:
                track_added[track].extend(sorted(hits))

    def _signal(added: list[str], n_bullets: int) -> str:
        n = len(added)
        if n == 0:
            return "neutral"
        if n <= 2 and n_bullets <= 1:
            return "small_lift"
        return "likely_lift"

    return {
        "technical":    {"signal": _signal(track_added["technical"], len(track_bullet_count["technical"])),
                         "added": track_added["technical"],
                         "n_bullets": len(track_bullet_count["technical"])},
        "architecture": {"signal": _signal(track_added["architecture"], len(track_bullet_count["architecture"])),
                         "added": track_added["architecture"],
                         "n_bullets": len(track_bullet_count["architecture"])},
        "leadership":   {"signal": _signal(track_added["leadership"], len(track_bullet_count["leadership"])),
                         "added": track_added["leadership"],
                         "n_bullets": len(track_bullet_count["leadership"])},
        "freed_bullets": freed,
        "open_gaps":     gaps,
    }


def _section_display(label: str, resume_profile: dict | None) -> str:
    """Turn a raw section_label into a human-readable header.

    "headline"                         -> "Headline (positioning tagline)"
    "summary"                          -> "Summary"
    "experience:Acme:Staff Engineer"   -> "Experience — Staff Engineer @ Acme"
    "skills"                           -> "Skills"
    "education:MIT"                    -> "Education — MIT"
    Anything else                      -> the raw label.
    """
    if not label:
        return "Other suggestions"
    if label == "headline":
        return "Headline (positioning tagline)"
    if label == "summary":
        return "Summary"
    if label == "skills":
        return "Skills"
    if label.startswith("experience:"):
        parts = label.split(":", 2)
        if len(parts) == 3:
            return f"Experience — {parts[2]} @ {parts[1]}"
        return label
    if label.startswith("education:"):
        return f"Education — {label.split(':', 1)[1]}"
    if label.startswith("certifications:"):
        return f"Certifications — {label.split(':', 1)[1]}"
    return label


def _section_order(resume_profile: dict | None) -> list[str]:
    """Build the display-order list of section_labels matching resume order:
    headline -> summary -> experience entries (in resume order) -> skills ->
    education -> certifications. Unknown labels render after these in
    encounter order.
    """
    rp = resume_profile or {}
    order: list[str] = ["headline", "summary"]
    for exp in (rp.get("experience") or []):
        co = (exp.get("company") or "").strip()
        ti = (exp.get("title") or "").strip()
        if co and ti:
            order.append(f"experience:{co}:{ti}")
    order.append("skills")
    for edu in (rp.get("education") or []):
        inst = (edu.get("institution") or "").strip()
        if inst:
            order.append(f"education:{inst}")
    for cert in (rp.get("certifications") or []):
        nm = (cert.get("name") or "").strip()
        if nm:
            order.append(f"certifications:{nm}")
    return order
