"""Asynchronous message queue abstractions and distributed-ready Gemini API rate limiter."""

from __future__ import annotations

import importlib
import json
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field


class QueueMessage(BaseModel):
    """Envelope dispatched from the Job API Gateway to Async Processing Workers."""

    receipt_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    job_id: str = Field(..., min_length=6, max_length=64)
    session_id: str = Field(..., min_length=3, max_length=64)
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delivery_count: int = Field(default=1, ge=1)


class JobQueueProtocol(Protocol):
    """Broker contract supporting Redis Streams/Lists in K8s and thread-safe memory locally."""

    def enqueue(self, job_id: str, session_id: str) -> QueueMessage:
        """Publish a job message if not already pending or in-flight."""

    def dequeue(self, timeout_seconds: float = 0.2) -> QueueMessage | None:
        """Claim next pending message into in-flight lease state."""

    def ack(self, receipt_id: str) -> None:
        """Acknowledge successful completion and remove message from in-flight tracking."""

    def nack(self, receipt_id: str, requeue: bool = True) -> None:
        """Release failed message back to pending queue (with incremented delivery count) or drop."""

    def queue_depth(self) -> int:
        """Return count of waiting messages (used by Kubernetes HPA)."""

    def oldest_message_age_seconds(self) -> float:
        """Return age in seconds of the oldest waiting message (used by Kubernetes HPA)."""

    def check_health(self) -> bool:
        """Return True if broker connection is healthy."""


