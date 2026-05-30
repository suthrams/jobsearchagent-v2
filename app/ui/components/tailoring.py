"""Tailoring-draft render components and their status badges.

Phase 2 of the UI refactor (docs/architecture/ui_refactor_plan.md). Renders a
tailoring draft card: the per-track estimated impact, the per-section suggestion
diffs, the fidelity flags, and the decision controls. ``_render_tailoring_card``
is the only entry point a view calls; the rest are its internal helpers.

The card is decoupled from the backend: it takes an ``on_decision(tailoring_id,
choice[, edited_draft])`` callback supplied by the view, so this module performs
no I/O - only rendering. ``st.*`` runs only inside function bodies.
"""
from __future__ import annotations

import streamlit as st

from app.ui.formatting import (
    _estimate_track_impact,
    _fmt_ts,
    _section_display,
    _section_order,
    _word_count,
)

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
