"""
group_worker.py — Per-branch thread that generates commits at random intervals.
"""
from __future__ import annotations
import threading
import time
import random
import logging
import queue
from typing import Optional

import git
from .commit_generator import generate_commit

logger = logging.getLogger(__name__)


class GroupWorker(threading.Thread):
    def __init__(self, repo: git.Repo, branch_name: str, group_name: str,
                 event_queue: queue.Queue, rng: random.Random,
                 enable_push: bool = False, daemon: bool = True):
        super().__init__(daemon=daemon, name=f"worker-{group_name}")
        self.repo = repo
        self.branch_name = branch_name
        self.group_name = group_name
        self.event_queue = event_queue
        self.rng = rng
        self.enable_push = enable_push
        self._stop_event = threading.Event()
        self.commit_count = 0
        self._lock: Optional[threading.Lock] = None  # injected by orchestrator

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("[%s] Worker started on '%s'", self.group_name, self.branch_name)
        while not self._stop_event.is_set():
            interval = self.rng.uniform(12, 22)
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(0.3)
            try:
                lock = self._lock or _noop_lock
                with lock:
                    self.repo.git.checkout(self.branch_name)
                    hexsha, file_meta = generate_commit(
                        self.repo, self.branch_name, self.group_name, self.rng)
                self.commit_count += 1
                total_mb = sum(s for _, s in file_meta) / 1e6
                self.event_queue.put({
                    "type": "commit",
                    "group": self.group_name,
                    "branch": self.branch_name,
                    "hexsha": hexsha[:8],
                    "total_mb": round(total_mb, 3),
                    "files": len(file_meta),
                    "commit_count": self.commit_count,
                })
                if self.enable_push:
                    try:
                        self.repo.remotes.origin.push(self.branch_name)
                    except Exception as e:
                        logger.warning("[%s] Push failed: %s", self.group_name, e)
            except Exception as exc:
                logger.error("[%s] Commit failed: %s", self.group_name, exc, exc_info=True)
                self.event_queue.put({"type": "error", "group": self.group_name, "message": str(exc)})
        logger.info("[%s] Worker stopped.", self.group_name)


class _NoopLock:
    def __enter__(self): return self
    def __exit__(self, *a): pass

_noop_lock = _NoopLock()
