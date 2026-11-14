"""Closed-loop feedback: engagement tracking and dynamic preference vectors."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from .embeddings import from_blob, to_blob


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n > 0 else v


def shift_agent_vector(db, agent_id: str, post_id: str, lr: float) -> bool:
    """lr > 0 pulls the agent's vector toward the post; lr < 0 pushes it away."""
    agent = db.one("SELECT interest_embedding FROM agents WHERE id=?", (agent_id,))
    post = db.one("SELECT post_embedding FROM posts WHERE id=?", (post_id,))
    if not agent or not post or not agent["interest_embedding"] or not post["post_embedding"]:
        return False
    new = _unit(from_blob(agent["interest_embedding"]) + lr * from_blob(post["post_embedding"]))
    db.execute("UPDATE agents SET interest_embedding=? WHERE id=?", (to_blob(new), agent_id))
    return True


def mark_engaged(db, settings, post_id: str, agent_id: str) -> bool:
    cur = db.execute(
        "UPDATE routing_logs SET status='ENGAGED', updated_at=? "
        "WHERE post_id=? AND target_agent_id=? AND status='NOTIFIED'",
        (_now().isoformat(), post_id, agent_id))
    if cur.rowcount > 0:
        shift_agent_vector(db, agent_id, post_id, +settings.preference_lr)
        return True
    return False


def sweep_ignored(db, settings, older_than_seconds: int | None = None) -> dict:
    """Mark stale NOTIFIED rows IGNORED. Agents with repeated ignores drift away from those topics."""
    age = settings.ignore_after_seconds if older_than_seconds is None else older_than_seconds
    cutoff = (_now() - timedelta(seconds=age)).isoformat()
    stale = db.query("SELECT id, post_id, target_agent_id FROM routing_logs "
                     "WHERE status='NOTIFIED' AND created_at <= ?", (cutoff,))
    for row in stale:
        db.execute("UPDATE routing_logs SET status='IGNORED', updated_at=? WHERE id=?",
                   (_now().isoformat(), row["id"]))
    shifted = 0
    for row in stale:
        total_ignored = db.one(
            "SELECT COUNT(*) c FROM routing_logs WHERE target_agent_id=? AND status='IGNORED'",
            (row["target_agent_id"],))["c"]
        if total_ignored >= settings.ignore_shift_after:
            if shift_agent_vector(db, row["target_agent_id"], row["post_id"],
                                  -settings.preference_lr):
                shifted += 1
    return {"marked_ignored": len(stale), "vectors_shifted_away": shifted}


def engagement_summary(db) -> dict:
    rows = db.query("""
        SELECT a.id, a.company_name,
               SUM(CASE WHEN l.status='ENGAGED' THEN 1 ELSE 0 END) AS engaged,
               SUM(CASE WHEN l.status IN ('NOTIFIED','ENGAGED','IGNORED') THEN 1 ELSE 0 END) AS sent
        FROM agents a JOIN routing_logs l ON l.target_agent_id = a.id
        GROUP BY a.id ORDER BY a.company_name""")
    per_agent = [{"agent_id": r["id"], "company_name": r["company_name"],
                  "notifications": r["sent"], "engaged": r["engaged"],
                  "engagement_rate": round(r["engaged"] / r["sent"], 3) if r["sent"] else None}
                 for r in rows]
    sent = sum(r["sent"] for r in rows)
    engaged = sum(r["engaged"] for r in rows)
    return {"overall_engagement_rate": round(engaged / sent, 3) if sent else None,
            "per_agent": per_agent}
