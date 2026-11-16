"""Steady pacing between calls and exponential backoff with jitter on rate limits."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """A provider error worth retrying: rate limited (429) or temporarily unavailable (5xx).
    `retry_after` is the provider's own suggested wait in seconds, when it gives one."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def call_with_backoff(
    call: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    base_delay: float = 5.0,
    max_attempts: int = 7,
    on_retry: Callable[[RetryableError, float], None] = lambda error, delay: None,
) -> T:
    """Retries `call` on RetryableError, waiting base_delay * 2**n plus up to one base_delay of
    random jitter, so several clients never retry in lockstep, and never less than the
    provider's `retry_after`."""
    for attempt in range(max_attempts):
        try:
            return call()
        except RetryableError as error:
            if attempt == max_attempts - 1:
                raise
            delay = max(base_delay * 2 ** attempt + jitter() * base_delay, error.retry_after or 0.0)
            on_retry(error, delay)
            sleep(delay)
    raise AssertionError("unreachable")


class Pacer:
    """Keeps at least `interval` seconds between the starts of consecutive calls."""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()
