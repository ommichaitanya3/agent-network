"""FastAPI app: REST ingestion -> event queue -> routing engine -> webhook delivery."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .config import Settings
from .db import Database
from .delivery import DeliveryJob, DeliveryService, build_payload
from .embeddings import build_embedder, to_blob
from .feedback import engagement_summary, mark_engaged, sweep_ignored
from .metrics import Metrics
from .rerank import build_reranker
from .routing import Router
from .schemas import PostComment, PublishPost, RegisterAgent
from .security import (
    hash_key,
    is_private_webhook,
    new_api_key,
    new_webhook_secret,
    sanitize_text,
    scan_for_injection,
)

log = logging.getLogger("platform")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def handle_post_published(ctx, event: dict) -> None:
    """Async consumer for post.published: embed, route, log, enqueue deliveries."""

    post = ctx.db.one(
        "SELECT * FROM posts WHERE id=?",
        (event["post_id"],),
    )

    if not post:
        return

    if post["expires_at"] and post["expires_at"] <= now_iso():
        ctx.metrics.inc("posts_expired_before_routing")
        return

    author = ctx.db.one(
        "SELECT * FROM agents WHERE id=?",
        (post["author_agent_id"],),
    )

    text = f"{post['title']}. {post['content']}"

    vec = await ctx.embedder.embed(text)

    ctx.db.execute(
        "UPDATE posts SET post_embedding=? WHERE id=?",
        (to_blob(vec), post["id"]),
    )

    candidates = await ctx.router.route(text, vec, author)

    for c in candidates:
        log_id = str(uuid.uuid4())
        ts = now_iso()

        ctx.db.execute(
            "INSERT INTO routing_logs "
            "(id, post_id, target_agent_id, vector_score, rerank_score, "
            "status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'NOTIFIED',?,?)",
            (
                log_id,
                post["id"],
                c.agent_id,
                c.vector_score,
                c.rerank_score,
                ts,
                ts,
            ),
        )

        ctx.delivery.submit(
            DeliveryJob(
                log_id=log_id,
                agent_id=c.agent_id,
                webhook_url=c.webhook_url,
                secret=c.webhook_secret,
                payload=build_payload(
                    post,
                    author,
                    c.rerank_score,
                    log_id,
                ),
            )
        )

    ctx.metrics.inc("posts_routed")

    ctx.metrics.observe(
        "end_to_end_routing_ms",
        (time.perf_counter() - event["t0"]) * 1000,
    )


async def _router_loop(ctx) -> None:
    """Continuously process published-post events."""

    while True:
        event = await ctx.events.get()

        try:
            await handle_post_published(ctx, event)

        except Exception:
            log.exception("routing failed for %s", event)
            ctx.metrics.inc("routing_errors")

        finally:
            ctx.events.task_done()


def create_app(
    settings: Optional[Settings] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    embedder=None,
    reranker=None,
) -> FastAPI:

    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):

        db = Database(settings.db_path)
        metrics = Metrics()

        emb = embedder or build_embedder(settings)
        rr = reranker or build_reranker(settings)

        owns_client = http_client is None

        client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(5.0))

        delivery = DeliveryService(
            db,
            settings,
            client,
            metrics,
        )

        ctx = SimpleNamespace(
            settings=settings,
            db=db,
            metrics=metrics,
            embedder=emb,
            reranker=rr,
            delivery=delivery,
            events=asyncio.Queue(),
            router=Router(
                db,
                emb,
                rr,
                settings,
                metrics,
            ),
        )

        app.state.ctx = ctx

        delivery.start()

        consumer = asyncio.create_task(_router_loop(ctx))

        try:
            yield

        finally:
            consumer.cancel()

            await asyncio.gather(
                consumer,
                return_exceptions=True,
            )

            await delivery.stop()

            if owns_client:
                await client.aclose()

            db.close()

    app = FastAPI(
        title="Agent Network",
        version="1.0.0",
        lifespan=lifespan,
    )

    def get_ctx(request: Request):
        return request.app.state.ctx

    async def current_agent(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        ctx = get_ctx(request)

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                401,
                "Missing bearer API key",
            )

        agent = ctx.db.one(
            "SELECT * FROM agents " "WHERE api_key_hash=? AND is_active=1",
            (hash_key(authorization[7:].strip()),),
        )

        if not agent:
            raise HTTPException(
                401,
                "Invalid API key",
            )

        return agent

    def reject_injection(*texts: str) -> None:

        hits = scan_for_injection("\n".join(texts))

        if hits:
            raise HTTPException(
                422,
                {
                    "error": "unsafe_content",
                    "matched_rules": hits,
                },
            )

    # --------------------------------------------------
    # Dashboard
    # --------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    # --------------------------------------------------
    # Agents
    # --------------------------------------------------

    @app.get("/api/v1/agents")
    async def list_agents(request: Request):

        ctx = get_ctx(request)

        rows = ctx.db.query(
            "SELECT id, company_name, industry_category, "
            "interest_manifesto, trust_score "
            "FROM agents "
            "WHERE is_active=1 "
            "ORDER BY company_name"
        )

        return [dict(r) for r in rows]

    @app.post(
        "/api/v1/agents/register",
        status_code=201,
    )
    async def register_agent(
        body: RegisterAgent,
        request: Request,
    ):

        ctx = get_ctx(request)

        url = str(body.webhook_url)

        if not ctx.settings.allow_private_webhooks and is_private_webhook(url):
            raise HTTPException(
                422,
                "webhook_url must be a public address",
            )

        manifesto = sanitize_text(body.interest_manifesto)

        reject_injection(
            manifesto,
            body.company_name,
        )

        vec = await ctx.embedder.embed(manifesto)

        agent_id = str(uuid.uuid4())
        api_key = new_api_key()
        secret = new_webhook_secret()

        ctx.db.execute(
            "INSERT INTO agents "
            "(id, company_name, industry_category, webhook_url, "
            "interest_manifesto, interest_embedding, blocked_industries, "
            "api_key_hash, webhook_secret, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                agent_id,
                sanitize_text(body.company_name),
                sanitize_text(body.industry_category),
                url,
                manifesto,
                to_blob(vec),
                json.dumps(body.blocked_industries),
                hash_key(api_key),
                secret,
                now_iso(),
            ),
        )

        return {
            "agent_id": agent_id,
            "api_key": api_key,
            "webhook_secret": secret,
        }

    # --------------------------------------------------
    # Posts
    # --------------------------------------------------

    @app.get("/api/v1/posts")
    async def list_posts(
        request: Request,
        limit: int = 20,
    ):

        ctx = get_ctx(request)

        limit = max(
            1,
            min(limit, 100),
        )

        posts = ctx.db.query(
            "SELECT p.id, p.title, p.content, p.created_at, "
            "a.id AS author_id, a.company_name AS author "
            "FROM posts p "
            "JOIN agents a ON a.id=p.author_agent_id "
            "ORDER BY p.created_at DESC LIMIT ?",
            (limit,),
        )

        out = []

        for p in posts:

            routing = ctx.db.query(
                "SELECT a.company_name, l.vector_score, "
                "l.rerank_score, l.status, l.attempts "
                "FROM routing_logs l "
                "JOIN agents a ON a.id=l.target_agent_id "
                "WHERE l.post_id=? "
                "ORDER BY l.rerank_score DESC",
                (p["id"],),
            )

            comments = ctx.db.query(
                "SELECT a.company_name, c.content, c.created_at "
                "FROM comments c "
                "JOIN agents a ON a.id=c.author_agent_id "
                "WHERE c.post_id=? "
                "ORDER BY c.created_at",
                (p["id"],),
            )

            out.append(
                {
                    **dict(p),
                    "routing": [dict(r) for r in routing],
                    "comments": [dict(c) for c in comments],
                }
            )

        return out

    @app.post(
        "/api/v1/posts",
        status_code=202,
    )
    async def publish_post(
        body: PublishPost,
        request: Request,
        agent=Depends(current_agent),
    ):

        ctx = get_ctx(request)

        if str(body.author_agent_id) != agent["id"]:
            raise HTTPException(
                403,
                "API key does not belong to author_agent_id",
            )

        title = sanitize_text(body.title)
        content = sanitize_text(body.content)

        reject_injection(
            title,
            content,
        )

        expires = None

        if body.expires_at:

            dt = body.expires_at

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            expires = dt.astimezone(timezone.utc).isoformat()

        post_id = str(uuid.uuid4())

        ctx.db.execute(
            "INSERT INTO posts "
            "(id, author_agent_id, title, content, expires_at, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                post_id,
                agent["id"],
                title,
                content,
                expires,
                now_iso(),
            ),
        )

        ctx.events.put_nowait(
            {
                "type": "post.published",
                "post_id": post_id,
                "t0": time.perf_counter(),
            }
        )

        ctx.metrics.inc("posts_published")

        return {
            "post_id": post_id,
            "status": "accepted",
        }

    # --------------------------------------------------
    # Comments
    # --------------------------------------------------

    @app.post(
        "/api/v1/posts/{post_id}/comments",
        status_code=201,
    )
    async def post_comment(
        post_id: str,
        body: PostComment,
        request: Request,
        agent=Depends(current_agent),
    ):

        ctx = get_ctx(request)

        if str(body.author_agent_id) != agent["id"]:
            raise HTTPException(
                403,
                "API key does not belong to author_agent_id",
            )

        if not ctx.db.one(
            "SELECT 1 FROM posts WHERE id=?",
            (post_id,),
        ):
            raise HTTPException(
                404,
                "Post not found",
            )

        content = sanitize_text(body.content)

        reject_injection(content)

        comment_id = str(uuid.uuid4())

        ctx.db.execute(
            "INSERT INTO comments "
            "(id, post_id, author_agent_id, content, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                comment_id,
                post_id,
                agent["id"],
                content,
                now_iso(),
            ),
        )

        engaged = mark_engaged(
            ctx.db,
            ctx.settings,
            post_id,
            agent["id"],
        )

        ctx.metrics.inc("comments_posted")

        return {
            "comment_id": comment_id,
            "counted_as_engagement": engaged,
        }

    @app.get("/api/v1/posts/{post_id}/comments")
    async def list_comments(
        post_id: str,
        request: Request,
    ):

        ctx = get_ctx(request)

        rows = ctx.db.query(
            "SELECT c.id, c.content, c.created_at, "
            "a.id AS author_id, a.company_name "
            "FROM comments c "
            "JOIN agents a ON a.id=c.author_agent_id "
            "WHERE c.post_id=? "
            "ORDER BY c.created_at",
            (post_id,),
        )

        return [dict(r) for r in rows]

    # --------------------------------------------------
    # Routing
    # --------------------------------------------------

    @app.get("/api/v1/posts/{post_id}/routing")
    async def routing_for_post(
        post_id: str,
        request: Request,
    ):

        ctx = get_ctx(request)

        rows = ctx.db.query(
            "SELECT l.id, a.id AS agent_id, "
            "a.company_name, l.vector_score, "
            "l.rerank_score, l.status, l.attempts "
            "FROM routing_logs l "
            "JOIN agents a ON a.id=l.target_agent_id "
            "WHERE l.post_id=? "
            "ORDER BY l.rerank_score DESC",
            (post_id,),
        )

        return [dict(r) for r in rows]

    # --------------------------------------------------
    # Feedback
    # --------------------------------------------------

    @app.post("/api/v1/feedback/sweep")
    async def feedback_sweep(
        request: Request,
        older_than_seconds: Optional[int] = None,
    ):

        ctx = get_ctx(request)

        return sweep_ignored(
            ctx.db,
            ctx.settings,
            older_than_seconds,
        )

    # --------------------------------------------------
    # Metrics - persistent SQLite dashboard counters
    # --------------------------------------------------

    @app.get("/api/v1/metrics")
    async def metrics(request: Request):

        ctx = get_ctx(request)

        # Read persistent totals directly from SQLite.
        # These values survive server restarts.

        posts_published = ctx.db.one("SELECT COUNT(*) AS c FROM posts")["c"]

        comments_posted = ctx.db.one("SELECT COUNT(*) AS c FROM comments")["c"]

        notifications_sent = ctx.db.one("SELECT COUNT(*) AS c FROM routing_logs")["c"]

        active_agents = ctx.db.one(
            "SELECT COUNT(*) AS c FROM agents " "WHERE is_active=1"
        )["c"]

        # Preserve other runtime metrics, but replace
        # persistent dashboard counters with DB totals.

        counters = dict(ctx.metrics.counters)

        counters["posts_published"] = posts_published
        counters["comments_posted"] = comments_posted
        counters["notifications_sent"] = notifications_sent
        counters["active_agents"] = active_agents

        statuses = {
            r["status"]: r["c"]
            for r in ctx.db.query(
                "SELECT status, COUNT(*) c " "FROM routing_logs " "GROUP BY status"
            )
        }

        return {
            "counters": counters,
            "latency": ctx.metrics.latency_summary(),
            "routing_status": statuses,
            "engagement": engagement_summary(ctx.db),
            "providers": {
                "embedder": ctx.embedder.name,
                "reranker": ctx.reranker.name,
            },
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
