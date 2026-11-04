from __future__ import annotations

import time

from datamate.config import Limits
from datamate.errors import ResourceExceeded


class BudgetManager:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.started = time.monotonic()
        self.model_calls = 0
        self.candidates = 0

    def remaining_seconds(self) -> float:
        return max(0, self.limits.max_wallclock_seconds - (time.monotonic() - self.started))

    def model_call(self) -> None:
        if self.remaining_seconds() <= 0 or self.model_calls >= self.limits.max_model_calls:
            raise ResourceExceeded("Model/wallclock budget exhausted")
        self.model_calls += 1

    def candidate(self) -> None:
        if self.remaining_seconds() <= 0 or self.candidates >= self.limits.max_candidates:
            raise ResourceExceeded("Candidate/wallclock budget exhausted")
        self.candidates += 1
