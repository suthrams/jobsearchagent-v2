"""Capture a PII-safe screenshot of the Cost Dashboard's Per-agent panel.

Article 6 supporting tool. Run LOCALLY (not in the agent sandbox): it boots
the real Streamlit app against your local data/v2.db, navigates ONLY to the
Cost Dashboard, and clips the screenshot to the "Per-agent cost breakdown"
section -- the panel that shows agent / model / calls / tokens / cost / %.

Why this clip and nothing else:
  - The Per-agent panel is pure aggregate. It contains NO job titles and NO
    company names, so it is safe to publish.
  - The "Top 5 most expensive runs" table and any run/job drill-through are
    deliberately OUT of frame. Those expand to job titles and companies.
  - A full-page reference shot is ALSO saved (……_FULL_review_only.png) so
    you can eyeball the whole dashboard for anything sensitive before you
    publish. That file is for your review only -- do not publish it.

Usage (from repo root):
    pip install playwright
    python -m playwright install chromium
    python tools/capture_cost_dashboard.py

Options:
    --port 8765            port to run Streamlit on
    --out PATH             publish-candidate screenshot path
    --window "All time"    Cost Dashboard time window ("Last 7 days" |
                           "Last 30 days" | "All time")
    --keep-streamlit       do not shut Streamlit down on exit (debugging)

You still MUST visually confirm the output PNG has no PII before wiring it
into the article. Publishing to LinkedIn is irreversible.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "blogs" / "blog_images" / "screenshot_v2_article6_cost_dashboard.png"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(1.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--window", default="All time",
                    choices=["Last 7 days", "Last 30 days", "All time"])
    ap.add_argument("--keep-streamlit", action="store_true")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  pip install playwright\n"
              "  python -m playwright install chromium", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    full_out = args.out.with_name(args.out.stem + "_FULL_review_only.png")

    streamlit_proc = None
    if not _port_open(args.port):
        print(f"Starting Streamlit on :{args.port} ...")
        streamlit_proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run",
             "app/ui/streamlit_app.py",
             "--server.headless", "true",
             "--server.port", str(args.port),
             "--browser.gatherUsageStats", "false"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            env={**os.environ},
        )
        if not _wait_for_port(args.port):
            print("Streamlit did not come up within 90s. Check it boots with "
                  "your local .env (it reads data/v2.db directly).",
                  file=sys.stderr)
            if streamlit_proc:
                streamlit_proc.terminate()
            return 3
    else:
        print(f"Reusing the server already on :{args.port}")

    url = f"http://localhost:{args.port}"
    rc = 0
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 2400},
                                    device_scale_factor=2)
            page.goto(url, wait_until="domcontentloaded")

            # Streamlit shell is ready when the sidebar mounts.
            page.wait_for_selector('section[data-testid="stSidebar"]',
                                   timeout=60_000)

            # Navigate via the sidebar radio, scoped so we do not match the
            # word "Cost Dashboard" where it appears in body captions.
            sidebar = page.locator('section[data-testid="stSidebar"]')
            sidebar.get_by_text("Cost Dashboard", exact=True).click()

            # Main panel rendered.
            page.get_by_role("heading", name="Cost Dashboard").wait_for(
                timeout=30_000)

            # Widen the window so the panel reflects all logged runs.
            page.get_by_text(args.window, exact=True).click()

            # Wait for the per-agent section + its Plotly chart to paint.
            start = page.get_by_text("Per-agent cost breakdown")
            start.wait_for(timeout=30_000)
            end = page.get_by_text("Per-model cost breakdown")
            end.wait_for(timeout=30_000)
            page.wait_for_selector(".js-plotly-plot", timeout=30_000)
            page.wait_for_timeout(2500)  # let Plotly settle

            # Full-page reference shot for the human PII review only.
            page.screenshot(path=str(full_out), full_page=True)

            # Clip precisely between the two subheaders: this is the
            # aggregate Per-agent panel and nothing below it.
            sb = start.bounding_box()
            eb = end.bounding_box()
            if not sb or not eb:
                print("Could not locate the panel bounds; saved full-page "
                      f"reference only at {full_out}", file=sys.stderr)
                rc = 4
            else:
                clip = {
                    "x": 0.0,
                    "y": max(sb["y"] - 16, 0.0),
                    "width": 1600.0,
                    "height": (eb["y"] - sb["y"]) - 8,
                }
                page.screenshot(path=str(args.out), clip=clip)
                print(f"Publish candidate : {args.out}")
            print(f"Review-only full  : {full_out}")
            browser.close()
    finally:
        if streamlit_proc and not args.keep_streamlit:
            streamlit_proc.terminate()
            try:
                streamlit_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                streamlit_proc.kill()

    print("\nNEXT: open the publish-candidate PNG and confirm there are no "
          "job titles or company names before wiring it into the article.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
