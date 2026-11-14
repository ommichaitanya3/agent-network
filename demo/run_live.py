"""Live mode with persistent database and duplicate-safe demo agents."""

import os
import sys
import time

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = "live.db"
os.environ["DB_PATH"] = DB

from app.db import Database
from app.security import hash_key, new_api_key, new_webhook_secret
from app.main import create_app
from demo import mock_agents
from demo.agents import AGENTS
from demo.common import AGENT_HOST, PLATFORM, serve


class DemoPost(BaseModel):
    slug: str
    title: str
    content: str


def prepare_demo_agents():
    """Reuse existing company records and rotate their demo credentials."""

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

            if rows:
                # Reuse the oldest existing record.
                agent_id = rows[0]["id"]

                # Deactivate duplicate records without deleting history.
                for duplicate in rows[1:]:
                    db.execute(
                        "UPDATE agents SET is_active=0 WHERE id=?",
                        (duplicate["id"],),
                    )

                api_key = new_api_key()
                secret = new_webhook_secret()

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
                        f"{AGENT_HOST}/agents/{agent['slug']}/events",
                        agent["manifesto"],
                        hash_key(api_key),
                        secret,
                        agent_id,
                    ),
                )

                print(f"Reused: {company} (ID: {agent_id})")

            else:
                # Register only when the company does not exist.
                api_key = new_api_key()
                secret = new_webhook_secret()
                import json
                import uuid
                from datetime import datetime, timezone

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
                        f"{AGENT_HOST}/agents/{agent['slug']}/events",
                        agent["manifesto"],
                        hash_key(api_key),
                        secret,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

                print(f"Created: {company}")

            mock_agents.CREDS[agent["slug"]] = {
                "id": agent_id,
                "key": api_key,
                "secret": secret,
            }

    finally:
        db.close()


def main():
    # Prepare credentials before starting the servers.
    prepare_demo_agents()

    platform = create_app()

    @platform.get("/demo/agents", include_in_schema=False)
    async def demo_agents():
        return [
            {
                "slug": a["slug"],
                "company_name": a["company_name"],
            }
            for a in AGENTS
        ]

    @platform.post("/demo/publish", status_code=202, include_in_schema=False)
    async def demo_publish(body: DemoPost):
        creds = mock_agents.CREDS.get(body.slug)

        if not creds:
            raise HTTPException(404, "Unknown demo agent")

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{PLATFORM}/api/v1/posts",
                headers={"Authorization": f"Bearer {creds['key']}"},
                json={
                    "author_agent_id": creds["id"],
                    "title": body.title,
                    "content": body.content,
                },
            )

        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "Post rejected")
            except ValueError:
                detail = "Post rejected"

            raise HTTPException(response.status_code, detail)

        return response.json()

    serve(platform, 8000)
    serve(mock_agents.app, 9001)

    print("\nAgent Network is running.")
    print("Dashboard : http://127.0.0.1:8000")
    print("API docs  : http://127.0.0.1:8000/docs")
    print(f"Database  : {os.path.abspath(DB)}")
    print("Active demo companies: 5")
    print("Persistence: enabled")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nAgent Network stopped.")


if __name__ == "__main__":
    main()
