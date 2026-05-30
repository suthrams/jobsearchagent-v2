"""v2 Streamlit UI.

Layout (sidebar order is intentional — top of the page first):

  * Workflow History (default landing) — list of all runs, click to drill in
  * Workflow Detail                    — per-run unified view: jobs, scores,
                                         deep review, advice, prep, settings
                                         used for that run, constraints hit
  * Start New Run                      — settings inline + custom URLs textarea
  * Live Run Monitor                   — activity feed for the currently running run
  * Run Report                         — generated markdown report
  * Settings                           — view + edit user-overridable config
  * (Cross-run analytics — Top Matches, IC/Architect/Mgmt Track, Companies)

Browse views read data/v2.db directly via db_reader.py.
Control actions (start workflow, edit config) call FastAPI via api_client.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import app.*` works when launched
# via `streamlit run app/ui/streamlit_app.py` (Streamlit puts the script's
# directory on sys.path[0], not the project root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import yaml
import app.ui.api_client as api
from app.services.constraint_analyzer import analyze, summary_metrics
from app.services.cost_breakdown import (
    all_runs_by_cost,
    compute_breakdown,
    compute_dashboard_aggregate,
    daily_spend_trend,
    top_calls_by_cost,
    top_runs_by_cost,
)
from app.workflows.limits import MAX_LLM_CALLS_PER_RUN
from app.ui.db_reader import (
    load_agent_events,
    load_deep_review_results,
    load_interview_prep,
    load_job_pipeline,
    load_llm_calls,
    load_persisted_workflow_runs,
    load_recent_workflows,
    load_scored_jobs,
    load_step_executions,
    load_user_clinic_reviews,
    load_user_resumes,
    load_workflow_jobs,
    load_workflow_run,
    load_workflow_runs,
)


@st.cache_data
def _load_yaml_config() -> dict:
    try:
        with open("config/config.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


# ── Cached HTTP wrappers ──────────────────────────────────────────────────────
# Streamlit reruns the whole script on every interaction. Without caching, these
# endpoints would fire on every keystroke / sidebar click. TTL keeps them fresh
# enough to feel live; .clear() is called after any write that would invalidate.

@st.cache_data(ttl=10)
def _cached_list_tailorings(workflow_id: str) -> list[dict]:
    return api.list_tailorings(workflow_id).get("tailorings") or []


@st.cache_data(ttl=60)
def _cached_get_providers() -> dict | None:
    try:
        return api.get_providers()
    except Exception:
        return None


@st.cache_data(ttl=30)
def _cached_list_users() -> list[dict]:
    """Profiles for the sidebar selector (ADR-062). Default user 0 first."""
    try:
        return api.list_users().get("users") or []
    except Exception:
        return []


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Search Agent v2",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ─────────────────────────────────────────────────────────────

for _key, _default in (
    ("workflow_id", None),
    ("last_status", None),
    ("last_response", None),
    ("detail_workflow_id", None),
    ("detail_job_id", None),
    ("config_cache", None),
    ("sidebar_view", "Workflow History"),
    ("current_user_id", "0"),  # ADR-062: active profile; default = pre-existing data
    ("onboard_step", 1),       # onboarding wizard cursor
    ("onboard_new_user_id", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ADR-062: mirror the active profile onto the API client before any request fires
# this rerun. The sidebar selector may change it below, after which we re-set it.
api.set_user_id(st.session_state.current_user_id)


def _navigate(view_name: str, **state_updates) -> None:
    """Programmatic sidebar navigation.

    Cannot write directly to sidebar_view after the radio widget is instantiated.
    Store the destination in _pending_nav instead; the pre-sidebar block picks it
    up on the next render cycle before the radio widget is created.
    """
    for k, v in state_updates.items():
        st.session_state[k] = v
    # When navigation explicitly changes the detail target, force the Detail
    # view's text_input to re-sync. Without this, a user-typed value in that
    # input would survive the next history row click and override the new target.
    if "detail_workflow_id" in state_updates:
        st.session_state.pop("_detail_wf_synced", None)
    st.session_state._pending_nav = view_name
    st.rerun()


def _fmt_ts(raw) -> str:
    """Format an ISO 8601 string as 'YYYY-MM-DD HH:MM:SS' for display."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "—"
    s = str(raw).replace("T", " ")
    return s[:19] if len(s) >= 19 else s


# Auto-reconnect to the most recent workflow on first load.
# Failures are stored on session_state so the sidebar can surface them as a caption
# instead of the user wondering why "Active Run" is empty.
if "workflow_reconnect_attempted" not in st.session_state:
    st.session_state.workflow_reconnect_attempted = True
    st.session_state.workflow_reconnect_error = None
    if st.session_state.workflow_id is None:
        try:
            _recent = load_recent_workflows()
            if not _recent.empty and "workflow_id" in _recent.columns:
                _reconnect_id = _recent.iloc[0]["workflow_id"]
                st.session_state.workflow_id = _reconnect_id
                try:
                    _reconnect_resp = api.get_workflow_status(_reconnect_id)
                    st.session_state.last_status = _reconnect_resp.get("status")
                    st.session_state.last_response = _reconnect_resp
                except Exception as exc:
                    st.session_state.workflow_reconnect_error = (
                        f"Could not fetch status for the most-recent run: {exc}"
                    )
        except Exception as exc:
            st.session_state.workflow_reconnect_error = (
                f"Could not load recent workflows from the database: {exc}"
            )


# ── Shared helpers ────────────────────────────────────────────────────────────

def score_badge(score: int | None) -> str:
    if score is None:
        return "—"
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    if score >= 50:
        return f"🟠 {score}"
    return f"🔴 {score}"


def _checked(flag) -> str:
    return "✅" if pd.notna(flag) and flag else "—"


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


def _bullets(label: str, items, *, sub: bool = False) -> None:
    """Render a labelled bullet list. No-op when items is empty / not a list.

    sub=True uses italic caption-style label for nested groups.
    """
    if not items or not isinstance(items, list):
        return
    if sub:
        st.markdown(f"_{label}_")
    else:
        st.markdown(f"**{label}**")
    for item in items:
        if isinstance(item, dict):
            # Best-effort flatten — render dicts as "key: value" bullets
            inner = "  ·  ".join(f"_{k}_: {v}" for k, v in item.items())
            st.markdown(f"- {inner}")
        else:
            st.markdown(f"- {item}")
    st.markdown("")  # blank line for breathing room


def _para(label: str, value) -> None:
    """Render a labelled paragraph. No-op when value is empty."""
    if not value:
        return
    st.markdown(f"**{label}**")
    st.write(value)
    st.markdown("")


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


def _get_config_cached() -> dict:
    """Pull config once per render and stash on session_state to avoid extra HTTP calls."""
    if st.session_state.config_cache is None:
        try:
            st.session_state.config_cache = api.get_config()
        except Exception as exc:
            st.session_state.config_cache = {"effective_config": _load_yaml_config(),
                                             "protected_keys": [],
                                             "_offline_reason": str(exc)}
    return st.session_state.config_cache


_CLAIM_BADGE = {
    "reword":    "🟦 reword",
    "emphasize": "🟩 emphasize",
    "gap":       "🟧 gap (need to add)",
    "remove":    "🟥 remove (frees space)",
}
_FIDELITY_RISK_BADGE = {"low": "🟢 low risk", "medium": "🟡 medium risk", "high": "🔴 high risk"}
_FIDELITY_STATUS_BADGE = {"pass": "🟢 fidelity pass", "needs_revision": "🟡 needs revision",
                          "fail": "🔴 fidelity fail"}
_DECISION_BADGE = {"approve": "🟢 approved", "revise": "🟡 needs revision", "reject": "🔴 rejected", "edit": "✍️ edited & accepted"}


def _word_count(text: str | None) -> int:
    return len((text or "").split())


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


_TRACK_SIGNAL_BADGE = {
    "neutral":     ("⚪", "neutral"),
    "small_lift":  ("🟡", "small lift"),
    "likely_lift": ("🟢", "likely lift"),
}


def _render_estimated_impact(draft: dict) -> None:
    """Render the per-track directional lift derived from the suggestion structure."""
    impact = _estimate_track_impact(draft)
    rows: list[str] = []
    for track in ("technical", "architecture", "leadership"):
        info = impact[track]
        icon, label = _TRACK_SIGNAL_BADGE[info["signal"]]
        track_name = track.capitalize()
        if info["signal"] == "neutral":
            rows.append(f"- {icon} **{track_name}**: {label} — no track-keyword additions in this draft.")
        else:
            # Show up to 4 example tokens added; dedupe preserving order.
            seen: set[str] = set()
            uniq: list[str] = []
            for t in info["added"]:
                if t not in seen:
                    seen.add(t)
                    uniq.append(t)
            ex = ", ".join(f"`{t}`" for t in uniq[:4])
            extra = f" +{len(uniq) - 4} more" if len(uniq) > 4 else ""
            n = info["n_bullets"]
            bullet_clause = f" across {n} bullet{'s' if n != 1 else ''}" if n else ""
            rows.append(f"- {icon} **{track_name}**: {label} — added {ex}{extra}{bullet_clause}.")

    footer_bits: list[str] = []
    if impact["freed_bullets"]:
        footer_bits.append(f"🗑 freed {impact['freed_bullets']} bullet(s) of space")
    if impact["open_gaps"]:
        footer_bits.append(f"⚠ {impact['open_gaps']} gap(s) remain unclosed")

    body = "**Estimated impact (directional, not a re-score)**\n\n" + "\n".join(rows)
    if footer_bits:
        body += "\n\n_" + "  ·  ".join(footer_bits) + "_"
    st.markdown(body)
    st.caption(
        "Heuristic: counts JD-relevant keywords the suggestion adds vs the original. "
        "It tells you which tracks the draft is moving toward, not what score the agent would assign."
    )


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


def _render_one_bullet(b: dict, idx: int) -> None:
    claim = b.get("claim_type") or "reword"
    risk = b.get("fidelity_risk") or "low"
    st.markdown(
        f"_Suggestion {idx + 1}_  ·  "
        f"{_CLAIM_BADGE.get(claim, claim)}  ·  "
        f"{_FIDELITY_RISK_BADGE.get(risk, risk)}"
    )
    c1, c2 = st.columns(2)
    c1.markdown("_Original_")
    c1.markdown(f"> {b.get('original_text') or '_(none — net-new line)_'}")
    c2.markdown("_Suggested_")
    if claim == "remove":
        c2.markdown("> _(remove this bullet to free space)_")
    elif claim == "gap":
        c2.markdown("> _(gap — candidate decides whether to add)_")
    else:
        c2.markdown(f"> {b.get('suggested_text') or '—'}")
    # Length-budget hint so the candidate sees the word delta inline.
    o_w = _word_count(b.get("original_text"))
    s_w = _word_count(b.get("suggested_text"))
    if claim in ("reword", "emphasize") and o_w:
        st.caption(f"Length: {o_w}w → {s_w}w  ({s_w - o_w:+d}w)")
    ev = b.get("supporting_evidence") or ""
    if ev:
        st.caption(f"📎 Evidence from your resume: _{ev}_")
    rationale = (b.get("impact_rationale") or "").strip()
    if rationale:
        st.caption(f"💡 Why for this role: {rationale}")
    unsupported = b.get("unsupported_claims") or []
    if unsupported:
        for u in unsupported:
            st.warning(f"Unsupported claim: {u}")
    st.markdown("")


def _render_tailored_sections(draft: dict, resume_profile: dict | None) -> None:
    """Group all bullet-style suggestions by section_label and render in resume order.

    Falls back gracefully for older drafts that have no section_label: those are
    bucketed as "summary" (if pulled from summary_suggestions) or "experience:other"
    (if from experience_bullet_suggestions) so existing drafts stay readable.
    """
    headline = list(draft.get("headline_suggestions") or [])
    summary = list(draft.get("summary_suggestions") or [])
    experience = list(draft.get("experience_bullet_suggestions") or [])

    # Tag each bullet with a fallback section_label so older drafts still group.
    tagged: list[dict] = []
    for b in headline:
        if isinstance(b, dict):
            b2 = dict(b)
            b2.setdefault("section_label", "headline")
            tagged.append(b2)
    for b in summary:
        if isinstance(b, dict):
            b2 = dict(b)
            b2.setdefault("section_label", "summary")
            tagged.append(b2)
    for b in experience:
        if isinstance(b, dict):
            b2 = dict(b)
            b2.setdefault("section_label", "experience:other")
            tagged.append(b2)

    if not tagged:
        return

    # Group
    by_section: dict[str, list[dict]] = {}
    for b in tagged:
        by_section.setdefault(b.get("section_label") or "", []).append(b)

    # Order: known sections first, then any unknown labels
    known = _section_order(resume_profile)
    seen: set[str] = set()
    ordered_labels: list[str] = []
    for k in known:
        if k in by_section and k not in seen:
            ordered_labels.append(k)
            seen.add(k)
    for k in by_section:
        if k not in seen:
            ordered_labels.append(k)
            seen.add(k)

    for label in ordered_labels:
        bullets = by_section[label]
        # Section-level word delta so the candidate sees the page-budget impact.
        delta_o = sum(_word_count(b.get("original_text")) for b in bullets
                      if (b.get("claim_type") or "reword") in ("reword", "emphasize"))
        delta_s = sum(_word_count(b.get("suggested_text")) for b in bullets
                      if (b.get("claim_type") or "reword") in ("reword", "emphasize"))
        n_remove = sum(1 for b in bullets if b.get("claim_type") == "remove")
        n_gap = sum(1 for b in bullets if b.get("claim_type") == "gap")
        meta_bits = [f"{len(bullets)} suggestion(s)"]
        if delta_o or delta_s:
            meta_bits.append(f"{delta_o}w → {delta_s}w ({delta_s - delta_o:+d}w)")
        if n_remove:
            meta_bits.append(f"{n_remove} remove")
        if n_gap:
            meta_bits.append(f"{n_gap} gap")
        st.markdown(f"### {_section_display(label, resume_profile)}")
        st.caption("  ·  ".join(meta_bits))
        for i, b in enumerate(bullets):
            _render_one_bullet(b, i)


