"""Generate v2 future state architecture diagram as PNG."""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

# ── Palette ───────────────────────────────────────────────────────────────────
C_SUPERVISOR  = "#1a3a5c"   # dark navy
C_AGENT       = "#2563eb"   # blue
C_GATE        = "#d97706"   # amber
C_UTIL        = "#6b7280"   # gray
C_STATE       = "#065f46"   # dark green
C_ARROW       = "#374151"
C_BG          = "#f8fafc"
C_WHITE       = "#ffffff"
C_AGENT_LIGHT = "#dbeafe"
C_GATE_LIGHT  = "#fef3c7"
C_STATE_LIGHT = "#d1fae5"
C_UTIL_LIGHT  = "#f3f4f6"
C_SUPER_LIGHT = "#e0e7ff"

fig, ax = plt.subplots(figsize=(18, 24))
ax.set_xlim(0, 18)
ax.set_ylim(0, 24)
ax.axis("off")
fig.patch.set_facecolor(C_BG)
ax.set_facecolor(C_BG)


# ── Helpers ───────────────────────────────────────────────────────────────────
def box(ax, x, y, w, h, label, sublabel="", fill=C_AGENT_LIGHT,
        edge=C_AGENT, text_color=C_AGENT, fontsize=11, bold=True):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0.08",
                          facecolor=fill, edgecolor=edge, linewidth=2)
    ax.add_patch(rect)
    weight = "bold" if bold else "normal"
    if sublabel:
        ax.text(x + w/2, y + h*0.62, label,
                ha="center", va="center", fontsize=fontsize,
                fontweight=weight, color=text_color)
        ax.text(x + w/2, y + h*0.28, sublabel,
                ha="center", va="center", fontsize=8,
                color=text_color, alpha=0.75)
    else:
        ax.text(x + w/2, y + h/2, label,
                ha="center", va="center", fontsize=fontsize,
                fontweight=weight, color=text_color)


def gate(ax, x, y, w, h, label):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0.08",
                          facecolor=C_GATE_LIGHT, edgecolor=C_GATE, linewidth=2.5,
                          linestyle="--")
    ax.add_patch(rect)
    ax.text(x + w/2, y + h*0.65, "⬡  APPROVAL GATE",
            ha="center", va="center", fontsize=7.5,
            fontweight="bold", color=C_GATE)
    ax.text(x + w/2, y + h*0.3, label,
            ha="center", va="center", fontsize=9,
            color=C_GATE, style="italic")


def arrow(ax, x1, y1, x2, y2, label=""):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                lw=1.8, mutation_scale=18))
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx+0.15, my, label, fontsize=7.5, color=C_ARROW, style="italic")


def section_label(ax, x, y, text):
    ax.text(x, y, text, fontsize=8, color="#9ca3af",
            fontweight="bold", ha="left", va="center")


# ═══════════════════════════════════════════════════════════════════════════════
# TITLE
# ═══════════════════════════════════════════════════════════════════════════════
ax.text(9, 23.4, "JobSearchAgent v2 — Future State Architecture",
        ha="center", va="center", fontsize=16, fontweight="bold", color=C_SUPERVISOR)
ax.text(9, 23.0, "LangGraph State Machine  ·  9 Agents  ·  4 Approval Gates",
        ha="center", va="center", fontsize=10, color="#6b7280")

# ═══════════════════════════════════════════════════════════════════════════════
# SUPERVISOR
# ═══════════════════════════════════════════════════════════════════════════════
box(ax, 5.5, 21.8, 7, 0.9,
    "SUPERVISOR AGENT",
    "Orchestrates · Routes · Manages Gates",
    fill=C_SUPER_LIGHT, edge=C_SUPERVISOR,
    text_color=C_SUPERVISOR, fontsize=12)

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 1 — Search · Profile · Memory
# ═══════════════════════════════════════════════════════════════════════════════
section_label(ax, 0.3, 20.9, "INPUTS")

box(ax, 0.5, 20.2, 4.5, 1.2,
    "SEARCH AGENT",
    "LinkedIn · Adzuna · Ladders · extensible",
    fontsize=10)

box(ax, 6.75, 20.2, 4.5, 1.2,
    "PROFILE AGENT",
    "IC resume · Architect resume · Mgmt resume",
    fontsize=10)

box(ax, 13.0, 20.2, 4.5, 1.2,
    "MEMORY AGENT",
    "Episodic store · Past outcomes · Patterns",
    fontsize=10)

# arrows: supervisor → row1
arrow(ax, 9.0, 21.8, 2.75, 21.4)
arrow(ax, 9.0, 21.8, 9.0,  21.4)
arrow(ax, 9.0, 21.8, 15.25,21.4)

# row1 → merge down
arrow(ax, 2.75, 20.2, 8.5, 19.65)
arrow(ax, 9.0,  20.2, 9.0, 19.65)
arrow(ax, 15.25,20.2, 9.5, 19.65)

