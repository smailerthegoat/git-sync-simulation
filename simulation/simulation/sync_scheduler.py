"""
sync_scheduler.py — Triggers SyncEngine at random 25–45 second intervals.
"""
from __future__ import annotations
import threading
import time
import random
import logging
import queue
from typing import List

from .sync_engine import SyncEngine

logger = logging.getLogger(__name__)


class SyncScheduler(threading.Thread):
    def __init__(self, sync_engine: SyncEngine, branches: List[str],
                 rng: random.Random, event_queue: queue.Queue,
                 cycle_manager, daemon: bool = True):
        super().__init__(daemon=daemon, name="SyncScheduler")
        self.engine = sync_engine
        self.branches = branches
        self.rng = rng
        self.event_queue = event_queue
        self.cycle_manager = cycle_manager
        self._stop_event = threading.Event()
        self._manual: queue.Queue = queue.Queue()

    def stop(self) -> None:
        self._stop_event.set()

    def trigger_manual(self, src: str, dst: str) -> None:
        self._manual.put((src, dst))

    def run(self) -> None:
        logger.info("[SyncScheduler] Started.")
        while not self._stop_event.is_set():
            interval = self.rng.uniform(25, 45)
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                try:
                    src, dst = self._manual.get_nowait()
                    self._do_sync(src, dst)
                except queue.Empty:
                    pass
                time.sleep(0.3)
            src, dst = self.rng.sample(self.branches, k=2)
            self._do_sync(src, dst)

    def _do_sync(self, src: str, dst: str) -> None:
        try:
            self.engine.sync(src, dst, self.cycle_manager.cycle_number)
        except Exception as exc:
            logger.error("[SyncScheduler] %s→%s failed: %s", src, dst, exc, exc_info=True)
            self.event_queue.put({"type": "error", "message": f"sync {src}→{dst}: {exc}"})
