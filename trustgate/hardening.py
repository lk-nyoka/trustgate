"""Production middleware: per-client rate limiting, security headers, structured request logs."""
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("trustgate.http")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Inline plus same-origin assets only; nothing is loaded from third parties.
    "Content-Security-Policy": ("default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                                "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
}


class RateLimiter:
    """Sliding window, in memory (per process). Fine for one instance; use Redis if you scale out."""

    def __init__(self, limit, window_s=60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(self.window - (now - q[0])))
            q.append(now)
            return True, 0


def client_ip(request, trust_proxy):
    if trust_proxy:  # behind Render/other proxy: the left-most X-Forwarded-For is the client
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class Hardening(BaseHTTPMiddleware):
    def __init__(self, app, api_limit=120, login_limit=10, trust_proxy=False, hsts=False):
        super().__init__(app)
        self.api = RateLimiter(api_limit)
        self.login = RateLimiter(login_limit)  # brute-force guard for the password form
        self.trust_proxy, self.hsts = trust_proxy, hsts

    async def dispatch(self, request, call_next):
        rid = uuid.uuid4().hex[:12]
        ip = client_ip(request, self.trust_proxy)
        path = request.url.path
        started = time.perf_counter()
        limiter = self.login if (path == "/login" and request.method == "POST") else self.api
        if path != "/healthz":
            ok, retry = limiter.allow(ip)
            if not ok:
                resp = JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                    headers={"Retry-After": str(retry)})
                self._finish(resp, rid)
                self._log(request, ip, rid, 429, started)
                return resp
        resp = await call_next(request)
        self._finish(resp, rid)
        self._log(request, ip, rid, resp.status_code, started)
        return resp

    def _finish(self, resp, rid):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if self.hsts:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        resp.headers["X-Request-Id"] = rid

    def _log(self, request, ip, rid, status, started):
        # Never log headers, cookies, bodies or query strings: they carry credentials.
        log.info(json.dumps({"rid": rid, "ip": ip, "method": request.method, "path": request.url.path,
                             "status": status, "ms": round((time.perf_counter() - started) * 1000, 1)}))