# ═══════════════════════════════════════════════════════════════════════════════
# GATE 1
# ═══════════════════════════════════════════════════════════════════════════════
gate(ax, 5.5, 18.9, 7, 0.75, "Cost review · Confirm before scoring")
arrow(ax, 9.0, 19.65, 9.0, 19.65)  # dummy — merge arrow handled above
ax.annotate("", xy=(9.0, 18.9), xytext=(9.0, 19.9),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.8, mutation_scale=18))

# ═══════════════════════════════════════════════════════════════════════════════
# SCORING AGENT
# ═══════════════════════════════════════════════════════════════════════════════
section_label(ax, 0.3, 18.55, "CORE PIPELINE")
box(ax, 5.5, 17.6, 7, 1.0,
    "SCORING AGENT",
    "Selects resume by track · Batch parallel · Haiku model",
    fontsize=10)
arrow(ax, 9.0, 18.9, 9.0, 18.6)

# ═══════════════════════════════════════════════════════════════════════════════
# GATE 2
# ═══════════════════════════════════════════════════════════════════════════════
gate(ax, 5.5, 16.8, 7, 0.75, "Score review · Approve 80%+ for research")
arrow(ax, 9.0, 17.6, 9.0, 17.55)

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 2 — Strategy · Research · Skills Gap
# ═══════════════════════════════════════════════════════════════════════════════
section_label(ax, 0.3, 16.45, "INTELLIGENCE LAYER")

box(ax, 0.5, 14.9, 4.8, 1.6,
    "STRATEGY AGENT",
    "ReAct loop · Analyzes scored jobs\nPlans next steps · Surfaces insights",
    fontsize=10)

box(ax, 6.6, 14.9, 4.8, 1.6,
    "RESEARCH AGENT",
    "Company background · Recent news\nCulture signals · Red flag detector",
    fontsize=10)

box(ax, 12.7, 14.9, 4.8, 1.6,
    "SKILLS GAP AGENT",
    "Job reqs vs profile\nResume edit suggestions\nPer-job score improvement",
    fontsize=10)

# Gate 2 → row2
arrow(ax, 9.0, 16.8, 2.9,  16.5)
arrow(ax, 9.0, 16.8, 9.0,  16.5)
arrow(ax, 9.0, 16.8, 15.1, 16.5)

ax.annotate("", xy=(2.9,  16.5), xytext=(2.9,  16.8),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.5, mutation_scale=15))
ax.annotate("", xy=(9.0,  16.5), xytext=(9.0,  16.8),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.5, mutation_scale=15))
ax.annotate("", xy=(15.1, 16.5), xytext=(15.1, 16.8),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.5, mutation_scale=15))

# row2 → gate3
arrow(ax, 2.9,  14.9, 8.0, 14.4)
arrow(ax, 9.0,  14.9, 9.0, 14.4)
arrow(ax, 15.1, 14.9, 10.0,14.4)

# ═══════════════════════════════════════════════════════════════════════════════
# GATE 3
# ═══════════════════════════════════════════════════════════════════════════════
gate(ax, 5.5, 13.55, 7, 0.75, "Research review · Choose which jobs to pursue")
ax.annotate("", xy=(9.0, 13.55), xytext=(9.0, 14.4),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.8, mutation_scale=18))

# ═══════════════════════════════════════════════════════════════════════════════
# TAILORING AGENT
# ═══════════════════════════════════════════════════════════════════════════════
section_label(ax, 0.3, 13.2, "ACTION LAYER  (ad-hoc, on demand)")

box(ax, 3.5, 11.9, 5.5, 1.4,
    "TAILORING AGENT",
    "Resume per track · Ad-hoc on demand\nEvaluator-Optimizer critique loop",
    fontsize=10)

# Status Manager (utility, not agent)
rect_util = FancyBboxPatch((9.5, 11.9), 5.0, 1.4,
                            boxstyle="round,pad=0.08",
                            facecolor=C_UTIL_LIGHT, edgecolor=C_UTIL,
                            linewidth=1.5, linestyle=":")
ax.add_patch(rect_util)
ax.text(14.0, 12.87, "STATUS MANAGER",
        ha="center", va="center", fontsize=10,
        fontweight="bold", color=C_UTIL)
ax.text(14.0, 12.47, "Mark as APPLIED (with or without tailoring)\nUpdate stage · Log dates",
        ha="center", va="center", fontsize=8, color=C_UTIL)
ax.text(14.0, 12.1, "── not an AI agent ──",
        ha="center", va="center", fontsize=7.5, color=C_UTIL, style="italic")

arrow(ax, 9.0, 13.55, 6.25, 13.3)
arrow(ax, 9.0, 13.55, 12.0, 13.3)

ax.annotate("", xy=(6.25, 13.3), xytext=(6.25, 13.55),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.5, mutation_scale=15))
ax.annotate("", xy=(12.0, 13.3), xytext=(12.0, 13.55),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.5, mutation_scale=15))

# ═══════════════════════════════════════════════════════════════════════════════
# GATE 4
# ═══════════════════════════════════════════════════════════════════════════════
gate(ax, 5.5, 10.95, 7, 0.75, "Review tailored materials · Mark as APPLIED")

