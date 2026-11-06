"""Playwright wrapper: one isolated browser context per run, with network-level safety enforcement."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright
from playwright.async_api import Error as PlaywrightError

from .config import Settings
from .safety import Policy, check_url


class ActionError(Exception):
    """An action could not be performed (element missing, timeout, blocked navigation...)."""


@dataclass
class Outcome:
    result: str
    data: dict | None = None  # merged into the run's structured result


# Injected into every document. Describes elements without ever exposing password values.
HELPER_JS = r"""
(() => {
  if (window.__tpDescribe) return;
  const clip = (s, n) => { s = (s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  window.__tpDescribe = (el) => {
    if (!el) return null;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || el.type || '').toLowerCase();
    const f = el.closest ? el.closest('form') : null;
    let text = '';
    if (tag === 'input' && ['submit', 'button', 'reset', 'image'].includes(type)) text = el.value || el.alt || '';
    else if (tag === 'select') { const o = el.selectedOptions && el.selectedOptions[0]; text = o ? o.textContent : ''; }
    else if (tag === 'input' || tag === 'textarea') text = '';
    else text = el.innerText || el.textContent;
    const label = (el.labels && el.labels.length) ? el.labels[0].innerText : '';
    const isPw = type === 'password';
    const isField = tag === 'input' || tag === 'textarea' || tag === 'select';
    return {
      tag, type,
      role: el.getAttribute('role') || '',
      text: clip(text || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt') || '', 80),
      label: clip(label, 60),
      name: clip(el.getAttribute('name') || el.id || '', 40),
      placeholder: clip(el.getAttribute('placeholder') || '', 50),
      aria: clip(el.getAttribute('aria-label') || '', 60),
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      href: tag === 'a' ? clip(el.href || '', 120) : '',
      value: (isField && !isPw && !['checkbox', 'radio', 'submit', 'button'].includes(type)) ? clip(el.value, 40) : '',
      checked: (type === 'checkbox' || type === 'radio') ? !!el.checked : null,
      options: tag === 'select' ? Array.from(el.options).slice(0, 15).map(o => clip(o.textContent, 30)) : null,
      form_method: f ? (f.getAttribute('method') || 'get').toLowerCase() : null,
      form_has_password: f ? !!f.querySelector('input[type=password]') : false,
    };
  };
})();
"""

OBSERVE_JS = r"""
() => {
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=option], [onclick]';
  document.querySelectorAll('[data-agent-id]').forEach(e => e.removeAttribute('data-agent-id'));
  const vh = window.innerHeight, vw = window.innerWidth;
  const items = [];
  for (const el of document.querySelectorAll(sel)) {
    if (el.disabled) continue;
    if ((el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    items.push({ el, inView: r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw });
  }
  items.sort((a, b) => (b.inView - a.inView));
  const out = [];
  for (const { el, inView } of items.slice(0, 70)) {
    const id = out.length + 1;
    el.setAttribute('data-agent-id', String(id));
    out.push(Object.assign({ id, in_view: inView }, window.__tpDescribe(el)));
  }
  const body = document.body ? document.body.innerText : '';
  return {
    url: location.href, title: document.title, elements: out,
    text: body.replace(/\n{3,}/g, '\n\n').slice(0, 6000),
    scroll_y: Math.round(window.scrollY), scroll_h: document.documentElement.scrollHeight,
    view_h: vh, total_interactive: items.length,
  };
}
"""

FOCUSED_JS = "() => window.__tpDescribe(document.activeElement && document.activeElement !== document.body ? document.activeElement : null)"

EXTRACT_ALL_JS = r"""
(args) => {
  const { selector, fields, limit } = args;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of Array.from(document.querySelectorAll(selector)).slice(0, limit)) {
    const row = {};
    for (const [k, spec] of Object.entries(fields)) {
      const [sel, attr] = spec.split('@');
      const t = sel ? el.querySelector(sel) : el;
      if (!t) { row[k] = null; continue; }
      if (attr) row[k] = attr === 'href' ? t.href : t.getAttribute(attr);
      else row[k] = clean(t.innerText || t.textContent);
    }
    out.push(row);
  }
  return out;
}
"""

EXTRACT_TEXT_JS = r"""
(args) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return Array.from(document.querySelectorAll(args.selector)).map(e => clean(e.innerText || e.textContent)).filter(Boolean).slice(0, args.limit);
}
"""

AUDIT_JS = r"""
() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const meta = (n) => (document.querySelector('meta[name="' + n + '"]') || {}).content || null;
  const imgs = q('img');
  return {
    title: document.title, url: location.href,
    meta_description: meta('description'),
    lang: document.documentElement.lang || null,
    has_viewport_meta: !!meta('viewport'),
    canonical: (document.querySelector('link[rel=canonical]') || {}).href || null,
    h1: q('h1').map(e => e.innerText.trim()).slice(0, 5),
    h2_count: q('h2').length,
    links: q('a[href]').length,
    images: imgs.length,
    images_missing_alt: imgs.filter(i => !i.hasAttribute('alt')).length,
    forms: q('form').length,
    inputs_without_label: q('input:not([type=hidden]):not([type=submit]):not([type=button])').filter(
      i => !(i.labels && i.labels.length) && !i.getAttribute('aria-label') && !i.getAttribute('placeholder')).length,
    external_scripts: q('script[src]').length,
    word_count: (document.body ? document.body.innerText : '').split(/\s+/).filter(Boolean).length,
  };
}
"""

LINKS_JS = r"""
(limit) => {
  const seen = new Set(); const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href;
    if (!/^https?:/i.test(h) || seen.has(h)) continue;
    seen.add(h); out.push(h);
    if (out.length >= limit) break;
  }
  return out;
}
"""

HEAVY_TYPES = {"image", "media", "font"}


class BrowserController:
    def __init__(self, playwright: Playwright, settings: Settings, policy: Policy):
        self._pw = playwright
        self.settings = settings
        self.policy = policy
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio", "--disable-extensions"]
        kwargs: dict = {"headless": self.settings.headless, "args": args}
        if self.settings.chrome_path:
            kwargs["executable_path"] = self.settings.chrome_path
        self.browser = await self._pw.chromium.launch(**kwargs)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            accept_downloads=False,
            service_workers="block",
            locale="en-US",
        )
        await self.context.add_init_script(HELPER_JS)
        await self.context.route("**/*", self._route)
        self.context.on("page", self._on_new_page)
        self.page = await self.context.new_page()
        self._wire_page(self.page)

    async def close(self) -> None:
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        except PlaywrightError:
            pass

    def _wire_page(self, page: Page) -> None:
        page.set_default_timeout(8000)
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

    def _on_new_page(self, page: Page) -> None:
        # The agent follows popups / target=_blank links into the new tab.
        self._wire_page(page)
        self.page = page

    async def _route(self, route) -> None:
        req = route.request
        url = req.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        if self.settings.block_heavy_resources and req.resource_type in HEAVY_TYPES:
            await route.abort()
            return
        ok, _ = await check_url(url, self.policy, navigation=req.is_navigation_request())
        if not ok:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    # ------------------------------------------------------------------ observation
    async def observe(self) -> dict:
        assert self.page
        try:
            obs = await self.page.evaluate(OBSERVE_JS)
        except PlaywrightError as e:
            # page mid-navigation or crashed document
            await asyncio.sleep(0.6)
            try:
                obs = await self.page.evaluate(OBSERVE_JS)
            except PlaywrightError:
                return {"url": self.page.url, "title": "", "elements": [], "text": f"(page not readable: {str(e)[:120]})",
                        "scroll_y": 0, "scroll_h": 0, "view_h": 0, "total_interactive": 0}
        if obs["url"].startswith("chrome-error:"):
            obs["text"] = "(The last navigation was blocked by the safety policy or failed to load.)"
        return obs

    async def screenshot(self) -> bytes | None:
        assert self.page
        try:
            return await self.page.screenshot(type="jpeg", quality=55, timeout=6000)
        except PlaywrightError:
            return None

    async def describe(self, action: dict) -> dict | None:
        """Describe the element an action targets (used for risk assessment)."""
        if action.get("type") == "press":
            try:
                return await self.page.evaluate(FOCUSED_JS)
            except PlaywrightError:
                return None
        if action.get("type") not in ("click", "type", "select"):
            return None
        loc = self._locator(action)
        try:
            desc = await loc.evaluate("el => window.__tpDescribe(el)", timeout=3000)
        except PlaywrightError:
            raise ActionError("Element not found. Element ids are renumbered every step; use an id from the latest list.")
        return desc

    def _locator(self, action: dict):
        if action.get("selector"):
            return self.page.locator(action["selector"]).first
        return self.page.locator(f'[data-agent-id="{int(action["id"])}"]').first

    # ------------------------------------------------------------------ actions
    async def act(self, action: dict) -> Outcome:
        assert self.page
        t = action["type"]
        try:
            return await getattr(self, f"_do_{t}")(action)
        except ActionError:
            raise
        except PlaywrightError as e:
            msg = str(e).split("\n")[0][:240]
            raise ActionError(msg)

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=4000)
        except PlaywrightError:
            pass
        await asyncio.sleep(0.4)

    async def _do_goto(self, a: dict) -> Outcome:
        url = a["url"]
        if "://" not in url:
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await self._settle()
        if self.page.url.startswith("chrome-error:"):
            raise ActionError("Navigation failed or was blocked")
        return Outcome(f"Opened {self.page.url}")

    async def _do_click(self, a: dict) -> Outcome:
        loc = self._locator(a)
        await loc.scroll_into_view_if_needed(timeout=3000)
        await loc.click(timeout=5000)
        await self._settle()
        return Outcome(f"Clicked. Now at {self.page.url}")

    async def _do_type(self, a: dict) -> Outcome:
        loc = self._locator(a)
        await loc.fill(str(a["text"]), timeout=5000)
        if a.get("submit"):
            await loc.press("Enter")
            await self._settle()
            return Outcome(f"Typed and pressed Enter. Now at {self.page.url}")
        return Outcome("Typed text")

    async def _do_select(self, a: dict) -> Outcome:
        loc = self._locator(a)
        try:
            await loc.select_option(label=str(a["value"]), timeout=3000)
        except PlaywrightError:
            await loc.select_option(value=str(a["value"]), timeout=3000)
        return Outcome(f"Selected '{a['value']}'")

    async def _do_press(self, a: dict) -> Outcome:
        await self.page.keyboard.press(a["key"])
        await self._settle()
        return Outcome(f"Pressed {a['key']}")

    async def _do_scroll(self, a: dict) -> Outcome:
        d = a.get("direction", "down")
        if d == "top":
            await self.page.evaluate("window.scrollTo(0,0)")
        elif d == "bottom":
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            await self.page.mouse.wheel(0, -700 if d == "up" else 700)
        await asyncio.sleep(0.3)
        return Outcome(f"Scrolled {d}")

    async def _do_wait(self, a: dict) -> Outcome:
        secs = min(max(float(a.get("seconds", 1)), 0.2), 5)
        await asyncio.sleep(secs)
        return Outcome(f"Waited {secs:g}s")

    async def _do_back(self, a: dict) -> Outcome:
        await self.page.go_back(wait_until="domcontentloaded", timeout=8000)
        await self._settle()
        return Outcome(f"Went back to {self.page.url}")

    async def _do_read(self, a: dict) -> Outcome:
        text = await self.page.evaluate("() => (document.body ? document.body.innerText : '')")
        text = text.strip()
        return Outcome("Page text (first 8000 chars):\n" + text[:8000])

    async def _do_extract_all(self, a: dict) -> Outcome:
        rows = await self.page.evaluate(EXTRACT_ALL_JS, {
            "selector": a["selector"], "fields": a["fields"], "limit": int(a.get("limit", 10)),
        })
        if not rows:
            raise ActionError(f"No elements matched '{a['selector']}'")
        return Outcome(f"Extracted {len(rows)} items", {a.get("key", "items"): rows})

    async def _do_extract_text(self, a: dict) -> Outcome:
        texts = await self.page.evaluate(EXTRACT_TEXT_JS, {"selector": a["selector"], "limit": int(a.get("limit", 5))})
        if not texts:
            raise ActionError(f"No text found for '{a['selector']}'")
        value = "\n\n".join(texts) if a.get("join") else texts
        return Outcome(f"Extracted {len(texts)} text block(s)", {a.get("key", "text"): value})

    async def _do_page_audit(self, a: dict) -> Outcome:
        raw = await self.page.evaluate(AUDIT_JS)
        issues, score = [], 100
        def flag(cond: bool, msg: str, penalty: int) -> None:
            nonlocal score
            if cond:
                issues.append(msg)
                score -= penalty
        flag(not raw["title"], "Missing <title>", 15)
        flag(not raw["meta_description"], "Missing meta description", 10)
        flag(len(raw["h1"]) != 1, f"Expected exactly one <h1>, found {len(raw['h1'])}", 10)
        flag(not raw["lang"], "<html> has no lang attribute", 8)
        flag(not raw["has_viewport_meta"], "Missing responsive viewport meta tag", 10)
        flag(not raw["canonical"], "No canonical link", 4)
        flag(raw["images_missing_alt"] > 0, f"{raw['images_missing_alt']} image(s) missing alt text", min(15, 3 * raw["images_missing_alt"]))
        flag(raw["inputs_without_label"] > 0, f"{raw['inputs_without_label']} form input(s) without a label", min(10, 3 * raw["inputs_without_label"]))
        flag(raw["word_count"] < 100, f"Very little text content ({raw['word_count']} words)", 5)
        raw["issues"] = issues
        raw["score"] = max(score, 0)
        return Outcome(f"Audit score {raw['score']}/100 with {len(issues)} issue(s)", {a.get("key", "audit"): raw})

    async def _do_check_links(self, a: dict) -> Outcome:
        limit = min(int(a.get("limit", 12)), 25)
        urls: list[str] = await self.page.evaluate(LINKS_JS, limit)
        sem = asyncio.Semaphore(4)

        async def probe(u: str) -> dict:
            ok, why = await check_url(u, self.policy, navigation=False)
            if not ok:
                return {"url": u, "status": None, "ok": False, "note": f"skipped: {why}"}
            async with sem:
                t0 = time.perf_counter()
                try:
                    resp = await self.context.request.get(u, timeout=8000, max_redirects=0, fail_on_status_code=False)
                    status = resp.status
                    loc = resp.headers.get("location")
                    await resp.dispose()
                    return {"url": u, "status": status, "ok": status < 400,
                            "ms": int((time.perf_counter() - t0) * 1000), "note": f"redirects to {loc}" if loc else ""}
                except PlaywrightError as e:
                    return {"url": u, "status": None, "ok": False, "note": str(e).split("\n")[0][:100]}

        results = await asyncio.gather(*(probe(u) for u in urls))
        broken = [r for r in results if not r["ok"]]
        host = urlparse(self.page.url).hostname or ""
        return Outcome(f"Checked {len(results)} links on {host}: {len(broken)} problem(s)",
                       {a.get("key", "links"): {"checked": len(results), "problems": len(broken), "results": results}})
