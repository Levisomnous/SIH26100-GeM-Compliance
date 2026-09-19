"""Security helpers: officer auth, admin/reset gating, rate limiting, upload checks.

Deliberately framework-free (no FastAPI imports) so the logic can be unit-tested
on its own; backend/main.py wires these into FastAPI dependencies.

Configuration is read from environment variables on every call to get_config(),
so it can be changed without re-importing (and tests can monkeypatch it):

  OFFICER_TOKENS   "alice:<token>,bob:<token>"  officer accounts (token >= 16 chars).
                   If unset, the API runs in OPEN DEV MODE (no auth) and warns.
  ADMIN_TOKEN      token required for DELETE /api/reset (>= 16 chars)
  ENABLE_RESET     "true" to allow DELETE /api/reset at all (default: false)
  APP_ENV          "production" refuses to start without OFFICER_TOKENS
  MAX_UPLOAD_MB    max PDF size (default 10)
  RATE_LIMIT_PER_MIN  per-client requests/minute on write endpoints (default 30; 0 = off)
  TRUST_PROXY      "true" to use the first X-Forwarded-For hop as client IP
"""
import hmac
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import BinaryIO, Deque, Dict, Optional, Tuple

MIN_TOKEN_LEN = 16
DEFAULT_MAX_UPLOAD_MB = 10
DEFAULT_RATE_LIMIT = 30


class UploadTooLarge(Exception):
    pass


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def parse_officer_tokens(raw: Optional[str]) -> Dict[str, str]:
    """'alice:tok1,bob:tok2' -> {'tok1': 'alice', 'tok2': 'bob'}. Raises ValueError on bad input."""
    out: Dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError("OFFICER_TOKENS entries must look like name:token")
        name, tok = (x.strip() for x in part.split(":", 1))
        if not name:
            raise ValueError("OFFICER_TOKENS entry has an empty name")
        if len(tok) < MIN_TOKEN_LEN:
            raise ValueError(f"OFFICER_TOKENS token for '{name}' must be at least {MIN_TOKEN_LEN} characters")
        if tok in out:
            raise ValueError("OFFICER_TOKENS contains a duplicate token")
        out[tok] = name
    return out


@dataclass(frozen=True)
class Config:
    officer_tokens: Dict[str, str]          # token -> officer name
    admin_token: Optional[str]
    enable_reset: bool
    production: bool
    max_upload_bytes: int
    rate_limit_per_min: int
    trust_proxy: bool

    @property
    def auth_enabled(self) -> bool:
        return bool(self.officer_tokens)


def get_config() -> Config:
    admin = (os.environ.get("ADMIN_TOKEN") or "").strip() or None
    if admin is not None and len(admin) < MIN_TOKEN_LEN:
        raise ValueError(f"ADMIN_TOKEN must be at least {MIN_TOKEN_LEN} characters")
    try:
        max_mb = float(os.environ.get("MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
        rate = int(os.environ.get("RATE_LIMIT_PER_MIN", DEFAULT_RATE_LIMIT))
    except ValueError as e:
        raise ValueError(f"invalid numeric setting: {e}")
    return Config(
        officer_tokens=parse_officer_tokens(os.environ.get("OFFICER_TOKENS")),
        admin_token=admin,
        enable_reset=_truthy(os.environ.get("ENABLE_RESET")),
        production=(os.environ.get("APP_ENV", "").strip().lower() == "production"),
        max_upload_bytes=int(max_mb * 1024 * 1024),
        rate_limit_per_min=rate,
        trust_proxy=_truthy(os.environ.get("TRUST_PROXY")),
    )


def validate_startup(cfg: Config) -> Optional[str]:
    """Fail fast on unsafe production config. Returns a warning string for dev mode, else None."""
    if cfg.production and not cfg.auth_enabled:
        raise RuntimeError("APP_ENV=production requires OFFICER_TOKENS to be set")
    if cfg.production and cfg.enable_reset:
        raise RuntimeError("ENABLE_RESET must not be enabled when APP_ENV=production")
    if not cfg.auth_enabled:
        return "OFFICER_TOKENS not set: officer endpoints are OPEN (dev mode only)."
    return None


def extract_token(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    return None


def authenticate(presented: Optional[str], tokens: Dict[str, str]) -> Optional[str]:
    """Constant-time token check. Returns the officer's name, or None."""
    if not presented:
        return None
    found: Optional[str] = None
    p = presented.encode("utf-8")
    for tok, name in tokens.items():          # no early exit: don't leak which token matched
        if hmac.compare_digest(p, tok.encode("utf-8")):
            found = name
    return found


def is_admin(presented: Optional[str], cfg: Config) -> bool:
    return bool(presented and cfg.admin_token
                and hmac.compare_digest(presented.encode("utf-8"), cfg.admin_token.encode("utf-8")))


def reset_decision(cfg: Config, presented: Optional[str]) -> Tuple[bool, int, str]:
    """(allowed, http_status_if_denied, message) for DELETE /api/reset."""
    if not cfg.enable_reset:
        return False, 403, "reset is disabled (set ENABLE_RESET=true to allow it on non-production setups)"
    if cfg.admin_token:
        if is_admin(presented, cfg):
            return True, 200, ""
        return False, 401, "valid admin token required"
    if cfg.auth_enabled:
        return False, 403, "set ADMIN_TOKEN to allow reset when officer auth is enabled"
    return True, 200, ""                      # open dev mode


class RateLimiter:
    """In-process sliding-window limiter. Per worker process — with N uvicorn
    workers the effective limit is N x limit; use a reverse proxy/Redis for a
    hard global limit."""

    def __init__(self, window_s: float = 60.0, max_keys: int = 10000):
        self.window = window_s
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, now: Optional[float] = None) -> Tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        if limit <= 0:
            return True, 0
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > self.max_keys:
                self._prune(now)
            q = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(q[0] + self.window - now) + 1)
            q.append(now)
            return True, 0

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def read_limited(fileobj: BinaryIO, max_bytes: int, chunk: int = 1 << 20) -> bytes:
    """Read at most max_bytes; raises UploadTooLarge as soon as the limit is exceeded."""
    buf = bytearray()
    while True:
        part = fileobj.read(min(chunk, max_bytes + 1 - len(buf)))
        if not part:
            break
        buf += part
        if len(buf) > max_bytes:
            raise UploadTooLarge(max_bytes)
    return bytes(buf)


def looks_like_pdf(data: bytes) -> bool:
    """PDF header must appear in the first 1024 bytes (per the PDF spec)."""
    return b"%PDF-" in data[:1024]