def _render_tailoring_card(t: dict, on_decision, resume_profile: dict | None = None) -> None:
    """Render one tailoring draft. on_decision(tailoring_id, choice) is invoked when
    one of the decision buttons is clicked. resume_profile, when provided, is used
    to order suggestion groups by the candidate's actual resume sections."""
    tid = t.get("tailoring_id") or t.get("id") or ""
    draft = t.get("tailored") or {}
    fidelity = t.get("fidelity_review") or {}
    decision = t.get("decision")

    # Header row: status badges
    if decision:
        st.markdown(f"### {_DECISION_BADGE.get(decision, decision)}  ·  `{tid[:8]}…`")
    else:
        f_status = (fidelity or {}).get("overall_fidelity_status", "unknown")
        rec = (fidelity or {}).get("approval_recommendation")
        head = _FIDELITY_STATUS_BADGE.get(f_status, f"fidelity: {f_status}")
        if rec:
            head += f"  ·  recommended: **{rec}**"
        st.markdown(f"### {head}  ·  `{tid[:8]}…`")
    st.caption(f"Created `{_fmt_ts(t.get('created_at'))}`"
               + (f"  ·  Decided `{_fmt_ts(t.get('decided_at'))}`" if t.get("decided_at") else ""))

    # Strategy summary — render at the top so the candidate sees the throughline
    # before scrolling through individual bullet diffs.
    strategy = (draft.get("overall_tailoring_notes") or "").strip()
    if strategy:
        st.info(f"**Strategy for this draft**\n\n{strategy}")

    # Estimated per-track impact — directional, derived from the suggestion
    # structure. Not a re-score (see ADR-056 addendum #3 for why).
    _render_estimated_impact(draft)

    # Fidelity flags
    flag_lines = []
    for fk, flabel in (
        ("unsupported_claims", "Unsupported claims"),
        ("fabricated_metrics", "Fabricated metrics"),
        ("inflated_scope_flags", "Inflated scope"),
        ("unsupported_technology_flags", "Unsupported tech"),
        ("unsupported_certification_flags", "Unsupported certifications"),
        ("required_removals", "Must remove"),
        ("required_revisions", "Must revise"),
    ):
        items = (fidelity or {}).get(fk) or []
        if items:
            flag_lines.append((flabel, items))
    if flag_lines:
        with st.expander("Fidelity flags", expanded=False):
            for label, items in flag_lines:
                st.markdown(f"**{label}**")
                for x in items:
                    st.markdown(f"- {x}")

    # Per-section diffs, grouped by section_label in resume order so the
    # candidate sees changes the way they appear on the page.
    _render_tailored_sections(draft, resume_profile)
    skills = draft.get("skills_section_suggestions") or []
    if skills:
        st.markdown("### Skills additions")
        st.caption(f"{len(skills)} bare skill label(s) to add to your existing Skills section")
        for s in skills:
            st.markdown(f"- {s}")
    if draft.get("fidelity_risk_summary"):
        st.caption(f"Risk summary: {draft['fidelity_risk_summary']}")

    # Decision buttons
    if not decision and tid:
        st.markdown("---")

        # Before you decide: a consequence summary at the decision point. The
        # HITL literature names "too little context to the reviewer" as the
        # common failure mode, so surface the reviewer's recommendation, the
        # unresolved risk, and what each choice actually does -- right next to
        # the buttons, not scattered up the card.
        _rec = (fidelity or {}).get("approval_recommendation")
        _conf = (fidelity or {}).get("confidence")
        _flag_total = sum(
            len((fidelity or {}).get(fk) or [])
            for fk in (
                "unsupported_claims", "fabricated_metrics", "inflated_scope_flags",
                "unsupported_technology_flags", "unsupported_certification_flags",
                "required_removals", "required_revisions",
            )
        )
        _n_suggestions = sum(
            len(v) for v in draft.values()
            if isinstance(v, list) and v and all(isinstance(b, dict) for b in v)
        )
        _rec_line = f"Reviewer recommends **{_rec}**" if _rec else "Reviewer made no recommendation"
        if _conf is not None:
            try:
                _rec_line += f" (confidence {int(_conf)}%)"
            except (TypeError, ValueError):
                pass
        if _flag_total:
            _rec_line += f"  ·  **{_flag_total}** unresolved fidelity flag(s)"
        st.markdown(f"**Before you decide** — {_rec_line}")
        st.markdown(
            f"- **Approve**: accept all {_n_suggestions} suggestion(s) as-is"
            + (f", including the {_flag_total} the reviewer flagged" if _flag_total else "")
            + ".\n"
            "- **Edit**: rewrite and accept your own wording (you author it; not re-checked).\n"
            "- **Request revision**: ask for another tailoring pass.\n"
            "- **Reject**: discard this draft; your resume is unchanged."
        )

        b1, b2, b3 = st.columns(3)
        if b1.button("✅ Approve", key=f"tail_app_{tid}"):
            on_decision(tid, "approve")
        if b2.button("✏️ Request revision", key=f"tail_rev_{tid}"):
            on_decision(tid, "revise")
        if b3.button("🚫 Reject", key=f"tail_rej_{tid}"):
            on_decision(tid, "reject")

        # Edit and accept as final: the human rewrites the suggested wording and
        # owns it. Saved as authored by the user (decision="edit"), not re-run
        # through the Fidelity Reviewer -- the reviewer polices the agent, not
        # the accountable human (ADR-059).
        with st.expander("Edit and accept as final (your wording, not the agent's)", expanded=False):
            import copy
            st.caption(
                "Edit any suggested line below and save. The saved text is recorded "
                "as authored by you and is not re-checked by the Fidelity Reviewer -- "
                "you own these words."
            )
            edited_draft = copy.deepcopy(draft)
            has_editable = False
            for field, value in edited_draft.items():
                if isinstance(value, list) and value and all(isinstance(b, dict) for b in value):
                    bullets = [b for b in value if "suggested_text" in b]
                    if not bullets:
                        continue
                    has_editable = True
                    st.markdown(f"**{field.replace('_', ' ').title()}**")
                    for i, b in enumerate(bullets):
                        b["suggested_text"] = st.text_area(
                            b.get("original_text") or f"{field} #{i + 1}",
                            value=b.get("suggested_text") or "",
                            key=f"edit_{tid}_{field}_{i}",
                        )
            if has_editable and st.button("Save edited draft (accept as final)", key=f"tail_edit_{tid}"):
                edited_draft["human_edited"] = True
                on_decision(tid, "edit", edited_draft)
            elif not has_editable:
                st.caption("This draft has no editable suggestions to revise.")


def render_track_table(df: pd.DataFrame, score_col: str, min_score: int) -> None:
    filtered = df[df[score_col] >= min_score].sort_values(score_col, ascending=False).copy()
    display = filtered[
        ["job_id", "title", "company", "location", score_col,
         "match_summary", "recommended_next_action", "url"]
    ].rename(columns={
        score_col: "Score",
        "match_summary": "Summary",
        "recommended_next_action": "Agent Recommendation",
        "url": "URL",
    }).reset_index(drop=True)

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "job_id":      st.column_config.TextColumn("Job ID",     width="small"),
            "title":       st.column_config.TextColumn("Title",      width="large"),
            "company":     st.column_config.TextColumn("Company",    width="medium"),
            "location":    st.column_config.TextColumn("Location",   width="medium"),
            "Score":       st.column_config.ProgressColumn("Score",  min_value=0, max_value=100, format="%d"),
            "Summary":              st.column_config.TextColumn("Summary",             width="large"),
            "Agent Recommendation": st.column_config.TextColumn("Agent Recommendation", width="large"),
            "URL":         st.column_config.LinkColumn("Link",       width="small"),
        },
    )
    st.caption(f"{len(filtered)} jobs with score >= {min_score}")


# ── Flush pending navigation before the radio widget is created ───────────────
# _navigate() cannot write sidebar_view after the widget is instantiated, so it
# stores the destination in _pending_nav and we apply it here on the next cycle.
if st.session_state.get("_pending_nav"):
    st.session_state.sidebar_view = st.session_state.pop("_pending_nav")

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Job Search Agent v2")

    # ── Profile selector (ADR-062) ───────────────────────────────────────────
    # Picks whose search this is. Re-scopes every history / analytics read and
    # the resume picker, and tags new runs with this owner. No auth — this is a
    # cooperative selector, not an access boundary (ADR-062 Decision E).
    _users = _cached_list_users()
    if _users:
        _id_to_label = {str(u["id"]): f"{u['name']}  (#{u['id']})" for u in _users}
        _ids = list(_id_to_label.keys())
        _cur = st.session_state.current_user_id
        if _cur not in _ids:
            _cur = _ids[0]
        _chosen = st.selectbox(
            "Profile",
            _ids,
            index=_ids.index(_cur),
            format_func=lambda i: _id_to_label.get(i, i),
            key="_profile_select",
            help="Whose search this is. Switching re-scopes history, analytics, "
                 "and the resume picker to that profile.",
        )
        if _chosen != st.session_state.current_user_id:
            st.session_state.current_user_id = _chosen
            api.set_user_id(_chosen)
            st.cache_data.clear()
            st.session_state.config_cache = None
            st.rerun()
        _note = next((u.get("note") for u in _users if str(u["id"]) == _chosen), None)
        if _note:
            st.caption(_note)
    else:
        st.caption("No profiles found (backend offline?).")
    if st.button("＋ Add profile", use_container_width=True):
        st.session_state.onboard_step = 1
        st.session_state.onboard_new_user_id = None
        _navigate("Profiles")

    st.markdown("---")
    view = st.radio(
        "View",
        [
            "Workflow History",
            "Workflow Detail",
            "Job Detail",
            "Start New Run",
            "Live Run Monitor",
            "Run Report",
            "Resume Clinic",
            "Settings",
            "Profiles",
            "─── Cross-Run Analytics ───",
            "Cost Dashboard",
            "Top Matches",
            "IC Track",
            "Architect Track",
            "Management Track",
            "Companies",
        ],
        key="sidebar_view",
    )
    st.markdown("---")
    min_score = st.slider(
        "Minimum match score",
        min_value=0, max_value=100, value=75, step=5,
        help="Jobs with any track score (technical / architecture / leadership) at or above "
             "this value qualify for deep review and interview prep.",
    )
    st.markdown("---")
    search = st.text_input("Search title / company", placeholder="e.g. Staff Engineer")
    include_excluded = st.checkbox(
        "Include excluded jobs",
        value=False,
        help="ADR-057: jobs you've explicitly excluded are hidden from cross-run "
             "analytics by default. Tick to surface them.",
    )
    st.markdown("---")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.session_state.config_cache = None
        st.rerun()
    if st.session_state.get("workflow_reconnect_error"):
        st.caption(f"⚠ {st.session_state.workflow_reconnect_error}")
    if st.session_state.workflow_id:
        _wst = st.session_state.last_status or "unknown"
        _wicon = {
            "running": "🔵", "waiting_for_user": "🟡",
            "completed": "🟢", "failed": "🔴",
        }.get(_wst, "⚪")
        st.markdown("---")
        st.markdown(f"**Active Run** {_wicon} `{_wst}`")
        st.caption(f"`{st.session_state.workflow_id[:12]}…`")
        _wresp = st.session_state.last_response or {}
        _wstep = _wresp.get("current_step")
        if _wstep:
            st.caption(f"Step: `{_wstep}`")
        _wm = _wresp.get("run_metrics") or {}
        if _wm.get("llm_calls"):
            st.caption(f"{_wm['llm_calls']} calls · ${_wm.get('estimated_cost_usd', 0):.4f}")
        _b1, _b2 = st.columns(2)
        if _b1.button("Detail", key="sb_open_detail", use_container_width=True):
            _navigate("Workflow Detail", detail_workflow_id=st.session_state.workflow_id)
        if _b2.button("Live", key="sb_open_live", use_container_width=True):
            _navigate("Live Run Monitor")

