"""Compose the LinkedIn banner from the D2-rendered LangGraph workflow.

Reads:    docs/blog_images/diag_v2_agent_graph_core.png
Writes:   docs/blog_images/diag_v2_agent_graph.png

Layout: 2400x1260 banner. Title on top, the horizontal D2-rendered graph in
the middle, pattern chips below, attribution at the bottom.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


CORE_GRAPH = Path("docs/blog_images/diag_v2_agent_graph_core.png")
OUT_BANNER = Path("docs/blog_images/diag_v2_agent_graph.png")

W, H = 2400, 1260
PAD_X = 80
TITLE_TOP = 50
GRAPH_TOP = 230
GRAPH_BOTTOM = 870       # graph rendering region
PATTERNS_TOP = 910
FOOTER_TOP = 1180


def _load_font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        filename,
        f"C:/Windows/Fonts/{filename}",
        "C:/Windows/Fonts/Candara.ttf",
        "C:/Windows/Fonts/Candarab.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def main() -> None:
    if not CORE_GRAPH.exists():
        raise FileNotFoundError(
            f"Core D2 render missing: {CORE_GRAPH}. "
            f"Run: d2 docs/blog_images/diag_v2_agent_graph.d2 {CORE_GRAPH} --theme=200"
        )

    graph = Image.open(CORE_GRAPH).convert("RGB")
    bg_color = graph.getpixel((4, 4))

    canvas = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(canvas)

    # ── Title block (top) ─────────────────────────────────────────────────────
    title_font = _load_font("Candarab.ttf", 72)
    sub_font   = _load_font("Candara.ttf",  30)
    accent_font= _load_font("Candarab.ttf", 26)

    TEXT   = (241, 245, 249)
    MUTED  = (148, 163, 184)
    ACCENT = ( 56, 189, 248)

    draw.text((PAD_X, TITLE_TOP), "v2 Workflow Graph", fill=TEXT, font=title_font)
    draw.text((PAD_X, TITLE_TOP + 95),
              "Agentic AI patterns wired into a stateful orchestrator",
              fill=MUTED, font=sub_font)
    draw.text((PAD_X, TITLE_TOP + 138),
              "LangGraph  +  SqliteSaver  +  8 specialized agents",
              fill=ACCENT, font=accent_font)

    # ── Graph (middle, full width, height-bound) ──────────────────────────────
    graph_max_h = GRAPH_BOTTOM - GRAPH_TOP
    graph_max_w = W - 2 * PAD_X
    g_w, g_h = graph.size
    scale = min(graph_max_h / g_h, graph_max_w / g_w)
    new_w, new_h = int(g_w * scale), int(g_h * scale)
    graph_resized = graph.resize((new_w, new_h), Image.LANCZOS)
    gx = (W - new_w) // 2
    gy = GRAPH_TOP + (graph_max_h - new_h) // 2
    canvas.paste(graph_resized, (gx, gy))

    # ── Pattern chips (below the graph) ───────────────────────────────────────
    section_h = _load_font("Candarab.ttf", 26)
    chip_bold = _load_font("Candarab.ttf", 24)
    chip_norm = _load_font("Candara.ttf",  22)

    draw.text((PAD_X, PATTERNS_TOP),
              "Patterns at work in this graph",
              fill=ACCENT, font=section_h)

    patterns = [
        ("Bounded ReAct",                "Research capped at two reasoning steps"),
        ("Structured Output",            "Scoring returns Pydantic JSON, batched"),
        ("Bounded Reflection",           "Critic + Auditor loop, max 3 rounds"),
        ("Evidence-Bound Generation",    "Tailoring claims cite the original resume"),
        ("Runtime Fidelity Guardrail",   "Reviewer always runs after Tailoring"),
        ("Stateful Graph Orchestration", "Workflow checkpointed, resumable on crash"),
    ]
    # Three columns x two rows
    cols = 3
    col_w = (W - 2 * PAD_X) // cols
    chip_y0 = PATTERNS_TOP + 50
    row_h = 60

    for i, (name, desc) in enumerate(patterns):
        col = i % cols
        row = i // cols
        x = PAD_X + col * col_w
        y = chip_y0 + row * row_h

        # Diamond bullet
        cx, cy = x + 8, y + 16
        diamond = [(cx, cy - 8), (cx + 8, cy), (cx, cy + 8), (cx - 8, cy)]
        draw.polygon(diamond, fill=ACCENT)

        # Name (bold) + dim description
        draw.text((x + 28, y), name, fill=TEXT, font=chip_bold)
        draw.text((x + 28, y + 32), desc, fill=MUTED, font=chip_norm)

    # ── Footer ────────────────────────────────────────────────────────────────
    small_font = _load_font("Candara.ttf", 22)
    draw.text((PAD_X, FOOTER_TOP),
              "Real graph from app.workflows.workflow_graph.build_graph()",
              fill=MUTED, font=small_font)
    # Right-aligned link
    link_text = "github.com/suthrams/jobsearchagent-v2"
    link_w = draw.textlength(link_text, font=small_font)
    draw.text((W - PAD_X - link_w, FOOTER_TOP),
              link_text, fill=ACCENT, font=small_font)

    OUT_BANNER.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT_BANNER, "PNG", optimize=True)
    print(f"Saved banner: {OUT_BANNER} ({W}x{H})")


if __name__ == "__main__":
    main()
