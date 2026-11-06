"""Safety layer: the reason this agent is "low-risk".

Everything the agent wants to do passes through here *before* it touches the browser:

* URL policy  - blocks non-http(s), credentials-in-URL, private/loopback/link-local targets (SSRF),
                banking/payment/identity domains, and anything outside an optional per-task allowlist.
* Action risk - classifies each action as low / medium / blocked.
                low      -> runs immediately
                medium   -> needs human approval (form submits, sign-up, add to cart, ...)
                blocked  -> never runs (payments, deletion, passwords, card numbers, auth forms)

The LLM can never relax these rules; they run outside the model.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_BLOCKED_DOMAINS = [
    # payments
    "paypal.com", "stripe.com", "razorpay.com", "paytm.com", "checkout.com", "braintreegateway.com",
    "squareup.com", "pay.google.com", "payu.in",
    # banks / brokers / crypto
    "chase.com", "bankofamerica.com", "wellsfargo.com", "hdfcbank.com", "icicibank.com", "sbi.co.in",
    "onlinesbi.sbi", "axisbank.com", "zerodha.com", "coinbase.com", "binance.com",
    # identity providers
    "accounts.google.com", "login.microsoftonline.com", "login.live.com", "appleid.apple.com",
]

ALLOWED_ACTIONS = {
    "goto", "click", "type", "select", "press", "scroll", "wait", "read", "back",
    "extract_all", "extract_text", "page_audit", "check_links", "done", "fail",
}

ALLOWED_KEYS = {
    "Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight",
    "PageDown", "PageUp", "Home", "End", "Space",
}


@dataclass
class Policy:
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_DOMAINS))
    allow_private: bool = False
    require_approval: bool = True


@dataclass
class Risk:
    level: str   # "low" | "medium" | "blocked"
    reason: str = ""


# --------------------------------------------------------------------------- URL policy

_dns_cache: dict[str, tuple[float, list[str]]] = {}


def _host_matches(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for d in domains:
        d = d.lower().strip().lstrip("*.").rstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def _is_public_ip(raw: str) -> bool:
    raw = raw.split("%")[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


async def _resolve(host: str) -> list[str]:
    now = time.time()
    cached = _dns_cache.get(host)
    if cached and now - cached[0] < 60:
        return cached[1]
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        ips = sorted({i[4][0] for i in infos})
    except socket.gaierror:
        ips = []
    _dns_cache[host] = (now, ips)
    return ips


async def check_url(url: str, policy: Policy, *, navigation: bool = True) -> tuple[bool, str]:
    """Return (allowed, reason). `navigation` enables the per-task domain allowlist."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False, "Malformed URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme or '?'}' is not allowed (http/https only)"
    if not host:
        return False, "URL has no host"
    if parsed.username or parsed.password:
        return False, "URLs with embedded credentials are not allowed"
    if _host_matches(host, policy.blocked_domains):
        return False, f"{host} is on the blocked list (payments, banking or identity provider)"
    if navigation and policy.allowed_domains and not _host_matches(host, policy.allowed_domains):
        return False, f"{host} is outside this task's allowed domains"
    if not policy.allow_private:
        if port not in (None, 80, 443):
            return False, f"Non-standard port {port} is not allowed"
        if host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
            return False, "Local/internal hostnames are not allowed"
        ips = [host] if _is_ip_literal(host) else await _resolve(host)
        if any(not _is_public_ip(ip) for ip in ips):
            return False, "Target resolves to a private or reserved network address"
    return True, ""


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- Action risk