if view.startswith("───"):
    st.info("Select a view from the sidebar.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW HISTORY (default landing)
# ══════════════════════════════════════════════════════════════════════════════

if view == "Workflow History":
    st.header("Workflow History")
    st.caption("All workflow runs, newest first. **Select a row, then click Open detail** "
               "(or just click a different row to switch).")

    _uid = st.session_state.current_user_id
    df = load_persisted_workflow_runs(user_id=_uid)

    # Fall back to the derived view (job_scores aggregation) if workflow_runs is still empty
    # — this keeps old runs visible while new ones populate the table.
    using_legacy = False
    if df.empty:
        df_legacy = load_workflow_runs(user_id=_uid)
        if df_legacy.empty:
            st.info("No workflow runs yet. Start one from **Start New Run**.")
            st.stop()
        df = df_legacy.rename(columns={"id": "workflow_id"})
        df["status"] = df.get("status", "completed")
        df["current_step"] = df.get("current_step", "—")
        df["completed_at"] = df.get("completed_at", df.get("updated_at"))
        df["error_message"] = df.get("error_message", None)
        # Fill in the columns the new query exposes so the table renders consistently
        for _col in ("max_jobs", "normalized_count", "selected_count",
                     "review_rounds_count", "cost_usd", "llm_calls",
                     "threshold", "custom_url_count", "roles_json", "locations_json"):
            if _col not in df.columns:
                df[_col] = None
        using_legacy = True

    # Filter input
    fcol1, fcol2 = st.columns([3, 1])
    q = fcol1.text_input(
        "Filter by role / location / workflow ID prefix",
        value="", placeholder="e.g. Staff Engineer  |  Atlanta  |  3fa85f",
    )
    only_with_jobs = fcol2.checkbox("Only runs with scored jobs", value=False)

    if q.strip():
        ql = q.strip().lower()
        def _match(row):
            blob = " ".join([
                str(row.get("workflow_id", "")),
                str(row.get("roles_json", "") or ""),
                str(row.get("locations_json", "") or ""),
            ]).lower()
            return ql in blob
        df = df[df.apply(_match, axis=1)]
    if only_with_jobs and "jobs_scored" in df.columns:
        df = df[df["jobs_scored"].fillna(0) > 0]

    df = df.reset_index(drop=True)

    # Top metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Runs shown", len(df))
    m2.metric("Total Jobs Scored", int(df["jobs_scored"].sum()) if "jobs_scored" in df.columns else 0)
    _completed = (df["status"] == "completed").sum() if "status" in df.columns else 0
    m3.metric("Completed", int(_completed))
    _failed = df["status"].isin(["failed", "completed_with_errors"]).sum() if "status" in df.columns else 0
    m4.metric("Failed / Errors", int(_failed))

    if using_legacy:
        st.caption("⚠ Showing legacy aggregation — these runs predate the workflow_runs snapshot. "
                   "Stage / progress / threshold won't be visible until you run a new workflow.")

    # ── Build a display dataframe with friendly columns ──────────────────────
    _STATUS_DISPLAY = {
        "running":               "🔵 Running",
        "waiting_for_user":      "🟡 Waiting",
        "completed":             "🟢 Done",
        "completed_with_errors": "🟠 Done (errors)",
        "failed":                "🔴 Failed",
    }

    def _summarize_list(raw, max_items=2) -> str:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(items, list) or not items:
                return ""
            shown = ", ".join(items[:max_items])
            if len(items) > max_items:
                shown += f" +{len(items) - max_items}"
            return shown
        except Exception:
            return ""

    rows_for_display: list[dict] = []
    for _, row in df.iterrows():
        # Keep the table scannable: one role + a "+N" badge for the rest, same for locations.
        # Full criteria are visible on the Workflow Detail screen.
        roles = _summarize_list(row.get("roles_json"), max_items=1)
        locs = _summarize_list(row.get("locations_json"), max_items=1)
        run_label = roles or "(no criteria)"
        if locs:
            run_label += f"  ·  {locs}"

        full_id = row.get("workflow_id", "") or ""

        rows_for_display.append({
            "Status":   _STATUS_DISPLAY.get(row.get("status", ""), str(row.get("status", "—"))),
            "Run":      run_label,
            "Stage":    _friendly_stage(row.get("current_step")),
            "Progress": _stage_progress(row.to_dict()),
            "Started":  _fmt_ts(row.get("started_at")),
            "Updated":  _fmt_ts(row.get("completed_at") or row.get("updated_at")),
            "Best":     int(row["best_score"]) if pd.notna(row.get("best_score")) else None,
            "≥": int(row["threshold"]) if pd.notna(row.get("threshold")) else None,
            "URLs":     int(row["custom_url_count"]) if pd.notna(row.get("custom_url_count")) else 0,
            "Cost":     float(row["cost_usd"]) if pd.notna(row.get("cost_usd")) else 0.0,
            "ID":       (full_id[:8] + "…") if len(full_id) > 8 else full_id,
        })
    display_df = pd.DataFrame(rows_for_display)

    event = st.dataframe(
        display_df,
        key="wf_history_table",
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Status":   st.column_config.TextColumn("Status",   width="small"),
            "Run":      st.column_config.TextColumn("Run",      width="medium",
                                                       help="Roles and locations searched (full list on the detail screen)"),
            "Stage":    st.column_config.TextColumn("Stage",    width="small"),
            "Progress": st.column_config.TextColumn("Progress", width="small"),
            "Started":  st.column_config.TextColumn("Started",  width="small"),
            "Updated":  st.column_config.TextColumn("Updated",  width="small"),
            "Best":     st.column_config.NumberColumn("Best",   format="%d", width="small"),
            "≥":        st.column_config.NumberColumn("≥",      format="%d", width="small",
                                                       help="min_match_score for this run"),
            "URLs":     st.column_config.NumberColumn("URLs",   format="%d", width="small",
                                                       help="custom URLs supplied at run start"),
            "Cost":     st.column_config.NumberColumn("Cost",   format="$%.4f", width="small"),
            "ID":       st.column_config.TextColumn("ID",       width="small",
                                                       help="Select the row, then click Open detail."),
        },
    )

    # Resolve the selected workflow_id from the dataframe widget. Selection state
    # is read from the keyed widget (st.session_state["wf_history_table"]) which
    # survives reruns, with a fallback to the event return value for the same
    # render cycle.
    sel: list[int] = []
    widget_state = st.session_state.get("wf_history_table")
    if widget_state and getattr(widget_state, "selection", None):
        sel = list(widget_state.selection.rows or [])
    if not sel and event and getattr(event, "selection", None):
        sel = list(event.selection.rows or [])

    chosen = ""
    if sel and sel[0] < len(df):
        chosen = str(df.iloc[sel[0]].get("workflow_id", "") or "")

    # Explicit navigation affordance. Row-click selection alone is easy to miss
    # (no visible focus ring) and the auto-navigate variant occasionally does
    # not fire if the selection state lands on the same id between renders.
    nav_col1, nav_col2 = st.columns([1, 4])
    open_clicked = nav_col1.button(
        "Open detail →",
        type="primary",
        disabled=not chosen,
        use_container_width=True,
        help="Select a row above, then click here to open its Workflow Detail.",
    )
    if chosen:
        nav_col2.caption(f"Selected: `{chosen}`")
    else:
        nav_col2.caption("Select any row above to enable Open detail.")

    if open_clicked and chosen:
        _navigate("Workflow Detail", detail_workflow_id=chosen, detail_job_id=None)
    # Convenience: also auto-navigate on a fresh row click when the user picks a
    # different run than the one currently loaded in the Detail view.
    elif chosen and chosen != st.session_state.get("detail_workflow_id"):
        _navigate("Workflow Detail", detail_workflow_id=chosen, detail_job_id=None)

    # Surface the most recent error inline so failures are obvious without drilling in
    err_rows = df[df["error_message"].notna()] if "error_message" in df.columns else pd.DataFrame()
    if not err_rows.empty:
        with st.expander(f"⚠ Errors on {len(err_rows)} run(s)"):
            for _, e in err_rows.head(5).iterrows():
                st.markdown(f"- `{e['workflow_id'][:18]}…` — {str(e['error_message'])[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW DETAIL — unified per-run drill-down
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Workflow Detail":
    st.header("Workflow Detail")

    # Sync the input widget to the navigation target on actual nav changes (row
    # click in History or sidebar button) but preserve user typing across reruns.
    # Why: st.text_input without a key holds onto its old widget value and ignores
    # the new value= arg, so a fresh detail_workflow_id from _navigate would be
    # clobbered back to whatever was in the input on the previous render.
    nav_target = st.session_state.detail_workflow_id or st.session_state.workflow_id or ""
    if nav_target and st.session_state.get("_detail_wf_synced") != nav_target:
        st.session_state.detail_wf_input = nav_target
        st.session_state._detail_wf_synced = nav_target

    wf_id = st.text_input("Workflow ID", key="detail_wf_input",
                          help="Pick a run from Workflow History or paste an ID.")
    if not wf_id:
        st.info("No workflow selected.")
        st.stop()
    st.session_state.detail_workflow_id = wf_id

    record = load_workflow_run(wf_id)
    state = (record or {}).get("state") or {}
    status = (record or {}).get("status", "unknown")

    # Status header
    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴", "completed_with_errors": "🟠",
            "awaiting_scoring_selection": "🟡"}.get(status, "⚪")
    h1, h2 = st.columns([3, 1])
    h1.markdown(f"### {icon} `{status}`")
    h2.caption(f"Started: {(record or {}).get('started_at', '—')}")

    metrics = summary_metrics(state)
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Discovered", metrics["jobs_discovered"])
    g2.metric("Scored", metrics["jobs_scored"])
    g3.metric("Selected (auto)", metrics["jobs_selected"])
    g4.metric("LLM cost", f"${metrics['estimated_cost_usd']:.4f}")
    g5, g6, g7, g8 = st.columns(4)
    g5.metric("LLM calls", metrics["llm_calls"])
    g6.metric("Tokens in", f"{metrics['tokens_input']:,}")
    g7.metric("Tokens out", f"{metrics['tokens_output']:,}")
    g8.metric("Review rounds", metrics["review_rounds"])

    # ── Manual scoring selection (ADR-060) ────────────────────────────────────
    # When a manual-selection run is parked after discovery, let the user pick
    # which discovered jobs are worth the research + scoring spend. Only the
    # selected jobs are scored; the rest are skipped.
    if status == "awaiting_scoring_selection":
        st.markdown("---")
        st.subheader("🧭 Select jobs to score")
        st.caption(
            "Manual selection is on for this run. Discovery cast a wide net; pick "
            "the jobs worth the research + scoring spend. Only the jobs you select "
            "are scored -- the rest are skipped, at no cost."
        )
        discovered = state.get("normalized_jobs") or []
        if not discovered:
            st.info("No jobs were discovered for this run.")
        else:
            options: dict[str, str] = {}
            for j in discovered:
                jid = j.get("id") or j.get("source_job_id") or ""
                if not jid:
                    continue
                options[jid] = (f"{j.get('title') or '(untitled)'} — "
                                f"{j.get('company') or '?'} ({j.get('location') or '?'})")
            picked = st.multiselect(
                f"{len(options)} discovered job(s)",
                options=list(options.keys()),
                format_func=lambda jid: options.get(jid, jid),
                key=f"score_select_{wf_id}",
            )
            b_sel, cap_sel = st.columns([1, 3])
            if b_sel.button("Score selected", type="primary",
                            disabled=not picked, key=f"score_btn_{wf_id}"):
                try:
                    resp = api.submit_scoring_selection(wf_id, picked)
                    st.success(
                        f"Scoring {resp.get('scoring_count', len(picked))} selected "
                        "job(s). Switch to Live Run Monitor to watch progress, or "
                        "reopen this run when it finishes."
                    )
                except Exception as exc:
                    st.error(f"Could not start scoring: {exc}")
            cap_sel.caption(
                f"{len(picked)} selected. Unselected jobs are skipped (not scored)."
            )
        st.stop()

    # ── Find & Score — Pipeline table (jobs × stages) ─────────────────────────
    st.markdown("---")
    st.subheader("📍 Find & Score — jobs surfaced and ranked")
    st.caption("What came back from the search and how each job scored across the three career tracks. "
               "Jobs whose best track score meets your threshold automatically advance to deep review.")

    jobs_df = load_workflow_jobs(wf_id)
    if jobs_df.empty:
        st.info("No scored jobs yet for this run.")
    else:
        # ADR-057: hide-by-default toggle for excluded rows in this run.
        n_excluded = int(jobs_df["excluded"].fillna(0).sum()) if "excluded" in jobs_df.columns else 0
        show_excluded = False
        if n_excluded:
            show_excluded = st.toggle(
                f"Show {n_excluded} excluded job(s) in this run",
                value=False,
                key=f"show_excluded_{wf_id}",
            )
        if not show_excluded and "excluded" in jobs_df.columns:
            jobs_df = jobs_df[(jobs_df["excluded"].fillna(0) == 0)].reset_index(drop=True)

        view_df = jobs_df.copy()
        view_df["🚫"] = view_df["excluded"].fillna(0).apply(lambda v: "🚫" if v else "")
        view_df["✅ Reviewed"] = view_df["reviewed_at"].apply(_checked)
        view_df["✅ Advised"] = view_df["advised_at"].apply(_checked)
        view_df["✅ Prep"] = view_df["prep_at"].apply(_checked)

        ev = st.dataframe(
            view_df[[
                "🚫",
                "title", "company", "location", "url",
                "overall_score", "technical_score", "architecture_score", "leadership_score",
                "✅ Reviewed", "✅ Advised", "✅ Prep",
                "found_at", "scored_at",
            ]].rename(columns={
                "title": "Title", "company": "Company", "location": "Location", "url": "URL",
                "overall_score": "Overall",
                "technical_score": "Tech",
                "architecture_score": "Arch",
                "leadership_score": "Lead",
                "found_at": "Found", "scored_at": "Scored",
            }),
            key=f"jobs_table_{wf_id}",
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "🚫":      st.column_config.TextColumn("🚫", width="small",
                                                       help="🚫 = excluded from cross-run analytics + future discovery"),
                "URL":     st.column_config.LinkColumn("URL", width="small"),
                "Overall": st.column_config.ProgressColumn("Overall", min_value=0, max_value=100, format="%d"),
                "Tech":    st.column_config.ProgressColumn("Tech",    min_value=0, max_value=100, format="%d"),
                "Arch":    st.column_config.ProgressColumn("Arch",    min_value=0, max_value=100, format="%d"),
                "Lead":    st.column_config.ProgressColumn("Lead",    min_value=0, max_value=100, format="%d"),
            },
        )

        # ADR-057: per-row exclude / un-exclude action. Single-row selection
        # is the same affordance Workflow History uses, kept consistent.
        sel_rows = (ev.selection.rows if ev and getattr(ev, "selection", None) else []) or []
        sel_job: dict | None = None
        if sel_rows and sel_rows[0] < len(jobs_df):
            sel_job = jobs_df.iloc[sel_rows[0]].to_dict()

        ex_col1, ex_col2 = st.columns([1, 4])
        if sel_job:
            is_excluded = bool(sel_job.get("excluded") or 0)
            label = "♻ Un-exclude selected" if is_excluded else "🚫 Exclude selected"
            if ex_col1.button(label, key=f"excl_btn_{wf_id}", use_container_width=True):
                try:
                    if is_excluded:
                        api.unexclude_job(sel_job["job_id"])
                        st.success(f"Un-excluded: {sel_job.get('title', '')}")
                    else:
                        api.exclude_job(sel_job["job_id"])
                        st.success(f"Excluded: {sel_job.get('title', '')}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Action failed: {exc}")
            ex_col2.caption(
                f"Selected: **{sel_job.get('title','(untitled)')}** @ {sel_job.get('company','')}"
                + ("  ·  currently excluded" if is_excluded else "")
            )
        else:
            ex_col1.button("🚫 Exclude selected", disabled=True, use_container_width=True,
                           key=f"excl_btn_disabled_{wf_id}")
            ex_col2.caption("Select a row above to enable Exclude / Un-exclude.")

        # Drill into a specific job — picker + button
        st.markdown("**Drill into a job →**")
        _options = {
            f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
            for _, r in jobs_df.iterrows()
        }
        _label = st.selectbox(
            "Pick a job to see all of its outputs (scoring, every review round, advice, prep) with timestamps",
            options=list(_options.keys()),
            label_visibility="collapsed",
        )
        if st.button("Drill in ▶", key="drill_in_job"):
            _navigate("Job Detail",
                      detail_workflow_id=wf_id,
                      detail_job_id=_options[_label])

    # ── Review — deep critic + career advice (per job) ────────────────────────
    rev_df = load_deep_review_results(wf_id)
    if not rev_df.empty:
        st.markdown("---")
        st.subheader("📋 Review — deep analysis & career guidance")
        st.caption("Per-job critic + auditor output and the career advisor's positioning summary. "
                   "Resume gaps can be addressed via tailoring; career gaps must not be fabricated.")
        # Look up title/company/location and review/advice timestamps from jobs_df
        # so each expander header is human-readable (UUIDs are useless to the user).
        meta_by_job = {
            r["job_id"]: {
                "title":    r.get("title") or "(untitled)",
                "company":  r.get("company") or "",
                "location": r.get("location") or "",
                "reviewed": r.get("reviewed_at"),
                "advised":  r.get("advised_at"),
            }
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in rev_df.iterrows():
            jid = row["job_id"]
            meta = meta_by_job.get(jid, {})
            title = meta.get("title") or "(untitled)"
            company = meta.get("company") or ""
            location = meta.get("location") or ""
            ts_caption = []
            if meta.get("reviewed"):
                ts_caption.append(f"reviewed `{_fmt_ts(meta['reviewed'])}`")
            if meta.get("advised"):
                ts_caption.append(f"advised `{_fmt_ts(meta['advised'])}`")
            ts_str = "  ·  ".join(ts_caption)
            header = title
            if company:
                header += f" @ {company}"
            if location:
                header += f"  ·  {location}"
            summary = (row.get("overall_fit_summary") or "").strip()
            if summary:
                header += f" — {summary[:80]}"
            if ts_str:
                header += f"  ·  {ts_str}"
            with st.expander(header):
                c1, c2 = st.columns(2)
                c1.markdown("**Resume Gaps** *(can tailor)*")
                try:
                    for g in (json.loads(row.get("resume_only_gaps_json") or "[]") or []):
                        c1.markdown(f"- {g}")
                except Exception:
                    pass
                c2.markdown("**Career Gaps** *(must not fabricate)*")
                try:
                    for g in (json.loads(row.get("career_gaps_observed_json") or "[]") or []):
                        c2.markdown(f"- {g}")
                except Exception:
                    pass
                if row.get("positioning_summary"):
                    st.markdown(f"**Positioning:** {row['positioning_summary']}")
                if row.get("recommended_next_action"):
                    st.markdown(f"**Recommended:** {row['recommended_next_action']}")

    # ── Prep — interview readiness ────────────────────────────────────────────
    prep_df = load_interview_prep(wf_id)
    if not prep_df.empty:
        st.markdown("---")
        st.subheader("✨ Prep — interview readiness")
        st.caption("Likely topics, technical areas to brush up on, and a 7-day prep plan per qualifying job.")
        prep_meta = {
            r["job_id"]: {
                "title":    r.get("title") or "(untitled)",
                "company":  r.get("company") or "",
                "location": r.get("location") or "",
                "prep":     r.get("prep_at"),
            }
            for _, r in (jobs_df.iterrows() if not jobs_df.empty else [])
        } if not jobs_df.empty else {}
        for _, row in prep_df.iterrows():
            jid = row["job_id"]
            meta = prep_meta.get(jid, {})
            title = meta.get("title") or "(untitled)"
            company = meta.get("company") or ""
            location = meta.get("location") or ""
            header = title
            if company:
                header += f" @ {company}"
            if location:
                header += f"  ·  {location}"
            if meta.get("prep"):
                header += f"  ·  prep `{_fmt_ts(meta['prep'])}`"
            with st.expander(header):
                # Render every section the InterviewCoach produces. The previous
                # version only rendered topics + plan and dropped 4 sections
                # entirely; combined with the field-name bug below it, the user
                # never saw any prep output at all.
                def _render_list_section(label: str, json_key: str, *, bullets: bool = True) -> None:
                    try:
                        items = json.loads(row.get(json_key) or "[]")
                    except Exception:
                        return
                    if not items:
                        return
                    st.markdown(f"**{label}**")
                    if bullets:
                        for item in items:
                            st.markdown(f"- {item}")
                    else:
                        st.markdown(", ".join(items))

                _render_list_section("Likely interview topics", "likely_topics_json")
                _render_list_section("Technical topics to review", "technical_topics_json")
                _render_list_section("Leadership stories to prepare", "leadership_stories_json")
                _render_list_section("Weak areas to defend", "weak_areas_json")
                _render_list_section("Questions to ask the interviewer", "questions_to_ask_json")
                _render_list_section("7-day prep plan", "seven_day_plan_json")
                conf = row.get("confidence")
                if conf is not None:
                    try:
                        st.caption(f"Coach confidence: {int(conf)}%")
                    except (TypeError, ValueError):
                        pass

    # ── Prep — resume tailoring (on-demand per job, the action surface) ───────
    st.markdown("---")
    st.subheader("✨ Prep — tailored resume drafts + interview")
    st.caption(
        "Pick ANY scored job (ADR-061) and generate evidence-bound section "
        "suggestions, or prep for the interview. Every suggestion cites the "
        "original line in your resume; missing experience is labelled as a gap, "
        "never rewritten as if present. A job that was not auto-selected for deep "
        "review is deep-reviewed on demand first, so the cost includes a critic "
        "pass. Approve, edit, revise, or reject each draft to record your decision."
    )

    # ADR-061: tailoring + interview prep are available for any scored job, not
    # only the auto-selected top-3. Fall back to selected_jobs for older runs
    # whose state predates scored_jobs being carried through.
    scored_jobs_state = [
        j for j in (state.get("scored_jobs") or [])
        if j.get("status") == "scored"
    ] or (state.get("selected_jobs") or [])
    if not scored_jobs_state:
        st.info("No scored jobs in this run yet — tailoring and interview prep "
                "need at least one scored job.")
    else:
        try:
            with st.spinner("Loading tailoring drafts…"):
                tail_index = _cached_list_tailorings(wf_id)
        except Exception as exc:
            st.error(f"Could not load existing tailorings: {exc}")
            tail_index = []

        by_job: dict[str, list[dict]] = {}
        for t in tail_index:
            by_job.setdefault(t.get("job_id", ""), []).append(t)

        def _decide(tid: str, choice: str, edited: dict | None = None) -> None:
            try:
                api.submit_tailoring_decision(tid, choice, edited=edited)
                _cached_list_tailorings.clear()
                st.success(f"Decision saved: {choice}")
                st.rerun()
            except Exception as exc:
                st.error(f"Decision failed: {exc}")

        selected_ids = {
            (sj.get("job_id") or sj.get("id"))
            for sj in (state.get("selected_jobs") or [])
        }
        for sj in scored_jobs_state:
            jid = sj.get("job_id") or sj.get("id") or ""
            if not jid:
                continue
            jtitle = sj.get("title") or "(untitled)"
            jcompany = sj.get("company") or ""
            existing = by_job.get(jid, [])
            label = f"**{jtitle}** @ {jcompany}  ·  job `{jid[:8]}…`"
            if jid in selected_ids:
                label += "  ·  auto-selected"
            if existing:
                label += f"  ·  {len(existing)} draft(s)"
            with st.expander(label, expanded=False):
                if jid not in selected_ids:
                    st.caption(
                        "Not auto-selected for deep review — generating a draft "
                        "will run a deep-review pass on demand first (extra cost)."
                    )
                trig_col, prep_col, _ = st.columns([1, 1, 3])
                if trig_col.button("✨ Generate new draft", key=f"trig_tail_{jid}"):
                    with st.spinner("Tailoring + fidelity review (~60-90s, longer if deep-reviewing first)…"):
                        try:
                            api.trigger_tailoring(wf_id, jid)
                            _cached_list_tailorings.clear()
                            st.rerun()
                        except httpx.ReadTimeout:
                            # The synchronous server path can outlast the socket
                            # timeout (180s). The draft typically lands in
                            # tailored_resumes anyway -- check the list.
                            _cached_list_tailorings.clear()
                            st.warning(
                                "Client timed out, but the server may have completed the draft anyway. "
                                "Click the section header to collapse and reopen — the new draft should appear."
                            )
                        except Exception as exc:
                            st.error(f"Tailoring failed: {exc}")
                if prep_col.button("🎤 Prep for interview", key=f"trig_prep_{jid}"):
                    with st.spinner("Interview coach (~10-20s)…"):
                        try:
                            api.trigger_interview_prep(wf_id, jid)
                            st.success("Interview prep generated — see the readiness section above.")
                            st.rerun()
                        except httpx.ReadTimeout:
                            st.warning("Client timed out, but the prep may have completed. Reload to check.")
                        except Exception as exc:
                            st.error(f"Interview prep failed: {exc}")
                if not existing:
                    st.caption("No drafts yet for this job. Click **Generate new draft** to create one.")
                else:
                    rp_for_render = state.get("resume_profile") or None
                    for t in existing:
                        st.markdown("---")
                        _render_tailoring_card(t, _decide, resume_profile=rp_for_render)

    # ── Diagnostics — collapsed by default to keep the action surfaces above ──
    st.markdown("---")
    st.subheader("🔧 Diagnostics")
    st.caption("Settings used, cost breakdown, limits hit, and any errors. "
               "Collapsed by default — open if a run looks off.")

    cfg_used = state.get("effective_config") or {}
    sc = state.get("search_criteria") or {}
    cc = state.get("custom_urls") or []
    breakdown = compute_breakdown(wf_id)
    findings = analyze(state)
    errors = state.get("errors") or []

    with st.expander("Settings used for this run", expanded=False):
        if cfg_used or sc or cc:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Search criteria**")
                st.json(sc, expanded=False)
                if cc:
                    st.markdown(f"**Custom URLs** ({len(cc)})")
                    for u in cc:
                        st.markdown(f"- {u}")
            with col_b:
                st.markdown("**Effective config**")
                st.json(cfg_used, expanded=False)
        else:
            st.caption("No settings snapshot stored for this run (likely a pre-snapshot legacy run).")

    if breakdown["rows"]:
        agg = breakdown["aggregate"]
        # Promoted out of a collapsed expander to its own visible subsection.
        # Cost is a primary operational concern; users shouldn't have to dig.
        st.markdown(f"#### 💰 Cost breakdown — ${agg['cost_usd']:.4f} across {agg['calls']} calls")
        st.caption(
            f"{agg['calls']} calls · {agg['tokens_input']:,} tokens in · "
            f"{agg['tokens_output']:,} tokens out · ~{int(agg['avg_latency_ms'])} ms avg latency. "
            "See the **Cost Dashboard** view for cross-run trends."
        )
        cost_df = pd.DataFrame(breakdown["rows"])
        # Side-by-side: bar chart and numeric table, so both visual and exact reads work.
        c_chart, c_table = st.columns([2, 3])
        with c_chart:
            chart_df = cost_df.sort_values("cost_usd", ascending=True)
            fig = px.bar(
                chart_df, x="cost_usd", y="agent_name", orientation="h",
                text="cost_usd",
                labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
                color="cost_usd", color_continuous_scale="oranges",
            )
            fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                              height=max(180, 32 * len(chart_df)),
                              coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        with c_table:
            tbl = cost_df.rename(columns={
                "agent_name": "Agent", "provider": "Provider", "model": "Model",
                "calls": "Calls", "tokens_input": "Tokens in",
                "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
                "avg_latency_ms": "Avg latency",
            })
            st.dataframe(
                tbl, hide_index=True, use_container_width=True,
                column_config={
                    "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                    "Avg latency": st.column_config.NumberColumn(format="%d ms"),
                },
            )

    # Limits & Constraints — keep open when something fired so the user notices
    _has_findings = bool(findings)
    with st.expander(
        f"Limits & Constraints" + (f" — {len(findings)} finding(s)" if _has_findings else ""),
        expanded=_has_findings,
    ):
        if not findings:
            st.success("No execution limits clipped this run.")
        else:
            for f in findings:
                (st.warning if f["severity"] == "warning" else st.info)(f["message"])

    # Errors — open when present
    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=True):
            for err in errors:
                st.json(err, expanded=False)


