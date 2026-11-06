"""End-to-end tests: real FastAPI server + real headless Chrome driving the built-in demo site.

The LLM is replaced by a scripted fake (no network). Everything else, including Playwright, the DOM
observation, the safety layer, approvals, persistence and the REST API, is the production code path.
"""
import httpx

from .conftest import HEADERS, TERMINAL, Server, agent_body, wait_for


def start(srv, body):
    r = srv.client.post("/api/runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def steps(srv, run_id):
    return srv.client.get(f"/api/runs/{run_id}/steps").json()


# ---------------------------------------------------------------- meta / API surface
def test_health_and_config(srv):
    assert srv.client.get("/api/health").json()["ok"] is True
    cfg = srv.client.get("/api/config").json()
    assert {p["id"] for p in cfg["playbooks"]} >= {"page_audit", "link_check", "hn_top", "demo_store", "demo_contact"}
    assert "paypal.com" in cfg["blocked_domains"]


def test_client_id_required(srv):
    r = httpx.get(srv.url + "/api/runs")
    assert r.status_code == 400


def test_agent_needs_llm_or_byok(srv):
    r = srv.client.post("/api/runs", json={"mode": "agent", "goal": "do something useful"})
    assert r.status_code == 400 and "LLM" in r.json()["detail"]


def test_validation_errors(srv):
    assert srv.client.post("/api/runs", json={"mode": "playbook", "playbook_id": "nope"}).status_code == 400
    assert srv.client.post("/api/runs", json={"mode": "agent", "goal": "x"}).status_code in (400,)
    assert srv.client.post("/api/runs", json={"mode": "weird"}).status_code == 422


# ---------------------------------------------------------------- playbooks (no LLM)
def test_playbook_demo_store(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_store", "params": {"query": "mug"}})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    products = run["result"]["data"]["products"]
    assert {p["name"] for p in products} == {"Ceramic Mug", "Travel Mug"}
    assert {p["price"] for p in products} == {"$12.50", "$18.25"}
    st = steps(srv, rid)
    assert [s["status"] for s in st] == ["ok", "ok"] and st[0]["screenshot"]
    img = srv.client.get(st[0]["screenshot"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"


def test_playbook_page_audit(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "page_audit", "params": {"url": srv.url + "/demo/"}})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    audit = run["result"]["data"]["audit"]
    assert audit["title"] == "Demo Store" and audit["has_viewport_meta"] and audit["lang"] == "en"
    assert 0 <= audit["score"] <= 100 and isinstance(audit["issues"], list)


def test_playbook_link_check(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "link_check", "params": {"url": srv.url + "/demo/", "limit": 10}})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    links = run["result"]["data"]["links"]
    assert links["checked"] >= 4 and links["problems"] == 0
    assert all(r["status"] == 200 for r in links["results"])


def test_playbook_contact_with_approval(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_contact",
                      "params": {"name": "Priya", "email": "p@example.com", "message": "Hello <b>team</b>"}})
    run = wait_for(srv.client, rid, {"awaiting_approval", "completed", "failed"})
    assert run["status"] == "awaiting_approval", run
    assert "Send message" in run["pending"]["summary"] and run["pending"]["reason"]
    assert srv.client.post(f"/api/runs/{rid}/approve", json={"approve": True}).status_code == 200
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    text = run["result"]["data"]["confirmation"]
    assert "Priya" in text and "Hello <b>team</b>" in text   # HTML was escaped by the site, shown as text
    assert [s["approval"] for s in steps(srv, rid) if s["approval"]] == ["approved"]


def test_playbook_contact_denied(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_contact"})
    wait_for(srv.client, rid, {"awaiting_approval"})
    srv.client.post(f"/api/runs/{rid}/approve", json={"approve": False})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "failed" and "declined" in run["error"]
    assert steps(srv, rid)[-1]["status"] == "denied"


def test_approve_without_pending_conflicts(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_store"})
    wait_for(srv.client, rid, TERMINAL)
    assert srv.client.post(f"/api/runs/{rid}/approve", json={"approve": True}).status_code == 409


def test_auto_approve_setting(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_contact", "require_approval": False})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    assert "auto" in [s["approval"] for s in steps(srv, rid)]


# ---------------------------------------------------------------- agent mode (scripted LLM)
def test_agent_multi_step_search(srv, fake_llm):
    rid = start(srv, agent_body("Find the price of the travel mug", "search", start_url=srv.url + "/demo/"))
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    assert "$18.25" in run["result"]["answer"] and run["result"]["success"] is True
    st = steps(srv, rid)
    assert [s["action"]["type"] for s in st] == ["goto", "type", "done"]
    assert st[1]["thought"] == "Use the search box" and st[1]["url"].endswith("q=travel")


def test_agent_blocked_actions_never_run(srv, fake_llm):
    rid = start(srv, agent_body("Buy a mug", "guard", start_url=srv.url + "/demo/checkout"))
    run = wait_for(srv.client, rid, TERMINAL)
    st = steps(srv, rid)
    assert [s["status"] for s in st] == ["ok", "blocked", "blocked", "ok"], st
    assert "payment" in st[1]["risk_reason"].lower() or "sensitive" in st[1]["risk_reason"].lower()
    assert "payment" in st[2]["risk_reason"].lower()
    assert "4111" not in str(st) and st[1]["action"]["text"] == "[redacted]"   # secrets are never persisted
    assert run["status"] == "completed" and run["result"]["success"] is False
    # the checkout page was never submitted: still on /checkout, nothing typed
    assert st[-1]["url"].endswith("/demo/checkout")


def test_agent_approval_flow(srv, fake_llm):
    rid = start(srv, agent_body("Send a message", "approval", start_url=srv.url + "/demo/contact"))
    run = wait_for(srv.client, rid, {"awaiting_approval", "completed", "failed"})
    assert run["status"] == "awaiting_approval", run
    assert srv.client.post(f"/api/runs/{rid}/approve", json={"approve": True}).status_code == 200
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "completed", run
    st = steps(srv, rid)
    assert st[3]["approval"] == "approved" and st[3]["url"].endswith("/demo/contact")  # POST page after submit


def test_agent_stop(srv, fake_llm):
    rid = start(srv, agent_body("Wait forever", "wait", start_url=srv.url + "/demo/", max_steps=30))
    wait_for(srv.client, rid, {"running"})
    assert srv.client.post(f"/api/runs/{rid}/stop").json()["ok"] is True
    run = wait_for(srv.client, rid, TERMINAL, timeout=20)
    assert run["status"] == "stopped"


def test_agent_step_limit(srv, fake_llm):
    rid = start(srv, agent_body("Keep waiting politely", "wait", start_url=srv.url + "/demo/", max_steps=2))
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "failed" and "step limit" in run["error"]


# ---------------------------------------------------------------- isolation, export, delete
def test_history_isolation_export_delete(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "demo_store"})
    wait_for(srv.client, rid, TERMINAL)
    assert any(r["id"] == rid for r in srv.client.get("/api/runs").json())
    other = {"X-Client-Id": "someone-else-0002"}
    assert httpx.get(f"{srv.url}/api/runs/{rid}", headers=other).status_code == 404
    assert httpx.get(f"{srv.url}/api/runs", headers=other).json() == []
    csv_text = srv.client.get(f"/api/runs/{rid}/export?format=csv").text
    assert csv_text.splitlines()[0] == "name,price,category" and "Travel Mug" in csv_text
    exported = srv.client.get(f"/api/runs/{rid}/export?format=json").json()
    assert exported["run"]["id"] == rid and len(exported["steps"]) == 2
    assert srv.client.delete(f"/api/runs/{rid}").status_code == 204
    assert srv.client.get(f"/api/runs/{rid}").status_code == 404


# ---------------------------------------------------------------- server-level safety / access control
def test_ssrf_blocked_when_private_hosts_disallowed(tmp_path):
    with Server(tmp_path, allow_private_hosts=False) as s:
        for url in ("http://127.0.0.1:%d/demo/" % s.port, "http://169.254.169.254/latest/meta-data/", "http://localhost/"):
            rid = start(s, {"mode": "playbook", "playbook_id": "page_audit", "params": {"url": url}})
            run = wait_for(s.client, rid, TERMINAL)
            assert run["status"] == "failed", (url, run)
            st = steps(s, rid)
            assert st[0]["status"] == "blocked", st
            assert "not allowed" in st[0]["risk_reason"] or "private" in st[0]["risk_reason"]


def test_allowed_domains_restrict_agent_navigation(srv):
    rid = start(srv, {"mode": "playbook", "playbook_id": "page_audit", "params": {"url": "https://example.com"},
                      "allowed_domains": ["wikipedia.org"]})
    run = wait_for(srv.client, rid, TERMINAL)
    assert run["status"] == "failed" and steps(srv, rid)[0]["status"] == "blocked"


def test_access_code(tmp_path):
    with Server(tmp_path, access_code="secret-123") as s:
        assert s.client.get("/api/config").json()["authorised"] is False
        assert s.client.get("/api/runs").status_code == 401
        s.client.headers["X-Access-Code"] = "secret-123"
        assert s.client.get("/api/config").json()["authorised"] is True
        assert s.client.get("/api/runs").status_code == 200


def test_rate_limit(tmp_path):
    with Server(tmp_path, rate_limit_per_hour=2) as s:
        for _ in range(2):
            assert s.client.post("/api/runs", json={"mode": "playbook", "playbook_id": "demo_store"}).status_code == 201
        assert s.client.post("/api/runs", json={"mode": "playbook", "playbook_id": "demo_store"}).status_code == 429
