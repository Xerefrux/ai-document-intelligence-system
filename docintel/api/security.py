"""Security helpers: API key check, rate limiting, security headers, safe file names."""

from __future__ import annotations

import hmac
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import Settings

PUBLIC_PATHS = ("/api/health",)


def api_key_ok(settings: Settings, request: Request) -> bool:
    if not settings.api_key:
        return True
    supplied = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization") or ""
    if not supplied and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    return hmac.compare_digest(supplied.encode(), settings.api_key.encode())


def require_key(settings: Settings):
    """FastAPI dependency factory: every /api route except /api/health needs the key when one is configured."""

    def dep(request: Request) -> None:
        if request.url.path in PUBLIC_PATHS:
            return
        if not api_key_ok(settings, request):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "A valid X-API-Key header is required."})

    return dep


class RateLimiter:
    """Sliding window per client. In memory, so it limits one process; behind several, put the limit on the proxy."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()
        self.last_sweep = time.monotonic()

    def check(self, key: str) -> float | None:
        """None when allowed, otherwise the seconds to wait."""
        if self.per_minute <= 0:
            return None
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            if len(q) >= self.per_minute:
                return max(1.0, 60.0 - (now - q[0]))
            q.append(now)
            if now - self.last_sweep > 300:
                self.last_sweep = now
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                    del self.hits[k]
        return None


def client_key(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, limiter: RateLimiter):
        super().__init__(app)
        self.settings, self.limiter = settings, limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and path not in PUBLIC_PATHS:
            wait = self.limiter.check(client_key(request, self.settings.trust_proxy_headers))
            if wait is not None:
                return JSONResponse({"error": {"code": "RATE_LIMITED", "message": f"Too many requests. Try again in {int(wait)} seconds."}}, status_code=429, headers={"Retry-After": str(int(wait))})
            clen = request.headers.get("content-length")
            if clen and clen.isdigit() and request.method in ("POST", "PUT", "PATCH"):
                if int(clen) > self.settings.max_request_bytes + 1_000_000:
                    return JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "The request is larger than the server accepts."}}, status_code=413)
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if path.startswith("/api"):
            h.setdefault("Cache-Control", "no-store")
            if "/file" in path or "/image" in path:
                h["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:"
        else:
            h.setdefault("Content-Security-Policy", CSP)
        return response


_BAD = re.compile(r"[\x00-\x1f\x7f\\/:*?\"<>|]+")


def safe_display_name(name: str | None) -> str:
    """A file name that is safe to store and show: no path parts, no control characters, bounded length."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFC", _BAD.sub("_", base)).strip(" .")
    if not base:
        base = "document"
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 190 - len(ext)] + dot + ext) if dot else base[:200]
    return base