BLOCK_CLICK = re.compile(
    r"\b(pay|pay now|payment|purchase|buy|buy now|place (?:your |my )?order|complete (?:order|purchase)|"
    r"confirm (?:order|payment|purchase)|delete|remove account|close account|deactivate|erase|"
    r"transfer|withdraw|send money|wire|donate)\b",
    re.I,
)
MEDIUM_CLICK = re.compile(
    r"\b(submit|send|post|publish|sign ?in|log ?in|sign ?up|register|create account|apply|book|reserve|"
    r"add to cart|add to bag|checkout|check out|comment|reply|share|save|confirm|subscribe|unsubscribe|upload)\b",
    re.I,
)
SENSITIVE_FIELD = re.compile(
    r"(passw(or)?d|passwd|pwd|\bpin\b|card|cvv|cvc|ssn|social.?security|aadhaar|\bpan\b|\botp\b|"
    r"one.?time|iban|routing|account.?number|security.?code|secret|token)",
    re.I,
)
SENSITIVE_AUTOCOMPLETE = ("cc-", "one-time-code", "current-password", "new-password")


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def looks_sensitive(text: str) -> str | None:
    """Detect card numbers / SSN-like strings in text the agent wants to type."""
    digits = re.sub(r"[\s-]", "", text)
    if digits.isdigit() and 13 <= len(digits) <= 19 and luhn_ok(digits):
        return "text looks like a payment card number"
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", text.strip()):
        return "text looks like a government ID number"
    return None


def _blob(el: dict) -> str:
    return " ".join(str(el.get(k) or "") for k in ("text", "label", "name", "placeholder", "aria"))


def assess_action(action: dict, el: dict | None) -> Risk:
    """Classify an action given the described target element (None for page-level actions)."""
    t = action.get("type")

    if t == "type":
        text = str(action.get("text", ""))
        el = el or {}
        blob = _blob(el)
        if el.get("type") == "password" or any(el.get("autocomplete", "").startswith(p) for p in SENSITIVE_AUTOCOMPLETE):
            return Risk("blocked", "Typing into password / payment fields is never allowed")
        if SENSITIVE_FIELD.search(blob):
            return Risk("blocked", f"Field looks sensitive ('{blob.strip()[:40]}'); the agent never enters secrets")
        why = looks_sensitive(text)
        if why:
            return Risk("blocked", f"Refusing to type sensitive data: {why}")
        if action.get("submit") and (el.get("form_method") == "post"):
            return Risk("medium", "Typing then submitting a POST form")
        return Risk("low")

    if t == "select":
        if SENSITIVE_FIELD.search(_blob(el or {})):
            return Risk("blocked", "Field looks sensitive")
        return Risk("low")

    if t == "click":
        el = el or {}
        blob = _blob(el)
        m = BLOCK_CLICK.search(blob)
        if m:
            return Risk("blocked", f"'{m.group(0)}' looks like a payment or destructive action")
        is_submit = el.get("type") in ("submit", "image")
        if is_submit and el.get("form_has_password"):
            return Risk("blocked", "Sign-in / authentication forms are out of scope")
        m = MEDIUM_CLICK.search(blob)
        if m:
            return Risk("medium", f"'{m.group(0)}' changes state on the site")
        if is_submit and el.get("form_method") == "post":
            return Risk("medium", "Submits a POST form")
        return Risk("low")

    if t == "press":
        if action.get("key") == "Enter" and el:
            if el.get("form_has_password"):
                return Risk("blocked", "Sign-in / authentication forms are out of scope")
            if el.get("form_method") == "post":
                return Risk("medium", "Enter would submit a POST form")
        return Risk("low")

    return Risk("low")


def validate_action(action: dict, *, allow_selector: bool) -> str | None:
    """Return an error string when the action is malformed, else None."""
    t = action.get("type")
    if t not in ALLOWED_ACTIONS:
        return f"Unknown action type '{t}'. Allowed: {', '.join(sorted(ALLOWED_ACTIONS))}"
    if "selector" in action and not allow_selector and t in ("click", "type", "select"):
        return "Use the numeric element 'id' from the list, not selectors"
    if t in ("click", "type", "select") and not (action.get("id") or (allow_selector and action.get("selector"))):
        return f"'{t}' needs an element id"
    if t == "type" and "text" not in action:
        return "'type' needs 'text'"
    if t == "select" and "value" not in action:
        return "'select' needs 'value'"
    if t == "goto" and not action.get("url"):
        return "'goto' needs 'url'"
    if t == "press" and action.get("key") not in ALLOWED_KEYS:
        return f"Key must be one of: {', '.join(sorted(ALLOWED_KEYS))}"
    return None
