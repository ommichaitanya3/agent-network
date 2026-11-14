import os
from dataclasses import dataclass, field
from typing import Optional


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "agent_network.db"))
    embedding_dim: int = field(default_factory=lambda: _i("EMBEDDING_DIM", 512))

    # Routing pipeline
    top_k: int = 50                    # Stage 2 candidates
    min_dense_score: float = 0.05      # Stage 2 floor, drops clearly unrelated agents
    relevance_threshold: float = field(default_factory=lambda: _f("RELEVANCE_THRESHOLD", 0.75))
    max_notified: int = 5              # Stage 3 cap
    min_trust_score: float = 0.3

    # Optional external AI providers
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    use_llm_judge: bool = field(default_factory=lambda: os.getenv("USE_LLM_JUDGE", "0") == "1")

    # Security
    allow_private_webhooks: bool = field(
        default_factory=lambda: os.getenv("ALLOW_PRIVATE_WEBHOOKS", "1") == "1"
    )

    # Webhook delivery
    delivery_workers: int = 4
    delivery_max_attempts: int = 4
    delivery_base_delay: float = 0.5
    circuit_failure_threshold: int = 3
    circuit_cooldown: float = 30.0

    # Feedback loop
    ignore_after_seconds: int = 300
    ignore_shift_after: int = 3        # shift away only after this many ignores
    preference_lr: float = 0.1
