"""Multi-stage relevance routing: SQL guardrails -> vector top-K -> rerank + threshold."""
import json
import time
from dataclasses import dataclass

import numpy as np

from .embeddings import from_blob


@dataclass
class Candidate:
    agent_id: str
    company_name: str
    webhook_url: str
    webhook_secret: str
    vector_score: float
    rerank_score: float = 0.0


STAGE1_SQL = """
SELECT id, company_name, industry_category, interest_manifesto, interest_embedding,
       webhook_url, webhook_secret
FROM agents
WHERE is_active = 1
  AND id != :author_id
  AND trust_score >= :min_trust
  AND interest_embedding IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM json_each(agents.blocked_industries)
                  WHERE lower(value) = lower(:author_industry))
  AND lower(industry_category) NOT IN (SELECT lower(value) FROM json_each(:author_blocked))
"""


class Router:
    def __init__(self, db, embedder, reranker, settings, metrics):
        self.db, self.embedder, self.reranker = db, embedder, reranker
        self.settings, self.metrics = settings, metrics

    def stage1(self, author) -> list:
        """Hard metadata guardrails: inactive agents, the author, blocklists, low trust."""
        return self.db.query(STAGE1_SQL, {
            "author_id": author["id"],
            "min_trust": self.settings.min_trust_score,
            "author_industry": author["industry_category"],
            "author_blocked": author["blocked_industries"] or "[]",
        })

    def stage2(self, post_vec: np.ndarray, rows: list) -> list[tuple]:
        """Dense cosine search. Brute-force matrix product here; pgvector HNSW in production."""
        if not rows:
            return []
        matrix = np.vstack([from_blob(r["interest_embedding"]) for r in rows])
        scores = np.clip(matrix @ post_vec, 0.0, 1.0)
        k = min(self.settings.top_k, len(rows))
        top = np.argsort(-scores)[:k]
        return [(rows[i], float(scores[i])) for i in top
                if scores[i] >= self.settings.min_dense_score]

    async def stage3(self, post_text: str, scored: list[tuple]) -> list[Candidate]:
        """Joint rerank, keep score >= threshold, cap at max_notified."""
        out = []
        for row, dense in scored:
            final = await self.reranker.score(post_text, row["interest_manifesto"], dense)
            if final >= self.settings.relevance_threshold:
                out.append(Candidate(row["id"], row["company_name"], row["webhook_url"],
                                     row["webhook_secret"], round(dense, 4), final))
        out.sort(key=lambda c: c.rerank_score, reverse=True)
        return out[: self.settings.max_notified]

    async def route(self, post_text: str, post_vec: np.ndarray, author) -> list[Candidate]:
        t0 = time.perf_counter()
        rows = self.stage1(author)
        t1 = time.perf_counter()
        scored = self.stage2(post_vec, rows)
        t2 = time.perf_counter()
        result = await self.stage3(post_text, scored)
        t3 = time.perf_counter()
        self.metrics.observe("stage1_sql_ms", (t1 - t0) * 1000)
        self.metrics.observe("stage2_vector_ms", (t2 - t1) * 1000)
        self.metrics.observe("stage3_rerank_ms", (t3 - t2) * 1000)
        self.metrics.inc("candidates_eligible", len(rows))
        self.metrics.inc("candidates_after_vector", len(scored))
        self.metrics.inc("candidates_notified", len(result))
        return result
