"""Browser-level UI test: drives the built React app (served by FastAPI) with real Chrome.

Set UI_SHOTS=/some/dir to save screenshots of each stage. Skipped when frontend/dist has not been built.
"""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import CHROME, FakeLLM, Server

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="run `npm run build` in frontend/ first")


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr("app.main.LLMClient", FakeLLM)
    with Server(tmp_path_factory.mktemp("uidata"), static_dir=DIST) as s, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME or None, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1180, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(30000)
        yield s, page
        browser.close()
    mp.undo()


def shot(page, name):
    d = os.getenv("UI_SHOTS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), full_page=True)


def set_llm(page, base, model):
    page.goto(base + "/#/settings")
    page.fill("#key", "test-key")
    page.fill("#model", model)
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_role("status")).to_have_text("Saved")


def test_home_and_playbooks_listing(ui):
    s, page = ui
    page.goto(s.url + "/")
    expect(page.get_by_role("heading", name="Automate multi-step browser tasks, safely.")).to_be_visible()
    shot(page, "01-home")
    page.get_by_role("tab", name="Playbooks").click()
    expect(page.get_by_role("heading", name="Website SEO & accessibility audit")).to_be_visible()
    shot(page, "02-playbooks")


def test_playbook_with_approval_through_ui(ui):
    s, page = ui
    page.goto(s.url + "/")
    page.get_by_role("tab", name="Playbooks").click()
    card = page.locator(".pb", has_text="Demo contact form")
    card.get_by_label("Name").fill("Asha Rao")
    card.get_by_role("button", name="Run playbook").click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Send message")
    shot(page, "03-approval")
    dialog.get_by_role("button", name="Approve").click()
    expect(page.locator(".badge")).to_have_text("Completed")
    expect(page.locator(".card", has_text="Result")).to_contain_text("Thanks, Asha Rao")
    expect(page.locator(".chip", has_text="you approved")).to_be_visible()
    expect(page.locator(".shot img").first).to_be_visible()
    shot(page, "04-completed")


def test_agent_run_through_ui(ui):
    s, page = ui
    set_llm(page, s.url, "search")
    page.goto(s.url + "/")
    page.get_by_role("button", name="Find a price").click()
    page.get_by_role("button", name="Run agent").click()
    expect(page.locator(".badge")).to_have_text("Completed")
    expect(page.locator(".answer")).to_contain_text("$18.25")
    expect(page.locator(".step")).to_have_count(3)
    shot(page, "05-agent-result")


def test_blocked_actions_visible_in_ui(ui):
    s, page = ui
    set_llm(page, s.url, "guard")
    page.goto(s.url + "/")
    page.get_by_role("button", name="Try to buy (blocked)").click()
    page.locator("#url").fill(s.url + "/demo/checkout")
    page.get_by_role("button", name="Run agent").click()
    expect(page.locator(".badge")).to_have_text("Completed")
    expect(page.locator(".chip.st-blocked")).to_have_count(2)
    expect(page.locator(".answer.partial")).to_contain_text("blocked")
    shot(page, "06-blocked")


def test_history_export_delete_and_safety_page(ui):
    s, page = ui
    page.goto(s.url + "/#/history")
    expect(page.locator(".runlist li").first).to_be_visible()
    n = page.locator(".runlist li").count()
    assert n >= 3
    shot(page, "07-history")
    page.locator(".runlist li a").first.click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Export CSV").click()
    assert dl.value.suggested_filename.endswith(".csv")
    page.goto(s.url + "/#/safety")
    expect(page.get_by_text("Blocked, no override")).to_be_visible()
    expect(page.get_by_text("payment, banking and identity domains")).to_be_visible()
    shot(page, "08-safety")
    page.goto(s.url + "/#/history")
    page.locator(".runlist li").first.get_by_role("button", name="Delete").click()
    expect(page.locator(".runlist li")).to_have_count(n - 1)


def test_stop_button(ui):
    s, page = ui
    set_llm(page, s.url, "wait")
    page.goto(s.url + "/")
    page.locator("#goal").fill("Keep waiting politely for a while")
    page.locator("#url").fill(s.url + "/demo/")
    page.get_by_role("button", name="Run agent").click()
    expect(page.locator(".badge")).to_have_text("Running")
    page.get_by_role("button", name="Stop").click()
    expect(page.locator(".badge")).to_have_text("Stopped")
