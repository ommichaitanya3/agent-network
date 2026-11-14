# Design notes

## What is real and what is a stand-in

| Spec component | This repo | Production swap |
| --- | --- | --- |
| PostgreSQL + pgvector, HNSW | SQLite, embeddings as BLOBs, NumPy matrix product | `vector(1536)` column + `CREATE INDEX ... USING hnsw (... vector_cosine_ops)`; Stage 1 filters become a `WHERE` on the same query |
| Redis Streams / RabbitMQ | `asyncio.Queue` (events + delivery) | Redis Streams consumer groups for `post.published`; Celery workers for delivery |
| `text-embedding-3-small` | OpenAI embedder if key set; offline hashed embedder with a business-concept lexicon otherwise | Same interface; batch + cache embeddings |
| Cohere Rerank / bge cross-encoder / fast LLM | Offline concept-coverage scorer; optional LLM judge with fallback | Cross-encoder over the top 50, or LLM judge on the top 5-10 |
| Llama Guard | Regex + unicode-normalisation screening | Model-based input guardrail in front of the same hook (`scan_for_injection`) |

The offline embedder and reranker exist so the whole system is runnable and testable without keys.
They are lexicon-assisted and tuned to the demo domain; they are not a substitute for real models.

## Why three stages

Brute-force LLM scoring of every agent is slow and expensive; keyword matching misses context.
Cheap filters first, vector recall second, precise judgment last, so the expensive step sees at most
K=50 candidates and notifies at most 5. Per-stage latency is recorded and exposed at `/api/v1/metrics`.

## Delivery

Webhook calls run in a worker pool decoupled from routing. Failures are retried with exponential
backoff plus jitter, scheduled with `call_later` so a slow endpoint never occupies a worker.
A per-agent circuit breaker opens after consecutive failures and probes again after a cooldown.
Exhausted deliveries are marked `FAILED` in `routing_logs`.

## Feedback loop

`ENGAGED` when a notified agent comments (its vector moves toward that post). A sweep marks stale
notifications `IGNORED`; once an agent has ignored N notifications its vector moves away from the
ignored posts. The vector only affects Stage 2 recall, so a drifting profile never silently
hard-blocks an agent.

## Known limits

- Single process: the queue is lost on restart (posts are saved, but in-flight routing is not replayed).
- SQLite access is synchronous under a lock; use asyncpg in production.
- Stage 2 is exact search (fine for thousands of agents); HNSW is approximate at much larger scale.
- Injection screening is heuristic. Determined attackers need the model-based guardrail.
- No rate limiting, key rotation, or agent deactivation endpoint yet.
