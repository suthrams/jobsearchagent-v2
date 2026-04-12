"""
Take dashboard screenshots for the blog post.
Requires dashboard running on localhost:8502.
Usage: conda run -n py3_13 python take_screenshots.py
"""

import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8502"
OUT  = Path("docs/blog_images")
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1400, 900

def wait(page, ms=2000):
    page.wait_for_timeout(ms)

def select_view(page, view_name):
    """Click a sidebar nav option by visible text."""
    # Streamlit radio labels are <p> elements inside label wrappers
    page.locator(f'label:has-text("{view_name}")').first.click()
    wait(page, 2500)

def screenshot(page, name):
    path = str(OUT / name)
    page.screenshot(path=path, full_page=False)
    print(f"Saved: {path}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": W, "height": H})
    page = ctx.new_page()

    # ── Load dashboard ────────────────────────────────────────────────────────
    page.goto(BASE, wait_until="networkidle")
    wait(page, 4000)

    # ── 1. Top Matches — scored jobs table with multi-track scores ────────────
    select_view(page, "Top Matches")
    screenshot(page, "screenshot_job_list.png")

    # ── 2. Run History — cost and token tracking ──────────────────────────────
    select_view(page, "Run History")
    screenshot(page, "screenshot_run_history.png")

    # ── 3. New Jobs — latest run ──────────────────────────────────────────────
    select_view(page, "New Jobs")
    screenshot(page, "screenshot_new_jobs.png")

    # ── 4. Companies view ─────────────────────────────────────────────────────
    select_view(page, "Companies")
    screenshot(page, "screenshot_companies.png")

    # ── 5. Architect Track — job list for tailoring context ───────────────────
    select_view(page, "Architect Track")
    screenshot(page, "screenshot_tailoring.png")

    browser.close()

print("\nAll screenshots saved to docs/blog_images/")
