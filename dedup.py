"""Request deduplication for the Octer plugin.

Filters duplicate tool_request deliveries that can occur when the WebSocket
reconnects and the cloud replays in-flight requests.  Mirrors the JS
implementation at octer-channel/src/channel/chat-queue.js (RequestDedup).
"""

import time


class RequestDedup:
    def __init__(self, ttl_ms: int = 60_000, max_entries: int = 1000) -> None:
        self._seen: dict[str, float] = {}
        self._ttl_ms = ttl_ms
        self._max_entries = max_entries

    def try_record(self, request_id: str) -> bool:
        """Return True if this is the first time we see request_id; False if duplicate."""
        self._cleanup()
        if request_id in self._seen:
            return False
        self._seen[request_id] = time.time() * 1000
        return True

    def _cleanup(self) -> None:
        if len(self._seen) <= self._max_entries:
            return
        now = time.time() * 1000
        for key in list(self._seen):
            if now - self._seen[key] > self._ttl_ms:
                del self._seen[key]
