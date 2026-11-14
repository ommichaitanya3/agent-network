from demo.agents import POSTS
from .conftest import publish, routing, seed_demo_agents, wait_for


def test_dashboard_page_is_served(env):
    r = env.tc.get("/")
    assert r.status_code == 200
    assert "Agent Network" in r.text and "text/html" in r.headers["content-type"]


def test_agent_directory_never_leaks_credentials(env):
    seed_demo_agents(env.tc)
    r = env.tc.get("/api/v1/agents")
    assert r.status_code == 200
    rows = r.json()
    assert {a["company_name"] for a in rows} >= {"Swiggy", "PhonePe", "Bajaj Auto"}
    flat = str(rows)
    for secret_field in ("api_key", "webhook_secret", "webhook_url", "whsec_", "ak_"):
        assert secret_field not in flat


def test_post_feed_includes_routing_and_replies(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    env.tc.post(f"/api/v1/posts/{pid}/comments", headers=agents["phonepe"].headers, json={
        "author_agent_id": agents["phonepe"].id, "content": "Cashback incoming."})
    feed = env.tc.get("/api/v1/posts?limit=5").json()
    assert feed[0]["id"] == pid and feed[0]["author"] == "Swiggy"
    assert any(r["company_name"] == "PhonePe" and r["status"] == "ENGAGED" for r in feed[0]["routing"])
    assert feed[0]["comments"][0]["company_name"] == "PhonePe"


def test_post_feed_limit_is_clamped(env):
    assert env.tc.get("/api/v1/posts?limit=100000").status_code == 200
    assert env.tc.get("/api/v1/posts?limit=0").status_code == 200
