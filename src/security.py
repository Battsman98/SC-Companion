"""Security controls shared by the website and Discord bot."""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict, deque
from threading import Lock


SENSITIVE_VALUE = re.compile(
    r"(?i)(authorization|cookie|token|secret|password|code)\s*[:=]\s*([^\s,;]+)"
)


class SecretRedactionFilter(logging.Filter):
    """Prevent common credential shapes from being written to application logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = SENSITIVE_VALUE.sub(r"\1=[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_secret_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
            handler.addFilter(SecretRedactionFilter())


class SlidingWindowLimiter:
    """Small in-process abuse limiter suitable for a single bot/web instance."""

    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(0.1, window_seconds)
        self.max_keys = max(1, max_keys)
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            # Entries are ordered by their last accepted event. Reclaim idle
            # identities even when they never send another request.
            while self._events:
                oldest = next(iter(self._events))
                if self._events[oldest][-1] > cutoff:
                    break
                self._events.popitem(last=False)
            events = self._events.get(key)
            if events is None:
                # Keep active limits intact under an identity-flood attack.
                if len(self._events) >= self.max_keys:
                    return False, max(1, int(self.window_seconds + 0.999))
                events = self._events[key] = deque()
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - events[0]) + 0.999))
                return False, retry_after
            events.append(now)
            self._events.move_to_end(key)
            return True, 0