# ══════════════════════════════════════════════════════════════════════════════
# JOB DETAIL — single-job drilldown for one workflow run
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Job Detail":
    st.header("Job Detail")

    wf_id = st.session_state.detail_workflow_id
    job_id = st.session_state.detail_job_id

    # ── Inline picker when navigated here directly ────────────────────────────
    if not wf_id or not job_id:
        st.caption("Pick a workflow run and job to drill into.")
        _all_runs = load_recent_workflows()
        if _all_runs.empty:
            st.info("No workflow runs found. Start one from **Start New Run**.")
            st.stop()

        _run_options = {
            f"`{r['workflow_id']}`  ({int(r.get('jobs_scored', 0))} scored)": r["workflow_id"]
            for _, r in _all_runs.iterrows()
        }
        _run_label = st.selectbox("Workflow run", list(_run_options.keys()), key="jd_wf_pick")
        _picked_wf = _run_options[_run_label]

        _jobs_pick = load_workflow_jobs(_picked_wf)
        if _jobs_pick.empty:
            st.info("No scored jobs in this run yet.")
            st.stop()

        _job_options = {
            f"{r['title']} @ {r['company']}  ·  overall {int(r['overall_score'])}": r["job_id"]
            for _, r in _jobs_pick.iterrows()
        }
        _job_label = st.selectbox("Job", list(_job_options.keys()), key="jd_job_pick")
        if st.button("Open ▶", key="jd_open"):
            _navigate("Job Detail",
                      detail_workflow_id=_picked_wf,
                      detail_job_id=_job_options[_job_label])
        st.stop()

    # Top breadcrumb / back nav
    nav1, nav2 = st.columns([1, 1])
    if nav1.button("← Back to Workflow Detail"):
        _navigate("Workflow Detail")
    if nav2.button("Back to Workflow History"):
        _navigate("Workflow History")

    pipeline = load_job_pipeline(wf_id, job_id)
    job = pipeline["job"] or {}

    # ── Job header ────────────────────────────────────────────────────────────
    if not job:
        st.error(f"No job row found for `{job_id}`. The job may have been purged or never persisted.")
        st.stop()

    st.markdown(f"### {job.get('title') or '(no title)'}")
    st.markdown(
        f"**{job.get('company') or '—'}**  ·  {job.get('location') or '—'}  ·  "
        f"source `{job.get('source') or '—'}`"
    )
    if job.get("url"):
        st.markdown(f"[Open posting ↗]({job['url']})")
    st.caption(
        f"Workflow `{wf_id}`  ·  job_id `{job_id}`  ·  "
        f"first found `{_fmt_ts(job.get('found_at'))}`"
    )

    # ── Build a chronological timeline of all this job's stages ────────────
    timeline_rows: list[dict] = []
    if job.get("found_at"):
        timeline_rows.append({"ts": job["found_at"], "stage": "Discovered"})
    if pipeline.get("score"):
        timeline_rows.append({"ts": pipeline["score"]["created_at"], "stage": "Scored"})
    for r in pipeline.get("review_rounds") or []:
        timeline_rows.append({
            "ts": r["created_at"],
            "stage": f"Review round {r['round_number']} (audit {r.get('audit_score', '—')})",
        })
    if pipeline.get("final_review"):
        timeline_rows.append({"ts": pipeline["final_review"]["created_at"],
                              "stage": "Final review persisted"})
    if pipeline.get("advice"):
        timeline_rows.append({"ts": pipeline["advice"]["created_at"], "stage": "Career advice"})
    if pipeline.get("prep"):
        timeline_rows.append({"ts": pipeline["prep"]["created_at"], "stage": "Interview prep"})
    timeline_rows.sort(key=lambda r: r["ts"] or "")

    if timeline_rows:
        st.markdown("---")
        st.subheader("Timeline for this job")
        _bullets(
            "",
            [f"`{_fmt_ts(r['ts'])}` — {r['stage']}" for r in timeline_rows],
        )

    # ── Score ─────────────────────────────────────────────────────────────────
    score = pipeline.get("score")
    st.markdown("---")
    if score:
        st.subheader(f"Score — produced `{_fmt_ts(score['created_at'])}`")
        sd = score["data"] or {}
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Overall",      sd.get("overall_score", "—"))
        s2.metric("Technical",    sd.get("technical_score", "—"))
        s3.metric("Architecture", sd.get("architecture_score", "—"))
        s4.metric("Leadership",   sd.get("leadership_score", "—"))
        st.markdown("")
        _para("Summary",     sd.get("match_summary"))
        _para("Recommended", sd.get("recommended_next_action"))
        _bullets("Strengths", sd.get("strengths"))
        _bullets("Gaps",      sd.get("gaps"))
    else:
        st.subheader("Score")
        st.info("This job was not scored.")

    # ── Review rounds (chronological) ────────────────────────────────────────
    rounds = pipeline.get("review_rounds") or []
    if rounds:
        st.markdown("---")
        st.subheader(f"Deep Review — {len(rounds)} round(s)")
        for r in rounds:
            with st.expander(
                f"Round {r['round_number']} — audit score {r.get('audit_score', '—')}  ·  "
                f"`{_fmt_ts(r['created_at'])}`",
                expanded=(r["round_number"] == len(rounds)),
            ):
                critic = r.get("critic") or {}
                audit = r.get("audit") or {}
                cc1, cc2 = st.columns(2)
                with cc1:
                    st.markdown("#### Critic")
                    _para("Fit summary",                 critic.get("overall_fit_summary"))
                    _bullets("Critical gaps",            critic.get("critical_gaps"))
                    _bullets("Resume gaps (can tailor)", critic.get("resume_only_gaps"))
                    _bullets("Career gaps",              critic.get("career_gaps_observed"))
                with cc2:
                    st.markdown("#### Auditor")
                    _para("Quality summary",          audit.get("quality_summary"))
                    _bullets("Missing analysis",      audit.get("missing_analysis_points"))
                    _bullets("Recommended revisions", audit.get("recommended_revision_instructions"))
                    if r.get("stop_reason"):
                        st.caption(f"Stop reason: {r['stop_reason']}")

    # ── Final resume review (the persisted "best" one) ────────────────────────
    fr = pipeline.get("final_review")
    if fr:
        st.markdown("---")
        st.subheader(f"Final Resume Review — persisted `{_fmt_ts(fr['created_at'])}`")
        d = fr["data"] or {}
        _para("Fit summary",            d.get("overall_fit_summary"))
        _bullets("Suggested improvements", d.get("suggested_improvements"))
        _bullets("Questions for you",      d.get("questions_for_user"))

    # ── Career advice ─────────────────────────────────────────────────────────
    adv = pipeline.get("advice")
    if adv:
        st.markdown("---")
        st.subheader(f"Career Advice — produced `{_fmt_ts(adv['created_at'])}`")
        d = adv["data"] or {}
        _para("Positioning",                d.get("positioning_summary"))
        _para("Recommended positioning",    d.get("recommended_positioning"))
        _para("Recommended next action",    d.get("recommended_next_action"))
        _bullets("Resume gaps (can address through tailoring)", d.get("resume_gaps"))
        _bullets("Career gaps (must not fabricate)",            d.get("career_gaps"))
        _bullets("Skills to strengthen",                        d.get("skills_to_strengthen"))
        _bullets("Experience to collect",                       d.get("experience_to_collect"))
        _bullets("30 / 60 / 90-day plan",                       d.get("thirty_sixty_ninety_day_plan"))

    # ── Interview prep ────────────────────────────────────────────────────────
    prep = pipeline.get("prep")
    if prep:
        st.markdown("---")
        st.subheader(f"Interview Prep — produced `{_fmt_ts(prep['created_at'])}`")
        d = prep["data"] or {}
        _bullets("Likely interview topics",       d.get("likely_interview_topics"))
        _bullets("Technical topics to review",    d.get("technical_topics_to_review"))
        _bullets("Leadership stories to prepare", d.get("leadership_stories_to_prepare"))
        _bullets("Weak areas to defend",          d.get("weak_areas_to_defend"))
        _bullets("Questions to ask the interviewer", d.get("questions_to_ask_interviewer"))
        _bullets("7-day prep plan",               d.get("seven_day_prep_plan"))


