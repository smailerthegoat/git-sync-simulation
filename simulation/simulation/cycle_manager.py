"""
cycle_manager.py — 60-second cycle timer + quota recalculation.
"""
from __future__ import annotations
import threading
import time
import logging
import queue
from typing import Dict

from .policy import StoragePolicy, Policy

logger = logging.getLogger(__name__)


class CycleManager(threading.Thread):
    def __init__(self, policies: Dict[str, Policy], event_queue: queue.Queue,
                 cycle_length: int = 60, daemon: bool = True):
        super().__init__(daemon=daemon, name="CycleManager")
        self.policies = policies
        self.event_queue = event_queue
        self.cycle_length = cycle_length
        self._stop_event = threading.Event()
        self.cycle_number = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("[CycleManager] Started — cycle_length=%ds", self.cycle_length)
        while not self._stop_event.is_set():
            deadline = time.monotonic() + self.cycle_length
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(0.3)
            self.cycle_number += 1
            self._end_of_cycle()

    def _end_of_cycle(self) -> None:
        quota_updates = {}
        for group, policy in self.policies.items():
            if isinstance(policy, StoragePolicy):
                old_quota = policy.quota_bytes
                policy.recalculate_quota(self.cycle_length)
                quota_updates[group] = {
                    "old_mb": round(old_quota / 1e6, 2),
                    "new_mb": round(policy.quota_bytes / 1e6, 2),
                }
        self.event_queue.put({
            "type": "cycle_end",
            "cycle_number": self.cycle_number,
            "quota_updates": quota_updates,
        })
        logger.info("[CycleManager] Cycle %d complete. Updates: %s",
                    self.cycle_number, quota_updates)
