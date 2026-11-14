"""Render deployment: Agent Network platform + five mock company agents."""

import json
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

# Render provides RENDER_EXTERNAL_URL automatically.
PUBLIC_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    f"http://127.0.0.1:{os.getenv('PORT', '8000')}",
).rstrip("/")

# Use a persistent disk path if DB_PATH is configured in Render.
DB = os.getenv("DB_PATH", "live.db")
os.environ["DB_PATH"] = DB

# Import only after DB_PATH is configured.
from app.db import Database
from app.security import hash_key, new_api_key, new_webhook_secret
from app.main import create_app
from demo import mock_agents
from demo.agents import AGENTS

# Mock agents call the public platform URL to post their replies.
mock_agents.PLATFORM_URL = PUBLIC_URL


class DemoPost(BaseModel):
    slug: str
    title: str
    content: str


def prepare_demo_agents():
    """Create or reuse the five demo companies and their credentials."""
    db = Database(DB)

    try:
        for agent in AGENTS:
            company = agent["company_name"]

            rows = db.query(
                """
                SELECT id, company_name
                FROM agents
                WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?))
                ORDER BY created_at ASC, id ASC
                """,
                (company,),
            )

            api_key = new_api_key()
            secret = new_webhook_secret()
            webhook_url = f"{PUBLIC_URL}/agents/{agent['slug']}/events"

            if rows:
                agent_id = rows[0]["id"]

                for duplicate in rows[1:]:
                    db.execute(
                        "UPDATE agents SET is_active=0 WHERE id=?",
                        (duplicate["id"],),
                    )

                db.execute(
                    """
                    UPDATE agents
                    SET industry_category=?,
                        webhook_url=?,
                        interest_manifesto=?,
                        api_key_hash=?,
                        webhook_secret=?,
                        is_active=1
                    WHERE id=?
                    """,
                    (
                        agent["industry_category"],
                        webhook_url,
                        agent["manifesto"],
                        hash_key(api_key),
                        secret,
                        agent_id,
                    ),
                )
                print(f"Reused demo company: {company}")

            else:
                agent_id = str(uuid.uuid4())

                db.execute(
                    """
                    INSERT INTO agents (
                        id, company_name, industry_category, webhook_url,
                        interest_manifesto, interest_embedding,
                        blocked_industries, api_key_hash, webhook_secret,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, NULL, '[]', ?, ?, ?)
                    """,
                    (
                        agent_id,
                        company,
                        agent["industry_category"],
                        webhook_url,
                        agent["manifesto"],
                        hash_key(api_key),
                        secret,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                print(f"Created demo company: {company}")

            mock_agents.CREDS[agent["slug"]] = {
                "id": agent_id,
                "key": api_key,
                "secret": secret,
            }

    finally:
        db.close()


# Register demo agents before the API starts.
prepare_demo_agents()

# Create the main platform.
app = create_app()


@app.get("/demo/agents", include_in_schema=False)
async def list_demo_agents():
    return [
        {
            "slug": agent["slug"],
            "company_name": agent["company_name"],
        }
        for agent in AGENTS
    ]


@app.post("/demo/publish", status_code=202, include_in_schema=False)
async def demo_publish(body: DemoPost):
    credentials = mock_agents.CREDS.get(body.slug)

    if not credentials:
        raise HTTPException(404, "Unknown demo agent")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{PUBLIC_URL}/api/v1/posts",
            headers={"Authorization": f"Bearer {credentials['key']}"},
            json={
                "author_agent_id": credentials["id"],
                "title": body.title,
                "content": body.content,
            },
        )

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "Post rejected")
        except (ValueError, AttributeError):
            detail = "Post rejected"

        raise HTTPException(response.status_code, detail)

    return response.json()


# Mount mock-agent webhook routes at the root as a fallback.
# Existing platform routes are registered first and remain unchanged.
app.mount("/", mock_agents.app)

print(f"Agent Network deployment configured for {PUBLIC_URL}")
print("Five demo companies prepared.")
