import numpy as np

from app.embeddings import from_blob
from .conftest import publish, routing, seed_demo_agents, wait_for
from demo.agents import POSTS


def agent_vec(env, agent_id):
    from fastapi import Request  # noqa: F401  (keeps import side effects explicit)
    ctx = env.tc.app.state.ctx
    return from_blob(ctx.db.one("SELECT interest_embedding e FROM agents WHERE id=?", (agent_id,))["e"])


def test_comment_marks_engagement_and_pulls_vector_toward_post(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    ctx = env.tc.app.state.ctx
    post_vec = from_blob(ctx.db.one("SELECT post_embedding e FROM posts WHERE id=?", (pid,))["e"])
    before = float(agent_vec(env, agents["phonepe"].id) @ post_vec)

    r = env.tc.post(f"/api/v1/posts/{pid}/comments", headers=agents["phonepe"].headers, json={
        "author_agent_id": agents["phonepe"].id, "content": "Extra Rs 50 cashback via UPI."})
    assert r.status_code == 201 and r.json()["counted_as_engagement"] is True

    statuses = {x["company_name"]: x["status"] for x in routing(env.tc, pid)}
    assert statuses["PhonePe"] == "ENGAGED"
    after = float(agent_vec(env, agents["phonepe"].id) @ post_vec)
    assert after > before
    comments = env.tc.get(f"/api/v1/posts/{pid}/comments").json()
    assert comments[0]["company_name"] == "PhonePe"


def test_comment_from_non_notified_agent_is_not_engagement(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    r = env.tc.post(f"/api/v1/posts/{pid}/comments", headers=agents["bajaj"].headers, json={
        "author_agent_id": agents["bajaj"].id, "content": "Interesting."})
    assert r.status_code == 201 and r.json()["counted_as_engagement"] is False


def test_sweep_marks_ignored_and_shifts_vector_away_after_repeats(env):
    env.settings.ignore_shift_after = 2
    agents = seed_demo_agents(env.tc)
    pids = []
    for _ in range(2):
        pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
        wait_for(lambda p=pid: routing(env.tc, p))
        pids.append(pid)
    ctx = env.tc.app.state.ctx
    post_vec = from_blob(ctx.db.one("SELECT post_embedding e FROM posts WHERE id=?", (pids[0],))["e"])
    before = float(agent_vec(env, agents["flipkart"].id) @ post_vec)

    res = env.tc.post("/api/v1/feedback/sweep", params={"older_than_seconds": 0}).json()
    assert res["marked_ignored"] >= 2 and res["vectors_shifted_away"] >= 1
    statuses = {x["status"] for pid in pids for x in routing(env.tc, pid)}
    assert statuses == {"IGNORED"}
    after = float(agent_vec(env, agents["flipkart"].id) @ post_vec)
    assert after < before
    assert abs(np.linalg.norm(agent_vec(env, agents["flipkart"].id)) - 1.0) < 1e-4


def test_metrics_report_engagement_and_stage_latencies(env):
    agents = seed_demo_agents(env.tc)
    pid = publish(env.tc, agents["swiggy"], **POSTS["swiggy"]).json()["post_id"]
    wait_for(lambda: routing(env.tc, pid))
    env.tc.post(f"/api/v1/posts/{pid}/comments", headers=agents["phonepe"].headers, json={
        "author_agent_id": agents["phonepe"].id, "content": "Deal."})
    m = env.tc.get("/api/v1/metrics").json()
    assert m["engagement"]["overall_engagement_rate"] > 0
    for stage in ("stage1_sql_ms", "stage2_vector_ms", "stage3_rerank_ms", "end_to_end_routing_ms"):
        assert stage in m["latency"]
    assert m["counters"]["posts_published"] == 1
