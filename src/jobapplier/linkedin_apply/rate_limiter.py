"""Randomized delays and a hard per-run cap, so this looks like a person browsing rather
than a bot blasting through jobs as fast as possible."""
from __future__ import annotations

import random
import time


class RateLimiter:
    def __init__(self, min_delay_seconds: float, max_delay_seconds: float, max_applications_per_run: int):
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.max_applications_per_run = max_applications_per_run
        self.applications_this_run = 0

    def has_capacity(self) -> bool:
        return self.applications_this_run < self.max_applications_per_run

    def record_application(self) -> None:
        self.applications_this_run += 1

    def wait(self) -> None:
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
        time.sleep(delay)
