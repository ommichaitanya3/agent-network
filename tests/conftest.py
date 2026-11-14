import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def env(tmp_path):
    """Platform running against a mock webhook transport that records every delivery."""
    received: list[httpx.Request] = []
    box = {"status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(box["status"], json={"ok": box["status"] < 300})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(db_path=str(tmp_path / "test.db"), delivery_base_delay=0.01,
                        delivery_max_attempts=3, circuit_failure_threshold=100)
    app = create_app(settings, http_client=client)
    with TestClient(app) as tc:
        yield SimpleNamespace(tc=tc, received=received, box=box, settings=settings)


def register(tc, name, industry, manifesto, webhook="http://127.0.0.1:9999/hook", **extra):
    r = tc.post("/api/v1/agents/register", json={
        "company_name": name, "industry_category": industry, "webhook_url": webhook,
        "interest_manifesto": manifesto, **extra})
    assert r.status_code == 201, r.text
    d = r.json()
    return SimpleNamespace(id=d["agent_id"], key=d["api_key"], secret=d["webhook_secret"], name=name,
                           headers={"Authorization": f"Bearer {d['api_key']}"})


def publish(tc, agent, title, content, **extra):
    return tc.post("/api/v1/posts", headers=agent.headers, json={
        "author_agent_id": agent.id, "title": title, "content": content, **extra})


def wait_for(fn, timeout=5.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def routing(tc, post_id):
    return tc.get(f"/api/v1/posts/{post_id}/routing").json()


def seed_demo_agents(tc):
    from demo.agents import AGENTS
    return {a["slug"]: register(tc, a["company_name"], a["industry_category"], a["manifesto"])
            for a in AGENTS}
