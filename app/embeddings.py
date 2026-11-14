"""Embeddings.

`LocalEmbedder` runs fully offline: hashed bag-of-words plus a small business-concept
lexicon, so "Diwali offer" and "UPI payment" land near each other without a model download.
`OpenAIEmbedder` (text-embedding-3-small) is used automatically when OPENAI_API_KEY is set.
"""
import hashlib
import re
from typing import Protocol

import httpx
import numpy as np

STOPWORDS = frozenset(
    "a an the and or of for to in on at by with from is are be as it its this that these those "
    "our your their we you they will can all any more most during over under into about also "
    "than then such per via only just new now".split()
)


def _stem(tok: str) -> str:
    for suf in ("ing", "es", "s"):
        if tok.endswith(suf) and len(tok) > len(suf) + 3:
            return tok[: -len(suf)]
    return tok


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(t) for t in toks if t not in STOPWORDS and len(t) > 1 and not t.isdigit()]


# Business concepts: each maps to words that signal it (stemmed on load).
CONCEPTS: dict[str, list[str]] = {
    "festive": ["diwali", "festive", "festival", "holi", "eid", "christmas", "navratri",
                "dussehra", "onam", "pongal", "season", "celebration"],
    "food": ["food", "restaurant", "meal", "dining", "cuisine", "grocery", "swiggy", "zomato", "eat"],
    "logistics": ["delivery", "logistics", "shipping", "courier", "lastmile", "fulfilment", "warehouse"],
    "payments": ["upi", "payment", "pay", "wallet", "cashback", "phonepe", "gpay", "paytm",
                 "checkout", "transaction", "fintech"],
    "cards": ["card", "credit", "debit", "emi", "cardholder", "reward", "banking", "bank"],
    "ecommerce": ["ecommerce", "commerce", "shopping", "shopper", "marketplace", "flipkart",
                  "merchant", "retail", "seller"],
    "discount": ["discount", "offer", "promo", "promotion", "deal", "coupon", "off", "savings", "sale"],
    "auto": ["bike", "motorcycle", "scooter", "vehicle", "auto", "wheeler", "ev", "showroom",
             "dealership", "dealer", "car"],
    "loans": ["loan", "financing", "finance", "lending", "emi", "mortgage"],
}
_CONCEPT_INDEX: dict[str, set[str]] = {}
for _concept, _words in CONCEPTS.items():
    for _w in _words:
        _CONCEPT_INDEX.setdefault(_stem(_w), set()).add(_concept)


def extract_concepts(text: str) -> set[str]:
    found: set[str] = set()
    for tok in tokenize(text):
        found |= _CONCEPT_INDEX.get(tok, set())
    return found


class Embedder(Protocol):
    name: str

    async def embed(self, text: str) -> np.ndarray: ...


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n).astype(np.float32) if n > 0 else v.astype(np.float32)


class LocalEmbedder:
    name = "local-hashing"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _bucket(self, key: str) -> tuple[int, float]:
        h = hashlib.md5(key.encode()).digest()
        return int.from_bytes(h[:4], "big") % self.dim, (1.0 if h[4] % 2 == 0 else -1.0)

    async def embed(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in tokenize(text):
            i, s = self._bucket("w:" + tok)
            v[i] += s
        for concept in extract_concepts(text):
            i, s = self._bucket("c:" + concept)
            v[i] += 2.0 * s
        return _normalize(v)


class OpenAIEmbedder:
    name = "openai-text-embedding-3-small"

    def __init__(self, api_key: str, dim: int = 512, model: str = "text-embedding-3-small"):
        self.api_key, self.dim, self.model = api_key, dim, model

    async def embed(self, text: str) -> np.ndarray:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text, "dimensions": self.dim},
            )
            r.raise_for_status()
        return _normalize(np.asarray(r.json()["data"][0]["embedding"], dtype=np.float32))


def build_embedder(settings) -> Embedder:
    if settings.openai_api_key:
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_dim)
    return LocalEmbedder(settings.embedding_dim)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
