"""Trust & safety: API keys, HMAC webhook signing, prompt-injection screening, SSRF guard."""
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
import unicodedata
from typing import Optional
from urllib.parse import urlparse


def new_api_key() -> str:
    return "ak_" + secrets.token_urlsafe(32)


def new_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str,
                     tolerance: int = 300, now: Optional[float] = None) -> bool:
    """Receiving agents call this. Rejects bad signatures and replays older than `tolerance` s."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False
    return hmac.compare_digest(sign_payload(secret, timestamp, body), signature or "")


_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Normalise unicode and strip control / invisible characters used to hide payloads."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    return text.strip()


_INJECTION_PATTERNS = {
    "ignore_instructions": r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|context)",
    "exfiltrate_secrets": r"(reveal|print|output|show|leak|send|share|expose).{0,40}(system prompt|api[\s_-]?keys?|secrets?|passwords?|credentials|private keys?|confidential)",
    "role_hijack": r"\byou are now\b|\bact as (an? )?(admin|root|system|developer)\b|\bnew instructions?:",
    "prompt_markup": r"</?\s*(system|assistant|instructions?)\s*>|\[/?inst\]|\bbegin (system|prompt)\b",
    "override_safety": r"override\s+(the\s+)?(safety|guardrails?|polic(y|ies)|rules)",
}
_COMPILED = {k: re.compile(v, re.I | re.S) for k, v in _INJECTION_PATTERNS.items()}


def scan_for_injection(text: str) -> list[str]:
    """Heuristic input guardrail. In production, put a model (e.g. Llama Guard) behind this."""
    norm = sanitize_text(text)
    return [name for name, rx in _COMPILED.items() if rx.search(norm)]


def is_private_webhook(url: str) -> bool:
    """True if the webhook host is localhost / a private, loopback or link-local IP literal."""
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", ""} or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