# ══════════════════════════════════════════════════════════════════════════════
# START NEW RUN — settings inline + custom URLs
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Start New Run":
    st.header("Start New Run")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {})
    search_cfg = eff.get("search", {}) or {}
    scoring_cfg = eff.get("scoring", {}) or {}

    _default_roles = ", ".join(search_cfg.get("titles", []))
    # ADR-064: locations are one-per-line so "City, State" survives (a comma split
    # would shatter "Atlanta, GA" into "Atlanta" + "GA").
    _default_locations = "\n".join(search_cfg.get("locations", []))

    with st.expander("📋 Settings in play for this run", expanded=True):
        st.caption(
            "Defaults below come from your saved settings. Edits here apply to this run only "
            "and are persisted as overrides so future runs reuse them."
        )

    with st.form("start_run"):
        c1, c2 = st.columns(2)
        with c1:
            # ADR-062: pick from this profile's stored resumes instead of typing a
            # raw id. Falls back to a text box if the profile has no resumes yet
            # (e.g. before onboarding step 2) so a run is still possible.
            _resumes = load_user_resumes(st.session_state.current_user_id)
            if not _resumes.empty:
                _rid_options = list(_resumes["resume_id"])
                _rid_labels = {
                    r["resume_id"]: (f"{r['file_name'] or r['resume_id']}"
                                     + ("  ·  active" if r["is_active"] else ""))
                    for _, r in _resumes.iterrows()
                }
                resume_id = st.selectbox(
                    "Resume",
                    _rid_options,
                    format_func=lambda i: _rid_labels.get(i, i),
                    help="This profile's resumes. The active one is listed first. "
                         "Add more via Profiles > Add profile, or Settings.",
                )
            else:
                resume_id = st.text_input(
                    "Resume ID", value="resume.pdf",
                    help="This profile has no stored resume yet. Enter 'resume.pdf' "
                         "to parse a file in the project root, or add one via Profiles.",
                )
            roles = st.text_input(
                "Roles (comma-separated)",
                value=_default_roles or "Staff Engineer, Principal Engineer",
            )
            locations = st.text_area(
                "Locations (one per line)",
                value=_default_locations or "Remote",
                height=90,
                help="One location per line, e.g. 'Atlanta, GA' on its own line. "
                     "Use 'Remote' (its own line) for a US-wide remote search.",
            )
        with c2:
            run_threshold = st.slider(
                "Min match score for this run",
                min_value=0, max_value=100,
                value=int(scoring_cfg.get("min_match_score", min_score)),
                step=5,
                help="Any track score (tech/arch/lead) at or above this triggers deep review + prep.",
            )
            max_scored = st.number_input(
                "Max jobs to score",
                min_value=1, max_value=25,
                value=int(scoring_cfg.get("max_scored", 10)),
                help="ADR-061: how many jobs get research + scoring (the funnel's "
                     "scored width). Default 10, ceiling 25. In auto mode this is "
                     "also the discovery cap.",
            )
            manual_scoring = st.checkbox(
                "Let me pick which jobs to score (review before scoring)",
                value=bool(scoring_cfg.get("manual_selection", False)),
                help="ADR-060: discover a wider net, then choose which jobs are "
                     "worth the research + scoring spend. Only the jobs you pick "
                     "are scored; the rest are skipped at no cost.",
            )
            max_discovered = st.number_input(
                "Discovery net width (manual mode only)",
                min_value=1, max_value=50,
                value=int(search_cfg.get("max_discovered", 50)),
                help="ADR-061: how many jobs to surface for triage when manual "
                     "selection is on. Default 50, ceiling 50. Ignored in auto mode.",
            )
            # ADR-065: experience window (per profile, off by default).
            min_years_experience = st.number_input(
                "Min years of experience (0 = no limit)",
                min_value=0, max_value=20,
                value=int(search_cfg.get("min_years_experience") or 0),
                help="ADR-065: drop postings asking for fewer years than this "
                     "(e.g. 5 = exclude junior roles). Postings that don't state "
                     "experience are kept. 0 disables the floor.",
            )
            max_years_experience = st.number_input(
                "Max years of experience (0 = no limit)",
                min_value=0, max_value=20,
                value=int(search_cfg.get("max_years_experience") or 0),
                help="ADR-065: drop postings asking for more years than this "
                     "(e.g. 2 = target 0-2 yrs). Postings that don't state "
                     "experience are kept. 0 disables the cap.",
            )
            exclude_senior = st.checkbox(
                "Exclude senior roles",
                value=bool(search_cfg.get("exclude_senior", False)),
                help="ADR-065: drop senior/principal/staff/lead/director/manager/architect "
                     "roles at the source and by title. Use for entry-level profiles.",
            )
            persist_prefs = st.checkbox(
                "Save these settings as my defaults for future runs",
                value=False,
            )

        st.markdown("**Custom job URLs** (optional, one per line — LinkedIn, company career pages, etc.)")
        custom_urls_raw = st.text_area(
            "URLs",
            value="",
            height=120,
            label_visibility="collapsed",
            placeholder="https://www.linkedin.com/jobs/view/123\nhttps://acme.com/careers/staff-engineer",
        )

        submitted = st.form_submit_button("Start Workflow")

    if submitted:
        custom_urls = [u.strip() for u in custom_urls_raw.splitlines() if u.strip()]

        search_criteria = {
            "roles": [r.strip() for r in roles.split(",") if r.strip()],
            "locations": [l.strip() for l in locations.splitlines() if l.strip()],
        }
        effective_config = {
            "scoring": {
                "career_track": "all",
                "min_match_score": int(run_threshold),
                "manual_selection": bool(manual_scoring),
                "max_scored": int(max_scored),
            },
            "search": {
                "max_discovered": int(max_discovered),
                # ADR-065: 0 = no limit (omit so discovery leaves that bound off).
                **({"max_years_experience": int(max_years_experience)}
                   if int(max_years_experience) > 0 else {}),
                **({"min_years_experience": int(min_years_experience)}
                   if int(min_years_experience) > 0 else {}),
                "exclude_senior": bool(exclude_senior),
            },
        }

        if persist_prefs:
            try:
                api.put_config("scoring.min_match_score", int(run_threshold))
                api.put_config("scoring.manual_selection", bool(manual_scoring))
                api.put_config("scoring.max_scored", int(max_scored))
                api.put_config("search.max_discovered", int(max_discovered))
                api.put_config("search.titles", search_criteria["roles"])
                api.put_config("search.locations", search_criteria["locations"])
                api.put_config("search.max_years_experience", int(max_years_experience))
                api.put_config("search.min_years_experience", int(min_years_experience))
                api.put_config("search.exclude_senior", bool(exclude_senior))
                st.session_state.config_cache = None  # invalidate
            except Exception as exc:
                st.warning(f"Settings save failed (run will still start): {exc}")

        try:
            with st.spinner("Submitting workflow…"):
                resp = api.start_workflow(
                    resume_id, search_criteria,
                    effective_config=effective_config,
                    custom_urls=custom_urls,
                )
            st.session_state.workflow_id = resp["workflow_id"]
            st.session_state.last_status = "running"
            st.session_state.last_response = resp
            st.session_state.detail_workflow_id = resp["workflow_id"]
            st.success(f"Workflow started: `{resp['workflow_id']}`")
            st.info("Switch to **Live Run Monitor** to watch progress, or **Workflow Detail** when it finishes.")
        except Exception as exc:
            st.error(f"Failed to start workflow: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# LIVE RUN MONITOR — activity feed only (no HITL — auto-select drives the workflow)
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Live Run Monitor":
    st.header("Live Run Monitor")

    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow in this session.")
        recent = load_recent_workflows()
        if not recent.empty:
            st.markdown("**Reconnect to a recent workflow:**")
            for _, row in recent.iterrows():
                _wf = row["workflow_id"]
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"`{_wf}`  \n{int(row.get('jobs_scored', 0))} scored")
                if c2.button("Reconnect", key=f"rc_{_wf}"):
                    st.session_state.workflow_id = _wf
                    st.rerun()
        st.stop()

    st.caption(f"Workflow: `{wf_id}`")
    cols = st.columns([1, 1, 4])
    if cols[0].button("Refresh"):
        st.cache_data.clear()
        try:
            resp = api.get_workflow_status(wf_id)
            st.session_state.last_response = resp
            st.session_state.last_status = resp.get("status")
        except Exception as exc:
            st.error(f"Could not fetch status: {exc}")

    status = st.session_state.last_status or "unknown"
    resp = st.session_state.last_response or {}

    if status == "running" and cols[1].button("▶ Retry", help="Re-submit after server restart"):
        try:
            api.retry_workflow(wf_id)
            st.session_state.last_status = "running"
            st.success("Workflow re-submitted.")
            st.rerun()
        except Exception as exc:
            st.error(f"Retry failed: {exc}")

    icon = {"running": "🔵", "completed": "🟢", "failed": "🔴"}.get(status, "⚪")
    st.markdown(f"**Status:** {icon} `{status}`")
    if resp.get("current_step"):
        st.markdown(f"**Step:** `{resp['current_step']}`")

    metrics = resp.get("run_metrics") or {}
    if metrics:
        m1, m2, m3 = st.columns(3)
        m1.metric("LLM calls", f"{metrics.get('llm_calls', 0)} / {MAX_LLM_CALLS_PER_RUN}")
        m2.metric("Est. cost", f"${metrics.get('estimated_cost_usd', 0):.4f}")
        m3.metric("Errors", len(resp.get("errors") or []))

    errors = resp.get("errors") or []
    if errors:
        with st.expander(f"Errors ({len(errors)})"):
            for err in errors:
                st.json(err)

    st.markdown("---")
    st.subheader("Run Activity")
    _STEP_ICON = {"completed": "✅", "failed": "❌", "started": "🔄"}

    steps_df = load_step_executions(wf_id)
    if steps_df.empty:
        st.caption("No steps recorded yet — workflow may still be initialising.")
    else:
        for _, row in steps_df.iterrows():
            ic = _STEP_ICON.get(row["status"], "⚪")
            dur = (
                f"  `{int(row['duration_ms']):,} ms`"
                if pd.notna(row.get("duration_ms")) and row["duration_ms"]
                else ""
            )
            notes = f"  — {row['notes']}" if row.get("notes") else ""
            st.markdown(f"{ic} **{row['step']}**{dur}{notes}")

    events_df = load_agent_events(wf_id)
    if not events_df.empty:
        with st.expander(f"Agent Events ({len(events_df)})", expanded=(status == "running")):
            display = events_df.copy()
            display[""] = display["event_type"].map(_STEP_ICON).fillna("⚪")
            display["Time"] = display["created_at"].str[11:19]
            display["Summary"] = display.apply(
                lambda r: (r.get("output_summary") or r.get("input_summary") or "")[:120],
                axis=1,
            )
            display["Duration"] = display["duration_ms"].apply(
                lambda x: f"{int(x):,} ms" if pd.notna(x) and x else "—"
            )
            st.dataframe(
                display[["", "Time", "agent_name", "event_type", "Duration", "Summary"]]
                .rename(columns={"agent_name": "Agent", "event_type": "Event"})
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    llm_df = load_llm_calls(wf_id)
    if not llm_df.empty:
        total_cost = llm_df["estimated_cost"].sum()
        total_tokens = int(llm_df["tokens_input"].sum() + llm_df["tokens_output"].sum())
        with st.expander(f"LLM Calls ({len(llm_df)}) · {total_tokens:,} tokens · ${total_cost:.4f}"):
            st.dataframe(
                llm_df[["agent_name", "model", "tokens_input", "tokens_output",
                        "estimated_cost", "latency_ms"]]
                .rename(columns={
                    "agent_name": "Agent", "model": "Model",
                    "tokens_input": "In", "tokens_output": "Out",
                    "estimated_cost": "Cost ($)", "latency_ms": "Latency (ms)",
                })
                .iloc[::-1],
                hide_index=True, use_container_width=True,
            )

    if status == "completed":
        st.success("Workflow complete — open it in **Workflow Detail** for a unified view.")
    elif status == "failed":
        st.error("Workflow failed — see errors above.")


# ══════════════════════════════════════════════════════════════════════════════
# RUN REPORT
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Run Report":
    st.header("Run Report")
    wf_id = st.session_state.workflow_id
    if not wf_id:
        st.warning("No active workflow.")
        st.stop()
    status = st.session_state.last_status
    if status not in ("completed", "completed_with_errors"):
        st.info(f"Report is available when the workflow completes. Current status: `{status or 'not started'}`.")
        st.stop()
    try:
        with st.spinner("Loading report…"):
            resp = api.get_report(wf_id)
        report = resp.get("report") or {}
        st.caption(f"Generated: {report.get('generated_at', '—')}")
        st.markdown(report.get("markdown", "_No report content._"))
        st.download_button(
            "Download Markdown",
            data=report.get("markdown", ""),
            file_name=f"report_{wf_id}.md",
            mime="text/markdown",
        )
    except Exception as exc:
        st.error(f"Could not fetch report: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# RESUME CLINIC (ADR-066) — standalone, job-agnostic resume review
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Resume Clinic":
    st.header("Resume Clinic")
    st.caption(
        "A job-agnostic resume review. Runs on the resume alone — no discovery, "
        "no scoring, no tailoring. Optional target role / track add the alignment axis."
    )

    user_id = st.session_state.current_user_id

    # ── Resume picker (active resume preselected) ────────────────────────────
    resumes_df = load_user_resumes(user_id)
    if resumes_df.empty:
        st.warning(
            "No resumes found for this profile. Upload one in Profiles, then return here."
        )
        st.stop()

    resume_label_by_id: dict[str, str] = {}
    for _, _row in resumes_df.iterrows():
        _flag = " (active)" if int(_row.get("is_active") or 0) else ""
        resume_label_by_id[str(_row["resume_id"])] = (
            f"{_row.get('file_name') or _row['resume_id']}  ·  v{_row.get('version') or '?'}{_flag}"
        )

    active_row = resumes_df[resumes_df["is_active"] == 1].head(1)
    default_resume_id = str(active_row.iloc[0]["resume_id"]) if not active_row.empty else str(
        resumes_df.iloc[0]["resume_id"]
    )

    rc_form_col, rc_results_col = st.columns([1, 2])
    with rc_form_col:
        st.subheader("Run a clinic")
        sel_resume_id = st.selectbox(
            "Resume",
            options=list(resume_label_by_id.keys()),
            format_func=lambda rid: resume_label_by_id.get(rid, rid),
            index=list(resume_label_by_id.keys()).index(default_resume_id),
            key="rc_resume_id",
        )

        # Pre-fill target role from profile.search_criteria.roles[0] if available
        prefill_role = ""
        try:
            _cfg = _get_config_cached().get("effective_config", {}) or {}
            _roles = (_cfg.get("search", {}) or {}).get("titles") or []
            if _roles:
                prefill_role = str(_roles[0])
        except Exception:
            prefill_role = ""
        target_role = st.text_input(
            "Target role (optional)",
            value=prefill_role,
            placeholder="e.g. entry-level security analyst",
            help=(
                "Free text. Adds the alignment axis (missing skills / keywords / "
                "certifications). Leave blank for quality-only mode."
            ),
            key="rc_target_role",
        )
        target_track = st.selectbox(
            "Target track (optional)",
            options=["", "ic", "architect", "management"],
            format_func=lambda x: "—" if x == "" else x.upper() if x == "ic" else x.title(),
            key="rc_target_track",
        )
        seniority_aware = st.toggle(
            "Seniority-aware feedback",
            value=False,
            help=(
                "When on, the reviewer calibrates findings, fixes, and rewrites to "
                "the candidate's career stage as inferred from the resume "
                "(early-career: project/education-forward; senior+: scope and outcomes)."
            ),
            key="rc_seniority_aware",
        )
        run_clicked = st.button("Run clinic", type="primary", use_container_width=True)

    if run_clicked:
        try:
            with st.spinner("Running clinic review… (resume reviewer + fidelity)"):
                row = api.run_resume_clinic(
                    user_id,
                    resume_id=sel_resume_id,
                    target_role=target_role.strip() or None,
                    target_track=target_track or None,
                    seniority_aware=bool(seniority_aware),
                )
            st.session_state.rc_last_review = row
            st.success("Clinic review complete.")
        except Exception as exc:
            st.error(f"Clinic failed: {exc}")
            st.session_state.rc_last_review = None

    # ── Results pane ─────────────────────────────────────────────────────────
    with rc_results_col:
        review = st.session_state.get("rc_last_review")
        if not review:
            st.info("Pick a resume and click **Run clinic** to start.")
        else:
            _quality = review.get("quality") or {}
            _alignment = review.get("alignment") or None
            _overhaul = review.get("overhaul") or {}
            _fid = review.get("fidelity_review") or None

            st.subheader("Quality scorecard")
            st.caption(_quality.get("overall_summary") or "")
            _dims = _quality.get("dimensions") or []
            if _dims:
                _rating_chip = {
                    "strong": "🟢 strong",
                    "adequate": "🟡 adequate",
                    "needs_work": "🔴 needs work",
                }
                for _d in _dims:
                    _name = (_d.get("dimension") or "").replace("_", " ").title()
                    _rating = _rating_chip.get(_d.get("rating", ""), _d.get("rating", ""))
                    with st.expander(f"{_name}  ·  {_rating}", expanded=False):
                        _findings = _d.get("findings") or []
                        _fixes = _d.get("fixes") or []
                        if _findings:
                            st.markdown("**Findings**")
                            for _f in _findings:
                                st.markdown(f"- {_f}")
                        if _fixes:
                            st.markdown("**Fixes**")
                            for _f in _fixes:
                                st.markdown(f"- {_f}")

            if _alignment:
                st.subheader("Role / track alignment")
                st.caption(_alignment.get("fit_summary") or "")
                _conf = (_alignment.get("confidence") or "").title()
                st.caption(f"Confidence: **{_conf}**")
                _cols = st.columns(2)
                with _cols[0]:
                    if _alignment.get("missing_skills"):
                        st.markdown("**Missing skills**")
                        for _s in _alignment["missing_skills"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("missing_keywords"):
                        st.markdown("**Missing keywords**")
                        for _s in _alignment["missing_keywords"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("emphasize"):
                        st.markdown("**Emphasize on the resume**")
                        for _s in _alignment["emphasize"]:
                            st.markdown(f"- {_s}")
                with _cols[1]:
                    if _alignment.get("suggested_certifications"):
                        st.markdown("**Suggested certifications**")
                        for _s in _alignment["suggested_certifications"]:
                            st.markdown(f"- {_s}")
                    if _alignment.get("suggested_projects"):
                        st.markdown("**Suggested projects**")
                        for _s in _alignment["suggested_projects"]:
                            st.markdown(f"- {_s}")

            _reorg = _overhaul.get("reorganization") or {}
            if _reorg:
                st.subheader("Reorganization plan")
                _order = _reorg.get("section_order") or []
                if _order:
                    st.markdown("**Proposed section order:** " + " → ".join(_order))
                _moves = _reorg.get("moves") or []
                if _moves:
                    _action_chip = {"move": "↕️", "cut": "🗑️", "promote": "⬆️"}
                    for _m in _moves:
                        st.markdown(
                            f"{_action_chip.get(_m.get('action'), '•')} "
                            f"**{(_m.get('action') or '').title()}** · "
                            f"{_m.get('subject') or ''}  ·  _{_m.get('rationale') or ''}_"
                        )

            _rewrites = _overhaul.get("rewrites") or []
            if _rewrites:
                st.subheader("Rewrites")
                _ct_chip = {
                    "restate":  "🔁 restate",
                    "reorder":  "↔ reorder",
                    "quantify": "🔢 quantify",
                    "reframe":  "🎯 reframe",
                }
                for _i, _r in enumerate(_rewrites):
                    _ct = _r.get("claim_type") or "restate"
                    st.markdown(
                        f"_Suggestion {_i + 1}_  ·  {_ct_chip.get(_ct, _ct)}  ·  "
                        f"_{_r.get('section_label') or ''}_"
                    )
                    _ca, _cb = st.columns(2)
                    _ca.markdown("_Original_")
                    _ca.markdown(f"> {_r.get('original_text') or '_(net-new line)_'}")
                    _cb.markdown("_Suggested_")
                    _cb.markdown(f"> {_r.get('suggested_text') or '—'}")
                    _ev = (_r.get("supporting_evidence") or "").strip()
                    if _ev:
                        st.caption(f"📎 Evidence from your resume: _{_ev}_")
                    st.markdown("")

            if _fid:
                st.subheader("Fidelity check")
                _verdict = _fid.get("approval_recommendation") or "—"
                _verdict_chip = {
                    "approve": "🟢 approve",
                    "revise":  "🟡 revise",
                    "reject":  "🔴 reject",
                }.get(_verdict, _verdict)
                st.markdown(f"**Recommendation:** {_verdict_chip}  ·  confidence **{_fid.get('confidence', 0)}**")
                _unsupported = _fid.get("unsupported_claims") or []
                _fabricated = _fid.get("fabricated_metrics") or []
                if _unsupported:
                    st.warning("Unsupported claims flagged:\n\n" + "\n".join(f"- {x}" for x in _unsupported))
                if _fabricated:
                    st.warning("Fabricated metrics flagged:\n\n" + "\n".join(f"- {x}" for x in _fabricated))
                st.caption(
                    "Note: the fidelity reviewer is tailoring-tuned; some of its "
                    "checks (length budget, impact rationale, strategy summary) "
                    "apply less cleanly to clinic rewrites. A clinic-tuned "
                    "fidelity prompt is a documented fast-follow."
                )

            # ── Decision controls ────────────────────────────────────────────
            st.markdown("---")
            st.subheader("Decision")
            _decision_now = (review or {}).get("decision")
            if _decision_now:
                st.caption(f"Decision on record: **{_decision_now}** at {review.get('decided_at') or '—'}")
            _dc1, _dc2, _dc3 = st.columns(3)
            _clinic_id = review.get("clinic_id")
            if _dc1.button("✅ Approve", key="rc_dec_approve", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "approve")
                    st.session_state.rc_last_review = _updated
                    st.success("Approved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")
            if _dc2.button("✏ Edit / send revise", key="rc_dec_revise", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "revise")
                    st.session_state.rc_last_review = _updated
                    st.info("Marked for revision.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")
            if _dc3.button("❌ Reject", key="rc_dec_reject", use_container_width=True):
                try:
                    _updated = api.submit_resume_clinic_decision(_clinic_id, "reject")
                    st.session_state.rc_last_review = _updated
                    st.warning("Rejected.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not record decision: {exc}")

            # ── Refine with feedback (ADR-068) ───────────────────────────────
            st.markdown("---")
            st.subheader("Refine with feedback")
            st.caption(
                "Iteratively revise the overhaul through chat. Each turn updates "
                "the preview in place; click **Save final edit** when you're done "
                "to lock the result in as your final draft, or **Discard chat edits** "
                "to revert to the agent's original overhaul."
            )

            # Live preview of the current state. compose_resume reads the
            # edited overhaul whenever populated (per ADR-068), so the preview
            # reflects the latest chat turn automatically.
            _rc_preview_review = st.session_state.get("rc_last_review") or {}
            _rc_overhaul = _rc_preview_review.get("overhaul")
            _rc_edited = _rc_preview_review.get("edited")
            _rc_decision = _rc_preview_review.get("decision")
            _rc_resume_id = _rc_preview_review.get("resume_id")

            # Fetch the parsed profile fresh from the DB so we render against
            # the same data the backend chat agent saw. Falls back to an
            # empty dict if the resume can't be loaded (the chat will still
            # work but the preview may be sparse).
            _rc_profile_dict: dict = {}
            if _rc_resume_id:
                try:
                    import sqlite3
                    import json as _json
                    from app.ui.db_reader import DB_PATH as _DBP
                    _db = sqlite3.connect(str(_DBP))
                    _db.row_factory = sqlite3.Row
                    _row = _db.execute(
                        "SELECT parsed_profile_json FROM resumes WHERE id = ?",
                        (_rc_resume_id,),
                    ).fetchone()
                    if _row and _row["parsed_profile_json"]:
                        _rc_profile_dict = _json.loads(_row["parsed_profile_json"])
                    _db.close()
                except Exception:
                    _rc_profile_dict = {}

            from app.services.resume_text_renderer import compose_resume as _compose_resume, render_markdown as _render_markdown
            try:
                _rc_rendered = _compose_resume(
                    _rc_profile_dict, _rc_overhaul, _rc_edited, _rc_decision,
                )
                _rc_markdown = _render_markdown(_rc_rendered)
            except Exception as _e:
                _rc_markdown = f"_Preview unavailable: {_e}_"

            with st.expander("Live preview", expanded=True):
                st.markdown(_rc_markdown)

            # ── Session cost meter (ADR-068) ────────────────────────────────
            # Surfaces `turns_used / max_turns` and `session_cost_usd` returned
            # by the last /chat call. Stays sticky between turns so the user
            # sees their remaining budget before sending the next message.
            _rc_cost_key = f"rc_chat_cost_{_clinic_id}"
            _rc_cost = st.session_state.get(_rc_cost_key)
            if _rc_cost:
                _turns_used = int(_rc_cost.get("turns_used") or 0)
                _max_turns = int(_rc_cost.get("max_turns") or 0)
                _sess_cost = float(_rc_cost.get("session_cost_usd") or 0.0)
                _pct = (_turns_used / _max_turns) if _max_turns else 0.0
                _cm1, _cm2 = st.columns([3, 2])
                _cm1.progress(min(_pct, 1.0),
                              text=f"Chat turns: {_turns_used} / {_max_turns}")
                _cm2.metric("Session cost", f"${_sess_cost:.4f}")
                if _pct >= 0.95:
                    st.error(
                        f"You've used {_turns_used} of {_max_turns} chat turns. "
                        "The next turn may be blocked - approve / edit your current "
                        "draft, or start a new clinic for more iterations."
                    )
                elif _pct >= 0.75:
                    st.warning(
                        f"You've used {_turns_used} of {_max_turns} chat turns. "
                        "Consider locking in your edit soon."
                    )

            # ── Chat input ──────────────────────────────────────────────────
            _rc_section_options = {
                "whole": "Whole resume",
                "headline": "Headline",
                "summary": "Summary",
                "experience": "Experience",
                "skills": "Skills",
                "education": "Education",
                "certifications": "Certifications",
            }
            _rc_section = st.selectbox(
                "Section to focus on",
                options=list(_rc_section_options.keys()),
                format_func=lambda k: _rc_section_options[k],
                key=f"rc_chat_section_{_clinic_id}",
            )
            _rc_message = st.text_area(
                "What would you like to change?",
                placeholder=(
                    "e.g. \"make the summary shorter and front-load the "
                    "cybersecurity angle\" or \"promote my projects above experience\""
                ),
                key=f"rc_chat_msg_{_clinic_id}",
                height=80,
            )

            _rc_chat_history_key = f"rc_chat_history_{_clinic_id}"
            if _rc_chat_history_key not in st.session_state:
                st.session_state[_rc_chat_history_key] = []

            _cc1, _cc2, _cc3 = st.columns([2, 2, 2])
            if _cc1.button("Send feedback", type="primary",
                           disabled=not _rc_message.strip(),
                           key=f"rc_chat_send_{_clinic_id}",
                           use_container_width=True):
                try:
                    with st.spinner("Revising…"):
                        _chat_resp = api.chat_resume_clinic(
                            _clinic_id,
                            _rc_message.strip(),
                            section=_rc_section,
                            history=st.session_state[_rc_chat_history_key],
                        )
                    # Append to in-session history.
                    st.session_state[_rc_chat_history_key].append(
                        {"role": "user", "message": _rc_message.strip()},
                    )
                    st.session_state[_rc_chat_history_key].append(
                        {"role": "assistant",
                         "message": _chat_resp.get("reply") or ""},
                    )
                    # Stash the cost meter fields so they survive the rerun.
                    st.session_state[_rc_cost_key] = {
                        "turns_used":      _chat_resp.get("turns_used", 0),
                        "max_turns":       _chat_resp.get("max_turns", 0),
                        "session_cost_usd": _chat_resp.get("session_cost_usd", 0.0),
                    }
                    # Refresh the clinic row so the preview re-renders.
                    try:
                        _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                        _updated = next(
                            (r for r in _rows if r.get("clinic_id") == _clinic_id),
                            None,
                        )
                        if _updated:
                            st.session_state.rc_last_review = _updated
                    except Exception:
                        pass
                    st.rerun()
                except httpx.HTTPStatusError as exc:
                    # Surface the cap-reached reason directly when the backend
                    # returns 429 (chat_turn_cap_reached) so the user doesn't
                    # see a raw HTTP error message.
                    _detail = None
                    try:
                        _detail = (exc.response.json() or {}).get("detail")
                    except Exception:
                        pass
                    if exc.response.status_code == 429:
                        st.error(
                            _detail or
                            "Chat turn cap reached for this clinic. Approve / edit "
                            "your current draft, or start a new clinic."
                        )
                    else:
                        st.error(f"Chat turn failed: {_detail or exc}")
                except Exception as exc:
                    st.error(f"Chat turn failed: {exc}")

            if _cc2.button("✓ Save final edit",
                           key=f"rc_chat_save_{_clinic_id}",
                           use_container_width=True,
                           help="Lock the current chat-edited state as your final draft (decision = edit)."):
                try:
                    _edited_payload = _rc_edited or _rc_overhaul or {}
                    _updated = api.submit_resume_clinic_decision(
                        _clinic_id, "edit", edited=_edited_payload,
                    )
                    st.session_state.rc_last_review = _updated
                    st.success("Saved as final edit.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Save failed: {exc}")

            if _cc3.button("↺ Discard chat edits",
                           key=f"rc_chat_discard_{_clinic_id}",
                           use_container_width=True,
                           help="Clear chat edits and decision; revert to the agent's original overhaul."):
                try:
                    api.discard_resume_clinic_edits(_clinic_id)
                    # Refresh from server.
                    _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                    _updated = next(
                        (r for r in _rows if r.get("clinic_id") == _clinic_id),
                        None,
                    )
                    if _updated:
                        st.session_state.rc_last_review = _updated
                    st.session_state[_rc_chat_history_key] = []
                    # Discard only clears edits on the server; the chat-turn
                    # spend on the workflow_run_id is permanent (it's already
                    # billed in llm_calls). Leave the meter intact so the user
                    # sees the true session cost.
                    st.info("Chat edits discarded.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Discard failed: {exc}")

            # ── Conversation log ────────────────────────────────────────────
            _hist = st.session_state.get(_rc_chat_history_key) or []
            if _hist:
                with st.expander(f"Conversation ({len(_hist) // 2} turn(s))", expanded=False):
                    for _msg in _hist:
                        _role = _msg.get("role", "")
                        _text = _msg.get("message", "")
                        if _role == "user":
                            st.markdown(f"**You:** {_text}")
                        else:
                            st.markdown(f"**Agent:** _{_text}_")

            # ── Export the final resume ──────────────────────────────────────
            st.markdown("---")
            st.subheader("Export the final resume")
            st.caption(
                "Decision-aware: approve uses the agent's overhaul, edit uses "
                "your draft, reject renders your original resume. No decision "
                "shows a preview-banner copy."
            )
            _fmt_labels = {
                "md":   "Markdown (.md)",
                "txt":  "Plain text (.txt)",
                "html": "HTML (.html)",
                "json": "JSON Resume (.json)",
                "docx": "Word (.docx)",
                "pdf":  "PDF (.pdf)",
            }
            _fmt = st.selectbox(
                "Format",
                options=list(_fmt_labels.keys()),
                format_func=lambda f: _fmt_labels[f],
                key="rc_export_format",
            )
            try:
                _bytes, _ctype, _fname = api.export_resume_clinic(_clinic_id, _fmt)
            except Exception as exc:
                _bytes, _ctype, _fname = None, None, None
                st.error(f"Could not generate export: {exc}")
            if _bytes is not None:
                # Inline preview for the text-y formats so the user sees what
                # will be downloaded before clicking.
                if _fmt in ("md", "txt"):
                    with st.expander("Preview", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language=("markdown" if _fmt == "md" else None))
                elif _fmt == "json":
                    with st.expander("Preview", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language="json")
                elif _fmt == "html":
                    with st.expander("Preview (raw HTML)", expanded=False):
                        st.code(_bytes.decode("utf-8", errors="replace"), language="html")
                st.download_button(
                    label=f"⬇ Download {_fmt_labels[_fmt]}",
                    data=_bytes,
                    file_name=_fname or f"resume_clinic_{_clinic_id[:8]}.{_fmt}",
                    mime=_ctype or "application/octet-stream",
                    use_container_width=True,
                    key=f"rc_dl_{_clinic_id}_{_fmt}",
                )

    # ── Past runs ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Past clinic runs")
    try:
        past = load_user_clinic_reviews(user_id)
    except Exception:
        past = None
    if past is None or past.empty:
        st.caption("No past clinic runs for this profile yet.")
    else:
        for _, _row in past.iterrows():
            _label_bits = [
                _fmt_ts(_row.get("created_at")),
                _row.get("target_role") or "no target",
                _row.get("target_track") or "—",
                (_row.get("decision") or "no decision"),
            ]
            with st.expander(" · ".join(_label_bits)):
                st.caption(f"clinic_id `{_row.get('clinic_id')}`  ·  resume `{_row.get('resume_id')}`")
                if st.button("Load into results pane", key=f"rc_load_{_row.get('clinic_id')}"):
                    try:
                        _rows = api.list_resume_clinic_runs(user_id).get("reviews") or []
                        _target = next((r for r in _rows if r.get("clinic_id") == _row.get("clinic_id")), None)
                        if _target:
                            st.session_state.rc_last_review = _target
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Could not load past run: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Settings":
    st.header("Settings")

    cfg = _get_config_cached()
    eff = cfg.get("effective_config", {}) or {}
    protected = set(cfg.get("protected_keys", []) or [])
    if cfg.get("_offline_reason"):
        st.warning(f"Backend not reachable — read-only fallback. ({cfg['_offline_reason']})")

    st.caption(
        "Edit the values below to update your defaults. Protected keys (LLM models, "
        "execution limits, retention windows) are read-only and live in `config/config.yaml`."
    )

    def _save(key: str, value: object) -> None:
        """Persist via PUT /config, then POST /config/reload so the backend
        picks up the change without a manual restart (ADR-053 addendum)."""
        try:
            api.put_config(key, value)
            st.session_state.config_cache = None
        except Exception as exc:
            st.error(f"Save failed for `{key}`: {exc}")
            return
        # Reload the backend so the change is live for the next workflow run.
        # Per-agent assignment changes especially need this — ModelRegistry
        # caches one provider per (provider, model) at startup.
        try:
            reload_result = api.reload_config()
            if key.startswith("agents."):
                # Surface the new effective assignment so the user can confirm
                # the agent now points at the chosen model.
                assignment = (reload_result or {}).get("agent_assignment") or {}
                # Extract the agent_name from "agents.{name}..." for the toast.
                parts = key.split(".")
                if len(parts) >= 2:
                    a = parts[1]
                    if a in assignment:
                        m = assignment[a]
                        st.success(
                            f"Saved `{key}` and applied. "
                            f"Active: **{a}** → `{m['provider']}/{m['model']}`"
                        )
                        return
            st.success(f"Saved `{key}` and applied (no restart needed).")
        except Exception as exc:
            st.warning(
                f"Saved `{key}` but the live reload failed: {exc}. "
                "Restart `uvicorn` to apply the change."
            )

    # ── Search ─────────────────────────────────────────────────────────────
    st.subheader("Search")
    search = (eff.get("search") or {}).copy()

    titles_str = st.text_area(
        "search.titles (comma-separated)",
        value=", ".join(search.get("titles", [])),
        height=80,
    )
    if st.button("Save titles"):
        _save("search.titles",
              [t.strip() for t in titles_str.split(",") if t.strip()])

    locations_str = st.text_area(
        "search.locations (comma-separated)",
        value=", ".join(search.get("locations", [])),
        height=60,
    )
    if st.button("Save locations"):
        _save("search.locations",
              [l.strip() for l in locations_str.split(",") if l.strip()])

    max_discovered = st.number_input(
        "search.max_discovered (manual-mode discovery net width)",
        min_value=1, max_value=50,
        value=int(search.get("max_discovered", 50)),
        help="ADR-061: how many jobs to surface for triage when manual selection "
             "is on. Default 50, ceiling 50. Ignored in auto mode.",
    )
    if st.button("Save max_discovered"):
        _save("search.max_discovered", int(max_discovered))

    # ── Scoring ────────────────────────────────────────────────────────────
    st.subheader("Scoring")
    scoring = (eff.get("scoring") or {}).copy()
    threshold = st.slider(
        "scoring.min_match_score (any track ≥ this triggers deep review)",
        min_value=0, max_value=100,
        value=int(scoring.get("min_match_score", 75)),
        step=5,
    )
    if st.button("Save min_match_score"):
        _save("scoring.min_match_score", int(threshold))

    max_scored = st.number_input(
        "scoring.max_scored (how many jobs get research + scoring)",
        min_value=1, max_value=25,
        value=int(scoring.get("max_scored", 10)),
        help="ADR-061: the funnel's scored width. Default 10, ceiling 25. In auto "
             "mode this is also the discovery cap; runs can override it.",
    )
    if st.button("Save max_scored"):
        _save("scoring.max_scored", int(max_scored))

    manual_selection_default = st.checkbox(
        "scoring.manual_selection (review discovered jobs before paying to score them)",
        value=bool(scoring.get("manual_selection", False)),
        help="ADR-060: when on, discovery casts a wider net and runs park at a "
             "selection screen so you choose which jobs are worth the research + "
             "scoring spend. This sets the default; each run can still override it "
             "on the Start New Run form.",
    )
    if st.button("Save manual_selection"):
        _save("scoring.manual_selection", bool(manual_selection_default))

    # ── Agent Models (per ADR-053) ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Agent Models")
    st.caption(
        "Pick a provider and model per agent. Indicative cost shown per million tokens. "
        "Saves trigger a live reload of the backend's agent bindings — no manual "
        "restart needed for runtime overrides. In-flight workflows keep their "
        "original assignment; only NEW workflows pick up the change."
    )

    with st.spinner("Loading provider catalog…"):
        providers_payload = _cached_get_providers()
    if providers_payload is None:
        st.warning("Couldn't reach `/config/providers` (backend may be down or restarting).")

    if providers_payload:
        catalog = providers_payload.get("providers", {}) or {}
        agent_assignment = providers_payload.get("agent_assignment", {}) or {}
        meta = catalog.get("_meta", {}) or {}
        high_volume_agents = set(meta.get("high_volume_agents") or [])
        high_volume_safe_models = set(meta.get("high_volume_safe_models") or [])

        if not catalog.get("openai", {}).get("available", False):
            st.info(
                "OpenAI provider is not registered (no `OPENAI_API_KEY` in `.env`). "
                "Add the key and restart the backend to enable OpenAI models."
            )

        # One row per agent; provider dropdown then a model dropdown filtered by it.
        # Iterate only over real agent names; the catalog's "_meta" key is sidecar metadata.
        for agent_name in sorted(a for a in agent_assignment.keys() if not a.startswith("_")):
            assignment = agent_assignment[agent_name]
            current_provider = assignment.get("provider", "claude")
            current_model = assignment.get("model", "")
            cost_capped = agent_name in high_volume_agents

            with st.expander(
                f"`{agent_name}`  ·  current: **{current_provider}** / `{current_model}`"
                + ("  ·  💰 cost-capped" if cost_capped else ""),
                expanded=False,
            ):
                if cost_capped:
                    st.caption(
                        "**Cost-capped agent.** This agent runs on every job (10-20 "
                        "calls per workflow), so its model is restricted to the "
                        f"cheapest tier: `{', '.join(sorted(high_volume_safe_models))}`. "
                        "Cost here is a design decision; expensive models are "
                        "reserved for low-volume, user-facing agents."
                    )

                # Provider options — only show those the server reports as available.
                # For cost-capped agents, also restrict to providers that have at
                # least one allowed model.
                def _has_allowed_model(provider_id: str) -> bool:
                    if not cost_capped:
                        return True
                    return any(
                        m["id"] in high_volume_safe_models
                        for m in (catalog.get(provider_id, {}).get("models") or [])
                    )

                provider_options = [
                    p for p, info in catalog.items()
                    if not p.startswith("_")
                    and (info.get("available", False) or p == current_provider)
                    and _has_allowed_model(p)
                ]
                provider_choice = st.selectbox(
                    "Provider",
                    options=provider_options,
                    index=provider_options.index(current_provider) if current_provider in provider_options else 0,
                    key=f"prov_{agent_name}",
                )

                # Model options for the chosen provider, filtered by cost cap.
                model_entries = catalog.get(provider_choice, {}).get("models", []) or []
                if cost_capped:
                    model_entries = [m for m in model_entries if m["id"] in high_volume_safe_models]
                model_ids = [m["id"] for m in model_entries]
                model_idx = model_ids.index(current_model) if current_model in model_ids else 0
                model_choice = st.selectbox(
                    "Model",
                    options=model_ids,
                    index=model_idx if model_ids else 0,
                    format_func=lambda mid: _label_with_cost(mid, model_entries),
                    key=f"model_{agent_name}",
                )

                if st.button("Save", key=f"save_{agent_name}"):
                    try:
                        api.put_config(f"agents.{agent_name}.provider", provider_choice)
                        api.put_config(f"agents.{agent_name}.model", model_choice)
                        st.session_state.config_cache = None
                        _cached_get_providers.clear()
                        st.success(
                            f"Saved {agent_name} → {provider_choice}/{model_choice}. "
                            "Restart the backend for it to take effect."
                        )
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    # ── Read-only protected ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Read-only (protected)")
    st.caption("These cannot be changed via the UI to prevent accidental cost spikes or instability.")
    st.json({k: _get_nested(eff, k.split(".")) for k in sorted(protected)
             if _get_nested(eff, k.split(".")) is not None}, expanded=False)

    # ── Data retention purge (ADR-070) ───────────────────────────────────────
    st.subheader("Data retention")
    st.caption(
        "Delete data past the retention windows above. A purged workflow run takes "
        "ALL its rows with it (scores, reviews, advice, prep, tailorings, clinic "
        "reviews, decisions, observability); inactive resumes are removed only once "
        "no surviving run still references them. The windows are read-only and live "
        "in `config/config.yaml`. Purge is explicit and never runs automatically."
    )
    with st.expander("Run data-retention purge"):
        st.warning(
            "This permanently deletes rows older than the retention windows. It "
            "cannot be undone. Make sure you have a backup of `data/v2.db` if you "
            "might want this data back."
        )
        confirm = st.checkbox(
            "I understand this permanently deletes data past the retention windows.",
            key="purge_confirm",
        )
        if st.button("Run purge now", type="primary", disabled=not confirm,
                     key="purge_run_btn"):
            try:
                with st.spinner("Purging..."):
                    result = api.purge_data()
            except Exception as exc:
                st.error(f"Purge failed: {exc}")
            else:
                deleted = {t: n for t, n in (result or {}).items() if n}
                total = sum(deleted.values())
                if total:
                    st.success(f"Purged {total} rows.")
                    st.json(deleted, expanded=True)
                else:
                    st.info("Nothing was past the retention windows; no rows deleted.")
                # The history/cost views read from the DB directly — invalidate any
                # cached config so a re-render reflects the smaller dataset.
                st.session_state.config_cache = None


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-RUN ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# PROFILES — onboarding wizard (ADR-062)
# ══════════════════════════════════════════════════════════════════════════════

elif view == "Profiles":
    st.header("Profiles")
    st.caption(
        "A profile is one job-seeker served from this install. Each has its own "
        "resume, search defaults, config, memory, cost view, and history. There "
        "is no login — switching profiles in the sidebar re-scopes what you see."
    )

    # Existing profiles
    _users = _cached_list_users()
    if _users:
        st.subheader("Existing profiles")
        st.dataframe(
            pd.DataFrame([
                {"ID": u["id"], "Name": u["name"], "Note": u.get("note") or "",
                 "Created": _fmt_ts(u.get("created_at"))}
                for u in _users
            ]),
            hide_index=True,
            use_container_width=True,
        )

        # ── Manage an existing profile ────────────────────────────────────────
        st.subheader("Manage an existing profile")
        _opts = {str(u["id"]): f"{u['name']}  (#{u['id']})" for u in _users}
        _by_id = {str(u["id"]): u for u in _users}

        with st.expander("Edit a profile (name / note)"):
            _eid = st.selectbox(
                "Profile to edit", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="edit_profile_select",
            )
            _cur = _by_id.get(_eid, {})
            _new_name = st.text_input("Display name", value=_cur.get("name") or "",
                                      key=f"edit_name_{_eid}")
            _new_note = st.text_input("Note (optional)", value=_cur.get("note") or "",
                                      key=f"edit_note_{_eid}")
            if st.button("Save changes", key=f"edit_save_{_eid}"):
                if not _new_name.strip():
                    st.error("Display name is required.")
                else:
                    try:
                        api.update_user(_eid, _new_name.strip(), _new_note.strip() or None)
                        _cached_list_users.clear()
                        st.success(f"Updated profile #{_eid}.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not update profile: {exc}")

        with st.expander("Add a resume to a profile"):
            _rid = st.selectbox(
                "Profile", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="add_resume_select",
            )
            _rfile = st.file_uploader("Resume PDF", type=["pdf"],
                                      key=f"add_resume_file_{_rid}")
            if st.button("Upload resume", type="primary",
                         disabled=_rfile is None, key=f"add_resume_btn_{_rid}"):
                try:
                    with st.spinner("Parsing resume (this can take a moment)…"):
                        resp = api.upload_resume(_rid, _rfile.getvalue(), _rfile.name)
                    st.success(f"Stored resume `{resp.get('resume_id')}` as the active "
                               f"resume for profile #{_rid}.")
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Resume upload failed: {exc}")

        with st.expander("Delete a resume from a profile"):
            st.caption(
                "Removes a resume from the profile and cascades to its Resume "
                "Clinic reviews (the past-runs panel would otherwise show "
                "broken rows). Job-search workflow history is preserved. "
                "Re-upload to get a fresh parse under the latest parser prompt."
            )
            _dprof = st.selectbox(
                "Profile", list(_opts.keys()),
                format_func=lambda i: _opts.get(i, i), key="del_resume_profile",
            )
            _dprof_resumes = load_user_resumes(_dprof)
            if _dprof_resumes.empty:
                st.info("This profile has no resumes to delete.")
            else:
                _del_options = {}
                for _, _r in _dprof_resumes.iterrows():
                    _flag = " (active)" if int(_r.get("is_active") or 0) else ""
                    _del_options[str(_r["resume_id"])] = (
                        f"{_r.get('file_name') or _r['resume_id']}  ·  "
                        f"v{_r.get('version') or '?'}{_flag}  ·  "
                        f"{_fmt_ts(_r.get('created_at'))}"
                    )
                _dres = st.selectbox(
                    "Resume to delete",
                    options=list(_del_options.keys()),
                    format_func=lambda i: _del_options[i],
                    key=f"del_resume_select_{_dprof}",
                )
                # Count clinic reviews that would cascade so the user sees the
                # full impact before confirming.
                try:
                    _clinic_rows = api.list_resume_clinic_runs(_dprof).get("reviews") or []
                    _cascade_count = sum(
                        1 for r in _clinic_rows if r.get("resume_id") == _dres
                    )
                except Exception:
                    _cascade_count = 0
                _confirm = st.checkbox(
                    f"Yes — also delete this resume's **{_cascade_count}** Resume "
                    f"Clinic review(s). Job-search history stays.",
                    key=f"del_resume_confirm_{_dprof}_{_dres}",
                )
                if st.button(
                    "Delete resume",
                    type="primary",
                    disabled=not _confirm,
                    key=f"del_resume_btn_{_dprof}_{_dres}",
                ):
                    try:
                        resp = api.delete_resume(_dprof, _dres)
                        st.success(
                            f"Deleted resume `{_dres[:8]}…` and "
                            f"{resp.get('clinic_reviews_deleted', 0)} clinic review(s)."
                        )
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")

    st.markdown("---")
    st.subheader("Add a profile")

    step = st.session_state.onboard_step

    # ── Step 1: identity ──────────────────────────────────────────────────────
    if step == 1:
        st.markdown("**Step 1 of 3 — Identity**")
        with st.form("onboard_identity"):
            name = st.text_input("Display name", placeholder="e.g. Alex (son)")
            note = st.text_input(
                "Note (optional)",
                placeholder="e.g. New-grad SWE, west coast — human-only label",
                help="Descriptive only. The system never acts on this.",
            )
            go = st.form_submit_button("Create profile", type="primary")
        if go:
            if not name.strip():
                st.error("A display name is required.")
            else:
                try:
                    resp = api.create_user(name.strip(), note.strip() or None)
                    new = resp.get("user") or {}
                    st.session_state.onboard_new_user_id = new.get("id")
                    st.session_state.onboard_step = 2
                    _cached_list_users.clear()
                    st.success(f"Created profile #{new.get('id')} — {new.get('name')}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not create profile: {exc}")

    # ── Step 2: resume ────────────────────────────────────────────────────────
    elif step == 2:
        new_uid = st.session_state.onboard_new_user_id
        st.markdown(f"**Step 2 of 3 — Resume** (profile #{new_uid})")
        st.caption("Upload a PDF resume for this profile. It becomes the profile's "
                   "active resume. You can skip and add one later.")
        up = st.file_uploader("Resume PDF", type=["pdf"], key="onboard_resume_file")
        c1, c2 = st.columns(2)
        if c1.button("Upload and continue", type="primary", disabled=up is None):
            try:
                with st.spinner("Parsing resume (this can take a moment)…"):
                    resp = api.upload_resume(new_uid, up.getvalue(), up.name)
                st.success(f"Stored resume `{resp.get('resume_id')}` for "
                           f"{resp.get('name') or 'this profile'}.")
                st.session_state.onboard_step = 3
                st.rerun()
            except Exception as exc:
                st.error(f"Resume upload failed: {exc}")
        if c2.button("Skip for now"):
            st.session_state.onboard_step = 3
            st.rerun()

    # ── Step 3: default search criteria ───────────────────────────────────────
    elif step == 3:
        new_uid = st.session_state.onboard_new_user_id
        st.markdown(f"**Step 3 of 3 — Default search criteria** (profile #{new_uid})")
        st.caption("Saved as this profile's defaults; Start New Run pre-fills from "
                   "them. Skippable — you can set them later in Settings.")
        with st.form("onboard_search"):
            roles = st.text_input("Roles (comma-separated)",
                                  placeholder="Security Analyst, SOC Analyst")
            locations = st.text_area("Locations (one per line)",
                                     placeholder="Atlanta, GA\nRemote", height=90,
                                     help="One per line so 'City, State' stays intact.")
            cs1, cs2 = st.columns(2)
            save = cs1.form_submit_button("Save and finish", type="primary")
            skip = cs2.form_submit_button("Skip and finish")
        if save or skip:
            if save:
                # Persist as the NEW profile's user_config defaults. Temporarily
                # point the client at that profile so the writes are owned by it,
                # then restore the active profile.
                _prev = st.session_state.current_user_id
                try:
                    api.set_user_id(str(new_uid))
                    role_list = [r.strip() for r in roles.split(",") if r.strip()]
                    loc_list = [l.strip() for l in locations.splitlines() if l.strip()]
                    if role_list:
                        api.put_config("search.titles", role_list)
                    if loc_list:
                        api.put_config("search.locations", loc_list)
                except Exception as exc:
                    st.warning(f"Could not save search defaults: {exc}")
                finally:
                    api.set_user_id(_prev)
            st.session_state.onboard_step = 1
            st.success(f"Profile #{new_uid} is ready. Select it in the sidebar to use it.")
            _cached_list_users.clear()

    if step != 1:
        if st.button("Cancel / start over"):
            st.session_state.onboard_step = 1
            st.session_state.onboard_new_user_id = None
            st.rerun()


# ── Cost Dashboard — system-wide spend visibility ────────────────────────────
# Mirrors the queries in docs/cost_troubleshooting.md so the UI surfaces the
# same numbers a developer would compute by hand. Click-through into Workflow
# Detail keeps drill-in one click away.

elif view == "Cost Dashboard":
    st.header("💰 Cost Dashboard")
    st.caption("System-wide LLM spend across all runs. The numbers here come from the "
               "`llm_calls` audit table. See `docs/cost_troubleshooting.md` for the "
               "lever decision matrix and reconciliation guide.")

    wc1, wc2 = st.columns([3, 2])
    with wc1:
        window_choice = st.radio(
            "Time window",
            ["Last 7 days", "Last 30 days", "All time"],
            horizontal=True, label_visibility="collapsed",
        )
    with wc2:
        # ADR-062: spend is attributable per profile. Default to this profile;
        # tick to see system-wide spend across every profile.
        all_profiles = st.checkbox(
            "All profiles (system-wide)", value=False,
            help="Off: only the active profile's runs. On: every profile.",
        )
    window_map = {"Last 7 days": 7, "Last 30 days": 30, "All time": None}
    window_days = window_map[window_choice]
    cost_uid = None if all_profiles else st.session_state.current_user_id

    dash = compute_dashboard_aggregate(days=window_days, user_id=cost_uid)
    totals = dash["totals"]

    if totals["calls"] == 0:
        st.info(
            f"No LLM calls recorded in the **{window_choice.lower()}** window.\n\n"
            "Either no workflows have run in this window, or observability isn't "
            "writing rows. Run `pytest tests/v2/test_cost_invariants.py -v` to verify "
            "the audit trail is intact."
        )
        st.stop()

    # ── Headline metrics ──────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Total spend", f"${totals['cost_usd']:.4f}")
    h2.metric("LLM calls", f"{totals['calls']:,}")
    h3.metric("Distinct runs", totals["distinct_runs"])
    avg_per_run = (totals["cost_usd"] / totals["distinct_runs"]) if totals["distinct_runs"] else 0.0
    h4.metric("Avg cost / run", f"${avg_per_run:.4f}")
    h5, h6 = st.columns(2)
    h5.metric("Tokens in",  f"{totals['tokens_input']:,}")
    h6.metric("Tokens out", f"{totals['tokens_output']:,}")

    # ── Cache effectiveness ───────────────────────────────────────────────────
    # Anthropic ephemeral prompt cache: writes bill 1.25x input rate, reads 0.10x.
    # A persistently low hit ratio means caching is configured but not landing —
    # the prompt is changing across calls within the 5-min cache window.
    cache_w = totals.get("cache_creation_tokens", 0)
    cache_r = totals.get("cache_read_tokens", 0)
    if cache_w or cache_r:
        st.markdown("---")
        st.subheader("⚡ Cache effectiveness")
        st.caption("Reads served from prompt cache pay 10% of the input rate; "
                   "writes pay 125%. A high read ratio is the goal.")
        c1, c2, c3 = st.columns(3)
        hit_pct = totals.get("cache_hit_ratio", 0.0) * 100
        c1.metric("Cache-hit ratio", f"{hit_pct:.1f}%",
                  help="Cache reads as a % of total billable input tokens.")
        c2.metric("Cache reads",  f"{cache_r:,}",
                  help="Tokens served from cache at 0.10x the input rate.")
        c3.metric("Cache writes", f"{cache_w:,}",
                  help="Tokens written into cache at 1.25x the input rate.")
        if hit_pct < 30 and totals["calls"] >= 10:
            st.warning(
                "Cache-hit ratio is below 30% across "
                f"{totals['calls']} calls. Either prompts are changing across "
                "calls (check that the resume profile is stable and that no "
                "per-call timestamps leak into the system message), or runs "
                "are spaced more than 5 minutes apart so the ephemeral cache "
                "expires before reuse."
            )

    # ── Daily spend trend ─────────────────────────────────────────────────────
    if window_days:
        st.markdown("---")
        st.subheader("📈 Daily spend trend")
        trend_rows = daily_spend_trend(days=window_days, user_id=cost_uid)
        if trend_rows:
            trend_df = pd.DataFrame(trend_rows)
            fig = px.line(
                trend_df, x="day", y="cost_usd", markers=True,
                labels={"day": "Day", "cost_usd": "Cost ($)"},
                title=None,
            )
            fig.update_traces(hovertemplate="<b>%{x}</b><br>$%{y:.4f}<extra></extra>")
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=260)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No daily activity in this window.")

    # ── Per-agent cost breakdown ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🤖 Per-agent cost breakdown")
    st.caption("Which agents are eating the budget. The biggest single bar is usually "
               "the right place to start cutting. Move it to a cheaper model in "
               "**Settings → Agent Models** if quality permits.")
    if dash["by_agent"]:
        ag_df = pd.DataFrame(dash["by_agent"]).sort_values("cost_usd", ascending=True)
        fig = px.bar(
            ag_df, x="cost_usd", y="agent_name", orientation="h",
            text="cost_usd",
            labels={"cost_usd": "Cost ($)", "agent_name": "Agent"},
            color="cost_usd", color_continuous_scale="oranges",
        )
        fig.update_traces(texttemplate="$%{x:.4f}", textposition="outside")
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                          height=max(220, 36 * len(ag_df)),
                          coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        # Numeric table alongside the chart
        ag_table = pd.DataFrame(dash["by_agent"])
        ag_table["share_pct"] = (
            ag_table["cost_usd"] / max(totals["cost_usd"], 1e-9) * 100
        ).round(1)
        ag_table = ag_table.rename(columns={
            "agent_name": "Agent", "calls": "Calls",
            "tokens_input": "Tokens in", "tokens_output": "Tokens out",
            "cost_usd": "Cost ($)", "share_pct": "% of total",
        })
        st.dataframe(
            ag_table, hide_index=True, use_container_width=True,
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                "% of total": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

    # ── Per-model cost breakdown ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🧠 Per-model cost breakdown")
    st.caption("Sonnet vs Haiku vs gpt-4o — same call count on different models can "
               "differ by 10-25x in cost. See the rate table in `docs/cost_troubleshooting.md` "
               "Step 6.")
    if dash["by_model"]:
        m_df = pd.DataFrame(dash["by_model"])
        m_df["model_label"] = m_df["provider"] + " / " + m_df["model"]
        # Pie for cost share, bar for call count side by side.
        c1, c2 = st.columns(2)
        with c1:
            fig_pie = px.pie(
                m_df, values="cost_usd", names="model_label",
                title="Cost share by model", hole=0.4,
            )
            fig_pie.update_traces(textinfo="percent+label",
                                  hovertemplate="<b>%{label}</b><br>$%{value:.4f}<extra></extra>")
            fig_pie.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320)
            st.plotly_chart(fig_pie, use_container_width=True)
        with c2:
            m_sorted = m_df.sort_values("calls", ascending=True)
            fig_bar = px.bar(
                m_sorted, x="calls", y="model_label", orientation="h",
                title="Call count by model",
                labels={"calls": "Calls", "model_label": "Model"},
                color="provider",
            )
            fig_bar.update_layout(margin=dict(l=10, r=10, t=40, b=10), height=320,
                                  showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True)

    # ── Top 5 most expensive runs (drill-through) ─────────────────────────────
    st.markdown("---")
    st.subheader("🔥 Top 5 most expensive runs")
    st.caption("Click a row to open that run's full detail (per-job pipeline, per-agent "
               "cost, fidelity flags).")
    runs = top_runs_by_cost(n=5, days=window_days, user_id=cost_uid)
    if runs:
        runs_df = pd.DataFrame(runs)
        runs_df["ID"] = runs_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        runs_df["Started"] = runs_df["started_at"].apply(_fmt_ts)
        runs_view = runs_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]]
        runs_view = runs_view.rename(columns={
            "calls": "Calls", "tokens_input": "Tokens in",
            "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
        })
        ev_runs = st.dataframe(
            runs_view, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row",
            key="cost_top_runs_table",
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
            },
        )
        sel = (ev_runs.selection.rows if ev_runs and getattr(ev_runs, "selection", None) else []) or []
        if sel and sel[0] < len(runs_df):
            chosen_wf = str(runs_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail",
                          detail_workflow_id=chosen_wf, detail_job_id=None)

    # ── All runs by cost (full per-run table) ─────────────────────────────────
    st.markdown("---")
    st.subheader("📋 All runs by cost")
    st.caption("Every workflow in the selected window, sourced from `llm_calls` "
               "(the truth source — see `docs/cost_troubleshooting.md` Step 4 for "
               "why this differs from `state_json` estimates). Click a row to drill in.")
    all_runs = all_runs_by_cost(days=window_days, user_id=cost_uid)
    if all_runs:
        all_df = pd.DataFrame(all_runs)
        all_df["ID"] = all_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        all_df["Started"] = all_df["started_at"].apply(_fmt_ts)
        all_view = all_df[["ID", "Started", "calls", "tokens_input", "tokens_output", "cost_usd"]]
        all_view = all_view.rename(columns={
            "calls": "Calls", "tokens_input": "Tokens in",
            "tokens_output": "Tokens out", "cost_usd": "Cost ($)",
        })
        ev_all_runs = st.dataframe(
            all_view, hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="single-row",
            key="cost_all_runs_table",
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
            },
        )
        sel = (ev_all_runs.selection.rows if ev_all_runs and getattr(ev_all_runs, "selection", None) else []) or []
        if sel and sel[0] < len(all_df):
            chosen_wf = str(all_df.iloc[sel[0]]["workflow_run_id"])
            if chosen_wf and chosen_wf != st.session_state.get("detail_workflow_id"):
                _navigate("Workflow Detail",
                          detail_workflow_id=chosen_wf, detail_job_id=None)
        st.caption(f"{len(all_runs)} run(s) in this window. Sum: ${sum(r['cost_usd'] for r in all_runs):.4f}")

    # ── Top 10 most expensive single calls ────────────────────────────────────
    st.markdown("---")
    st.subheader("⚡ Top 10 most expensive single calls")
    st.caption("Outliers worth looking at — high token count, high latency, or both. A single "
               "call >$0.10 usually means a long prompt + Sonnet/Opus combination.")
    calls = top_calls_by_cost(n=10, days=window_days, user_id=cost_uid)
    if calls:
        calls_df = pd.DataFrame(calls)
        calls_df["Run"] = calls_df["workflow_run_id"].apply(lambda s: (s[:8] + "…") if len(s) > 8 else s)
        calls_df["When"] = calls_df["created_at"].apply(_fmt_ts)
        calls_view = calls_df[[
            "When", "Run", "agent_name", "provider", "model",
            "tokens_input", "tokens_output", "cost_usd", "latency_ms",
        ]].rename(columns={
            "agent_name": "Agent", "provider": "Provider", "model": "Model",
            "tokens_input": "Tokens in", "tokens_output": "Tokens out",
            "cost_usd": "Cost ($)", "latency_ms": "Latency (ms)",
        })
        st.dataframe(
            calls_view, hide_index=True, use_container_width=True,
            column_config={
                "Cost ($)": st.column_config.NumberColumn(format="$%.4f"),
                "Latency (ms)": st.column_config.NumberColumn(format="%d ms"),
            },
        )

    # ── Reconciliation strip ──────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("🧮 Reconcile against the provider billing console", expanded=False):
        st.markdown(
            "The local total above is computed from successful calls at our local "
            "rate table. Anthropic / OpenAI bill for **all** calls including retries "
            "and may apply different rates for cached input. A 10-20% gap is normal; "
            "more than 2x means something's wrong (see "
            "`docs/cost_troubleshooting.md` Step 4).\n\n"
            f"**Local total ({window_choice.lower()}): ${totals['cost_usd']:.4f}**"
        )
        provider_total = st.number_input(
            "Provider console total for the same window (USD)",
            min_value=0.0, step=0.01, value=0.0, format="%.2f",
            help="Look this up in console.anthropic.com → Usage or "
                 "platform.openai.com → Usage and paste it here.",
        )
        if provider_total > 0:
            gap = provider_total - totals["cost_usd"]
            gap_pct = (gap / max(totals["cost_usd"], 1e-9)) * 100
            if abs(gap_pct) > 100:
                st.error(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — investigate. "
                         "Likely retries or stuck workflows; see Step 4 of the cost guide.")
            elif abs(gap_pct) > 20:
                st.warning(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — outside the normal "
                           "10-20% band. Worth a closer look.")
            else:
                st.success(f"Gap: ${gap:+.4f} ({gap_pct:+.0f}%) — within the normal range.")