class InMemoryJobQueue:
    """Thread-safe FIFO queue with deduplication and in-flight lease tracking."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: deque[QueueMessage] = deque()
        self._inflight: dict[str, QueueMessage] = {}
        self._active_job_ids: set[str] = set()

    def enqueue(self, job_id: str, session_id: str) -> QueueMessage:
        """Enqueue a job once, preventing duplicate concurrent execution of the same `job_id`."""
        with self._cond:
            for existing in self._pending:
                if existing.job_id == job_id:
                    return existing
            for existing in self._inflight.values():
                if existing.job_id == job_id:
                    return existing

            msg = QueueMessage(job_id=job_id, session_id=session_id)
            self._pending.append(msg)
            self._active_job_ids.add(job_id)
            self._cond.notify()
            return msg

    def dequeue(self, timeout_seconds: float = 0.2) -> QueueMessage | None:
        """Pop oldest message into in-flight map, waiting up to `timeout_seconds` if empty."""
        with self._cond:
            if not self._pending and timeout_seconds > 0:
                self._cond.wait(timeout=timeout_seconds)
            if not self._pending:
                return None
            msg = self._pending.popleft()
            self._inflight[msg.receipt_id] = msg
            return msg

    def ack(self, receipt_id: str) -> None:
        """Mark in-flight message as completed."""
        with self._cond:
            msg = self._inflight.pop(receipt_id, None)
            if msg is not None:
                self._active_job_ids.discard(msg.job_id)

    def nack(self, receipt_id: str, requeue: bool = True) -> None:
        """Return message to pending queue on transient failure or drop on terminal failure."""
        with self._cond:
            msg = self._inflight.pop(receipt_id, None)
            if msg is None:
                return
            if requeue:
                requeued = msg.model_copy(
                    update={
                        "receipt_id": f"msg_{uuid4().hex[:12]}",
                        "delivery_count": msg.delivery_count + 1,
                    }
                )
                self._pending.append(requeued)
                self._cond.notify()
            else:
                self._active_job_ids.discard(msg.job_id)

    def queue_depth(self) -> int:
        """Return number of messages waiting to be claimed by a worker."""
        with self._cond:
            return len(self._pending)

    def oldest_message_age_seconds(self) -> float:
        """Return age in seconds of the head-of-line message (0.0 when queue is empty)."""
        with self._cond:
            if not self._pending:
                return 0.0
            oldest = self._pending[0].enqueued_at
        delta = (datetime.now(UTC) - oldest).total_seconds()
        return round(max(0.0, delta), 3)

    def check_health(self) -> bool:
        """In-memory queue is always available while process is alive."""
        return True


class RedisJobQueue:
    """Redis-backed reliable queue (`LPUSH` / `BRPOPLPUSH`) for multi-pod Kubernetes scaling."""

    def __init__(self, redis_url: str, namespace: str = "doclayout") -> None:
        redis_mod: Any = importlib.import_module("redis")
        self._client: Any = redis_mod.Redis.from_url(redis_url, decode_responses=True)
        self._pending_key = f"{namespace}:queue:pending"
        self._inflight_key = f"{namespace}:queue:inflight"

    def enqueue(self, job_id: str, session_id: str) -> QueueMessage:
        """Serialize and push message onto Redis pending list."""
        msg = QueueMessage(job_id=job_id, session_id=session_id)
        self._client.lpush(self._pending_key, msg.model_dump_json())
        return msg

    def dequeue(self, timeout_seconds: float = 1.0) -> QueueMessage | None:
        """Atomically move tail of pending list into in-flight list."""
        wait_int = max(1, int(timeout_seconds))
        raw = self._client.brpoplpush(self._pending_key, self._inflight_key, timeout=wait_int)
        if not raw:
            return None
        return QueueMessage.model_validate_json(str(raw))

    def ack(self, receipt_id: str) -> None:
        """Remove acknowledged receipt from Redis in-flight list."""
        items: list[str] = list(self._client.lrange(self._inflight_key, 0, -1))
        for raw in items:
            parsed = json.loads(raw)
            if parsed.get("receipt_id") == receipt_id:
                self._client.lrem(self._inflight_key, 1, raw)
                break

    def nack(self, receipt_id: str, requeue: bool = True) -> None:
        """Remove from in-flight and optionally requeue with incremented delivery count."""
        items: list[str] = list(self._client.lrange(self._inflight_key, 0, -1))
        for raw in items:
            parsed = json.loads(raw)
            if parsed.get("receipt_id") == receipt_id:
                self._client.lrem(self._inflight_key, 1, raw)
                if requeue:
                    msg = QueueMessage.model_validate(parsed)
                    updated = msg.model_copy(
                        update={
                            "receipt_id": f"msg_{uuid4().hex[:12]}",
                            "delivery_count": msg.delivery_count + 1,
                        }
                    )
                    self._client.lpush(self._pending_key, updated.model_dump_json())
                break

    def queue_depth(self) -> int:
        """Return Redis `LLEN` of pending queue."""
        return int(self._client.llen(self._pending_key))

    def oldest_message_age_seconds(self) -> float:
        """Inspect oldest item at tail of Redis pending list."""
        raw = self._client.lindex(self._pending_key, -1)
        if not raw:
            return 0.0
        msg = QueueMessage.model_validate_json(str(raw))
        delta = (datetime.now(UTC) - msg.enqueued_at).total_seconds()
        return round(max(0.0, delta), 3)

    def check_health(self) -> bool:
        """Ping Redis broker without touching Gemini API."""
        try:
            return bool(self._client.ping())
        except Exception:
            return False


class SlidingWindowRateLimiter:
    """
    Protects Gemini Developer API quotas when HPA scales workers horizontally.
    Enforces at most `max_requests_per_minute` acquisitions in any rolling 60-second window.
    """

    def __init__(
        self,
        max_requests_per_minute: int = 30,
        window_seconds: float = 60.0,
    ) -> None:
        self.max_requests = max(1, max_requests_per_minute)
        self.window_seconds = max(0.05, window_seconds)
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()

    def try_acquire(self) -> tuple[bool, float]:
        """
        Attempt non-blocking quota acquisition.
        Returns `(True, 0.0)` if permitted, or `(False, retry_after_seconds)` if throttled.
        """
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True, 0.0

            retry_after = max(0.01, (self._timestamps[0] + self.window_seconds) - now)
            return False, round(retry_after, 3)

    def acquire_blocking(self, max_wait_seconds: float = 10.0) -> bool:
        """Wait up to `max_wait_seconds` for a rate-limit slot; return False on timeout."""
        deadline = time.monotonic() + max(0.0, max_wait_seconds)
        while True:
            allowed, retry_after = self.try_acquire()
            if allowed:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(retry_after, remaining, 0.25))


def build_job_queue(redis_url: str = "") -> JobQueueProtocol:
    """Factory selecting RedisJobQueue when `REDIS_URL` is configured, else InMemoryJobQueue."""
    cleaned = redis_url.strip()
    if cleaned:
        return RedisJobQueue(redis_url=cleaned)
    return InMemoryJobQueue()
