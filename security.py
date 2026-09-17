"""Webhook authentication and small in-process abuse controls."""
import base64
import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit, urlunsplit


def _public_request_url(request):
    """Return the URL providers signed, accounting for a configured tunnel URL."""
    public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base:
        return request.url
    parsed = urlsplit(request.url)
    base = urlsplit(public_base)
    return urlunsplit((base.scheme, base.netloc, parsed.path, parsed.query, ""))


def valid_twilio_request(request):
    """Validate Twilio's HMAC-SHA1 request signature."""
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    signature = request.headers.get("X-Twilio-Signature", "")
    if not token or not signature:
        return False
    signed = _public_request_url(request)
    for key in sorted(request.form):
        for value in request.form.getlist(key):
            signed += key + value
    digest = hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def valid_meta_request(request):
    """Validate Meta's HMAC-SHA256 body signature."""
    secret = os.environ.get("META_APP_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[7:])


class RateLimiter:
    """Per-key sliding-window limiter. State is intentionally process-local."""
    def __init__(self, limit=30, window_seconds=60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True