elif view == "Top Matches":
    st.header("Top Matches (across all runs)")
    df = load_scored_jobs(include_excluded=include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "results will populate this view automatically.")
        st.stop()
    if search:
        mask = (
            df["title"].str.contains(search, case=False, na=False)
            | df["company"].str.contains(search, case=False, na=False)
        )
        df = df[mask]
    filtered = df[df["overall_score"] >= min_score].copy()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total scored", len(df))
    m2.metric(f"Score >= {min_score}", len(filtered))
    m3.metric("Companies", filtered["company"].nunique())
    render_track_table(filtered, "overall_score", min_score)


elif view == "IC Track":
    st.header("IC Engineering Track")
    df = load_scored_jobs(include_excluded=include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the IC track will appear here.")
        st.stop()
    render_track_table(df, "technical_score", min_score)


elif view == "Architect Track":
    st.header("Architect Track")
    df = load_scored_jobs(include_excluded=include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Architect track will appear here.")
        st.stop()
    render_track_table(df, "architecture_score", min_score)


elif view == "Management Track":
    st.header("Management Track")
    df = load_scored_jobs(include_excluded=include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "jobs scored on the Management track will appear here.")
        st.stop()
    render_track_table(df, "leadership_score", min_score)


elif view == "Companies":
    st.header("Top Target Companies")
    df = load_scored_jobs(include_excluded=include_excluded, user_id=st.session_state.current_user_id)
    if df.empty:
        st.info("No scored jobs yet. Kick off a run from **Start New Run** in the sidebar — "
                "this view aggregates the best score per company across all runs.")
        st.stop()
    agg = (
        df.groupby("company")
        .agg(
            jobs=("job_id", "count"),
            best_overall=("overall_score", "max"),
            best_technical=("technical_score", "max"),
            best_arch=("architecture_score", "max"),
            best_lead=("leadership_score", "max"),
        )
        .reset_index()
        .sort_values("best_overall", ascending=False)
    )
    agg = agg[agg["best_overall"] >= min_score]
    top = agg.head(20).sort_values("best_overall")
    fig = px.bar(
        top, x="best_overall", y="company", orientation="h",
        color="best_overall", color_continuous_scale="teal",
        labels={"best_overall": "Best Score", "company": "Company"},
        title=f"Top {len(top)} Companies by Best Match Score",
        text="best_overall",
    )
    fig.update_layout(showlegend=False, coloraxis_showscale=False, height=500)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        agg.rename(columns={
            "company": "Company", "jobs": "Roles",
            "best_overall": "Best", "best_technical": "Tech",
            "best_arch": "Arch", "best_lead": "Lead",
        }),
        hide_index=True, use_container_width=True,
    )


