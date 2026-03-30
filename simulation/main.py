"""
main.py — Headless CLI entry point for testing without Streamlit.

Usage:
    python simulation/main.py --duration 60 --seed 42
"""
import argparse
import logging
import queue
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import git

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BRANCHES = ["group-storage", "group-privacy", "group-unconstrained"]
CONFIG_DIR = Path(__file__).parent / "config"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    repo = git.Repo(Path(__file__).parent.parent)
    eq: queue.Queue = queue.Queue()
    repo_lock = threading.Lock()

    from simulation.simulation.policy import Policy
    from simulation.simulation.group_worker import GroupWorker
    from simulation.simulation.sync_engine import SyncEngine
    from simulation.simulation.cycle_manager import CycleManager
    from simulation.simulation.sync_scheduler import SyncScheduler

    policies = {
        "group-storage":       Policy.from_yaml(str(CONFIG_DIR / "group_storage.yaml")),
        "group-privacy":       Policy.from_yaml(str(CONFIG_DIR / "group_privacy.yaml")),
        "group-unconstrained": Policy.from_yaml(str(CONFIG_DIR / "group_unconstrained.yaml")),
    }

    workers = []
    for branch in BRANCHES:
        w = GroupWorker(repo, branch, branch, eq, rng, enable_push=args.push)
        w._lock = repo_lock
        w.start()
        workers.append(w)

    cm = CycleManager(policies, eq, cycle_length=60)
    cm.start()

    engine = SyncEngine(repo, policies, eq, repo_lock=repo_lock)
    scheduler = SyncScheduler(engine, BRANCHES, rng, eq, cm)
    scheduler.start()

    logger.info("▶  Simulation running for %ds (seed=%d)", args.duration, args.seed)
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.duration:
            try:
                ev = eq.get(timeout=1.0)
                t = ev.get("type", "?")
                if t == "commit":
                    logger.info("COMMIT  %-22s  %.2f MB  (%d files)  %s",
                                ev["group"], ev["total_mb"], ev["files"], ev["hexsha"])
                elif t == "sync":
                    logger.info("SYNC    %s → %s  accepted=%.2f MB  rejected=%.2f MB",
                                ev["src"], ev["dst"], ev["accepted_mb"], ev["rejected_mb"])
                elif t == "cycle_end":
                    logger.info("CYCLE   #%d complete  quota_updates=%s",
                                ev["cycle_number"], ev["quota_updates"])
                elif t == "error":
                    logger.error("ERROR   %s", ev.get("message"))
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        for w in workers:
            w.stop()
        scheduler.stop()
        cm.stop()
        for t in workers + [scheduler, cm]:
            t.join(timeout=5)
        logger.info("⏹  Simulation stopped.")


if __name__ == "__main__":
    main()
