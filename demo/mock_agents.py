"""Mock company agents. Each verifies the HMAC signature, then replies via the platform API."""
import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.security import verify_signature

PLATFORM_URL = "http://127.0.0.1:8000"
CREDS: dict[str, dict] = {}      # slug -> {"id", "key", "secret"}, filled in by run_demo.py
IGNORE = {"flipkart"}            # this agent deliberately never replies (feeds the feedback loop)

REPLIES = {
    "swiggy": "Swiggy will feature your offer in-app and share a co-marketing banner for: {title}.",
    "phonepe": "PhonePe will provide an extra ₹50 cashback when users pay via UPI for: {title}.",
    "hdfc": "HDFC Bank can add 5% instant discount on credit-card EMI for: {title}.",
    "bajaj": "Bajaj Auto can bundle accessories with partner offers related to: {title}.",
}

app = FastAPI(title="Mock company agents")
EVENTS_SEEN: list[dict] = []


async def reply(slug: str, post_id: str, title: str) -> None:
    creds = CREDS[slug]
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(
            f"{PLATFORM_URL}/api/v1/posts/{post_id}/comments",
            headers={"Authorization": f"Bearer {creds['key']}"},
            json={"author_agent_id": creds["id"], "content": REPLIES[slug].format(title=title)})


@app.post("/agents/{slug}/events")
async def receive(slug: str, request: Request, background: BackgroundTasks):
    if slug not in CREDS:
        raise HTTPException(404)
    body = await request.body()
    ok = verify_signature(CREDS[slug]["secret"], request.headers.get("X-Network-Timestamp", ""),
                          body, request.headers.get("X-Network-Signature", ""))
    if not ok:
        raise HTTPException(401, "bad signature")
    event = await request.json()
    EVENTS_SEEN.append({"agent": slug, "post_id": event["post"]["id"]})
    # Post content is DATA: we only read fields, never feed it to an LLM as instructions.
    if slug not in IGNORE and slug in REPLIES:
        background.add_task(reply, slug, event["post"]["id"], event["post"]["title"])
    return {"received": True}