arrow(ax, 6.25, 11.9, 8.5,  11.7)
arrow(ax, 12.0, 11.9, 9.5,  11.7)
ax.annotate("", xy=(9.0, 10.95), xytext=(9.0, 11.7),
            arrowprops=dict(arrowstyle="-|>", color=C_ARROW, lw=1.8, mutation_scale=18))

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED GRAPH STATE
# ═══════════════════════════════════════════════════════════════════════════════
section_label(ax, 0.3, 10.5, "SHARED GRAPH STATE  (LangGraph — flows through every node)")

state_rect = FancyBboxPatch((0.5, 8.2), 17, 2.0,
                             boxstyle="round,pad=0.1",
                             facecolor=C_STATE_LIGHT, edgecolor=C_STATE,
                             linewidth=2)
ax.add_patch(state_rect)

state_fields = [
    ("profiles",          "dict[CareerTrack → Profile]"),
    ("raw_jobs / scored_jobs", "list[Job]"),
    ("shortlisted_jobs",  "list[Job]  — 80%+ approved"),
    ("research",          "dict[job_id → CompanyResearch]"),
    ("skill_gaps",        "dict[job_id → GapAnalysis]"),
    ("strategy",          "StrategyPlan  — ReAct output"),
    ("tailored_resumes",  "dict[job_id → TailoredResume]"),
    ("episodic_context",  "EpisodicMemory  — cross-run"),
    ("human_decisions",   "dict[gate_id → approved]"),
    ("run_metrics",       "cost · tokens · latency"),
]

cols = 2
rows_per_col = 5
for i, (field, desc) in enumerate(state_fields):
    col = i // rows_per_col
    row = i % rows_per_col
    fx = 1.2 + col * 8.5
    fy = 9.95 - row * 0.33
    ax.text(fx, fy, f"  {field}",
            fontsize=8.5, color=C_STATE, fontweight="bold", va="center")
    ax.text(fx + 3.2, fy, desc,
            fontsize=8, color="#065f46", va="center")

# dashed connector from Gate 4 to state
ax.annotate("", xy=(9.0, 10.2), xytext=(9.0, 10.95),
            arrowprops=dict(arrowstyle="-|>", color=C_STATE,
                            lw=1.5, mutation_scale=15, linestyle="dashed"))

# ═══════════════════════════════════════════════════════════════════════════════
# ARTICLE SERIES ROADMAP
# ═══════════════════════════════════════════════════════════════════════════════
roadmap_rect = FancyBboxPatch((0.5, 6.4), 17, 1.55,
                               boxstyle="round,pad=0.1",
                               facecolor="#f0f9ff", edgecolor="#0369a1",
                               linewidth=1.5)
ax.add_patch(roadmap_rect)

ax.text(9, 7.75, "ARTICLE SERIES ROADMAP",
        ha="center", va="center", fontsize=10, fontweight="bold", color="#0369a1")

roadmap = [
    ("Art. 1 ✓", "Direct SDK\nDeterministic pipeline", 1.5),
    ("Art. 2 ✓", "Patterns catalogue\nConceptual overview", 5.0),
    ("Art. 3",   "LangGraph foundation\nReAct + Tool Use", 8.5),
    ("Art. 4",   "Multi-resume + Research\n+ Planning pattern", 12.0),
    ("Art. 5",   "Eval-Optimizer + Memory\n+ Reflection", 15.5),
]

for label, desc, rx in roadmap:
    done = "✓" in label
    col = "#15803d" if done else "#0369a1"
    ax.text(rx, 7.35, label, ha="center", fontsize=8.5,
            fontweight="bold", color=col)
    ax.text(rx, 6.85, desc, ha="center", fontsize=7.5,
            color="#374151", va="center")

# ═══════════════════════════════════════════════════════════════════════════════
# LEGEND
# ═══════════════════════════════════════════════════════════════════════════════
legend_items = [
    (C_AGENT_LIGHT,  C_AGENT,     "AI Agent"),
    (C_GATE_LIGHT,   C_GATE,      "Approval Gate (human-in-the-loop)"),
    (C_UTIL_LIGHT,   C_UTIL,      "Utility (no AI)"),
    (C_STATE_LIGHT,  C_STATE,     "Shared Graph State"),
    (C_SUPER_LIGHT,  C_SUPERVISOR,"Supervisor"),
]

lx, ly = 0.5, 5.9
ax.text(lx, ly, "LEGEND", fontsize=8, fontweight="bold", color="#374151")
for i, (fc, ec, lbl) in enumerate(legend_items):
    bx = lx + i * 3.4
    rect = FancyBboxPatch((bx, ly-0.55), 0.45, 0.35,
                           boxstyle="round,pad=0.04",
                           facecolor=fc, edgecolor=ec, linewidth=1.5)
    ax.add_patch(rect)
    ax.text(bx + 0.6, ly - 0.37, lbl, fontsize=8, color="#374151", va="center")

# ── Save ──────────────────────────────────────────────────────────────────────
import os
os.makedirs("docs/architecture", exist_ok=True)
out = "docs/architecture/v2_future_state.png"
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=C_BG)
print(f"Saved: {out}")
plt.close()
