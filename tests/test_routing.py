import json

from app.security import verify_signature
from .conftest import publish, register, routing, seed_demo_agents, wait_for
from demo.agents import POSTS


def notified_names(env, post_id):
    return {r["company_name"] for r in routing(env.tc, post_id)}


def test_swiggy_diwali_post_reaches_relevant_agents_only(env):
    agents = seed_demo_agents(env.tc)
    r = publish(env.tc, agents["swiggy"], **POSTS["swiggy"])
    assert r.status_code == 202
    pid = r.json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    names = notified_names(env, pid)
    assert "PhonePe" in names
    assert "Bajaj Auto" not in names          # irrelevant: dropped
    assert "Swiggy" not in names              # author never notified of own post


def test_bajaj_post_reaches_hdfc_not_food_or_payments_apps(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["bajaj"], **POSTS["bajaj"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    names = notified_names(env, pid)
    assert names == {"HDFC Bank"}


def test_scores_respect_threshold_and_cap(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["phonepe"], **POSTS["phonepe"]).json()["post_id"]
    rows = wait_for(lambda: routing(env.tc, pid))
    assert len(rows) <= env.settings.max_notified
    assert all(r["rerank_score"] >= env.settings.relevance_threshold for r in rows)
    assert [r["rerank_score"] for r in rows] == sorted((r["rerank_score"] for r in rows), reverse=True)


def test_blocked_industry_is_excluded_in_stage1(env):
    agents = seed_demo_agents(env.tc)
    blocker = register(env.tc, "Strict Pay", "Payments & Fintech",
                       "Interested in e-commerce promotions, food delivery festive discounts, "
                       "card payment partnerships, and merchant cashback opportunities.",
                       blocked_industries=["food delivery"])
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    assert "Strict Pay" not in notified_names(env, pid)
    assert "PhonePe" in notified_names(env, pid)


def test_author_blocklist_excludes_target_industry(env):
    agents = seed_demo_agents(env.tc)
    picky = register(env.tc, "PickyFood", "Food Delivery",
                     "Interested in festive food delivery campaigns and restaurant partnerships.",
                     blocked_industries=["Payments & Fintech"])
    pid = publish(env.tc, picky, title="Festive food delivery deals",
                  content="Festive food delivery discounts with payment cashback for orders.").json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    assert "PhonePe" not in notified_names(env, pid)


def test_webhook_is_signed_and_wraps_content_as_untrusted_data(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: env.received)
    req = env.received[0]
    body = req.content
    payload = json.loads(body)
    assert payload["event"] == "post.relevant"
    assert payload["post"]["id"] == pid
    assert payload["safety"]["content_trust"] == "untrusted_third_party_data"
    # The signature must verify with the secret of whichever agent received it.
    secret_by_hook = {a.secret for a in agents.values()}
    ts, sig = req.headers["X-Network-Timestamp"], req.headers["X-Network-Signature"]
    assert any(verify_signature(s, ts, body, sig) for s in secret_by_hook)


def test_failed_webhook_retries_then_marks_failed(env):
    env.box["status"] = 500
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    rows = wait_for(lambda: [r for r in routing(env.tc, pid) if r["status"] == "FAILED"]
                    and routing(env.tc, pid))
    assert all(r["status"] == "FAILED" for r in rows)
    assert all(r["attempts"] == env.settings.delivery_max_attempts for r in rows)


def test_expired_post_is_not_routed(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], expires_at="2000-01-01T00:00:00Z",
                  **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: env.tc.get("/api/v1/metrics").json()["counters"].get("posts_expired_before_routing"))
    assert routing(env.tc, pid) == []
