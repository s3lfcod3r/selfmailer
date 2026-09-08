"""Bounded, non-blocking admission for expensive whole-account live counts."""
from threading import Lock
from time import monotonic


class CountsGate:
    def __init__(self, cooldown: float = 30, max_entries: int = 1024, now=monotonic):
        self.cooldown = cooldown
        self.max_entries = max_entries
        self.now = now
        self.lock = Lock()
        self.entries: dict[int, float | None] = {}  # None: currently running

    def claim(self, account_id: int) -> bool:
        with self.lock:
            now = self.now()
            self.entries = {k: v for k, v in self.entries.items() if v is None or v > now}
            if account_id in self.entries or len(self.entries) >= self.max_entries:
                return False
            self.entries[account_id] = None
            return True

    def finish(self, account_id: int) -> None:
        with self.lock:
            self.entries[account_id] = self.now() + self.cooldown


counts_gate = CountsGate()
