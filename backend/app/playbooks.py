"""Playbooks: ready-made, deterministic automations. They need no LLM and use the same safety layer."""
from __future__ import annotations

from urllib.parse import quote


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _str(v, default: str = "", n: int = 200) -> str:
    return (str(v) if v is not None else default).strip()[:n]


def _hn(p: dict, base: str) -> list[dict]:
    return [
        {"type": "goto", "url": "https://news.ycombinator.com/", "note": "Open Hacker News"},
        {"type": "extract_all", "key": "stories", "selector": ".athing",
         "fields": {"rank": ".rank", "title": ".titleline > a", "url": ".titleline > a@href"},
         "limit": _int(p.get("limit"), 10, 1, 30), "note": "Read the top stories"},
    ]


def _wiki(p: dict, base: str) -> list[dict]:
    topic = _str(p.get("topic"), "Python (programming language)", 100)
    return [
        {"type": "goto", "url": f"https://en.wikipedia.org/wiki/Special:Search?search={quote(topic)}&go=Go",
         "note": f"Search Wikipedia for {topic}"},
        {"type": "extract_text", "key": "title", "selector": "#firstHeading", "join": True,
         "note": "Read the article title"},
        {"type": "extract_text", "key": "summary", "selector": "#mw-content-text .mw-parser-output > p:not(.mw-empty-elt)",
         "limit": 2, "join": True, "note": "Read the opening paragraphs"},
    ]


def _audit(p: dict, base: str) -> list[dict]:
    return [
        {"type": "goto", "url": _str(p.get("url"), "https://example.com", 300), "note": "Open the page"},
        {"type": "page_audit", "key": "audit", "note": "Run SEO + accessibility checks"},
    ]


def _links(p: dict, base: str) -> list[dict]:
    return [
        {"type": "goto", "url": _str(p.get("url"), "https://example.com", 300), "note": "Open the page"},
        {"type": "check_links", "key": "links", "limit": _int(p.get("limit"), 12, 1, 25), "note": "Probe each link's HTTP status"},
    ]


def _demo_store(p: dict, base: str) -> list[dict]:
    q = _str(p.get("query"), "mug", 60)
    return [
        {"type": "goto", "url": f"{base}/demo/?q={quote(q)}", "note": f"Search the demo store for '{q}'"},
        {"type": "extract_all", "key": "products", "selector": ".product",
         "fields": {"name": "h3", "price": ".price", "category": ".cat"}, "limit": 20, "note": "Collect matching products"},
    ]


def _demo_contact(p: dict, base: str) -> list[dict]:
    return [
        {"type": "goto", "url": f"{base}/demo/contact", "note": "Open the contact form"},
        {"type": "type", "selector": "#name", "text": _str(p.get("name"), "Alex Tester", 60), "note": "Fill in name"},
        {"type": "type", "selector": "#email", "text": _str(p.get("email"), "alex@example.com", 80), "note": "Fill in email"},
        {"type": "type", "selector": "#message", "text": _str(p.get("message"), "Hello from TaskPilot!", 300), "note": "Fill in message"},
        {"type": "click", "selector": "#send", "note": "Submit the form (needs your approval)"},
        {"type": "extract_text", "key": "confirmation", "selector": "#confirmation", "join": True, "note": "Read the confirmation"},
    ]


PLAYBOOKS: dict[str, dict] = {
    "page_audit": {
        "name": "Website SEO & accessibility audit", "category": "IT Services",
        "description": "Opens a page and scores it on title, meta description, headings, alt text, form labels and more.",
        "params": [{"key": "url", "label": "Page URL", "default": "https://example.com", "type": "url"}],
        "build": _audit,
    },
    "link_check": {
        "name": "Broken link checker", "category": "IT Services",
        "description": "Collects links on a page and checks the HTTP status of each (redirects are reported, never followed).",
        "params": [{"key": "url", "label": "Page URL", "default": "https://example.com", "type": "url"},
                   {"key": "limit", "label": "Max links", "default": 12, "type": "number"}],
        "build": _links,
    },
    "hn_top": {
        "name": "Hacker News top stories", "category": "Research",
        "description": "Extracts the current top stories (rank, title, link) from news.ycombinator.com.",
        "params": [{"key": "limit", "label": "How many", "default": 10, "type": "number"}],
        "build": _hn,
    },
    "wikipedia_summary": {
        "name": "Wikipedia topic summary", "category": "Research",
        "description": "Searches Wikipedia and returns the article title and opening paragraphs.",
        "params": [{"key": "topic", "label": "Topic", "default": "Kubernetes", "type": "text"}],
        "build": _wiki,
    },
    "demo_store": {
        "name": "Demo store product search", "category": "Demo (safe sandbox)",
        "description": "Searches the built-in demo store and extracts matching products and prices.",
        "params": [{"key": "query", "label": "Search term", "default": "mug", "type": "text"}],
        "build": _demo_store,
    },
    "demo_contact": {
        "name": "Demo contact form (with approval)", "category": "Demo (safe sandbox)",
        "description": "Fills the demo contact form. Submitting is a medium-risk action, so it pauses for your approval.",
        "params": [{"key": "name", "label": "Name", "default": "Alex Tester", "type": "text"},
                   {"key": "email", "label": "Email", "default": "alex@example.com", "type": "text"},
                   {"key": "message", "label": "Message", "default": "Hello from TaskPilot!", "type": "text"}],
        "build": _demo_contact,
    },
}


def public_list() -> list[dict]:
    return [{"id": k, **{f: v[f] for f in ("name", "category", "description", "params")}} for k, v in PLAYBOOKS.items()]


def build_steps(playbook_id: str, params: dict, base_url: str) -> list[dict]:
    return PLAYBOOKS[playbook_id]["build"](params or {}, base_url)
