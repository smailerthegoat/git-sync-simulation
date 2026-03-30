"""
main.py — Headless CLI entry point for testing without Streamlit.

Usage:
    python simulation/main.py --duration 60 --seed 42
"""
import argparse
import logging
import queue
import random
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root

import git

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BRANCHES = ["group-storage", "group-privacy", "group-unconstrained"]
CONFIG_DIR = Path(__file__).parent / "config"
REPO_ROOT = Path(__file__).parent.parent
WORKTREE_DIR = REPO_ROOT / ".worktrees"


def setup_worktrees(repo: git.Repo) -> dict:
    """Create one worktree per branch; return {branch_name: git.Repo}.

    Detaches HEAD on the main repo first so all three branch names are free
    to be checked out by their dedicated worktrees.
    """
    # Tear down any leftover worktrees from a previous crashed run
    teardown_worktrees(repo, quiet=True)
    WORKTREE_DIR.mkdir(exist_ok=True)

    # Detach HEAD by writing the commit SHA directly to .git/HEAD — no
    # subprocess, no lock contention.  This frees all branch refs so each
    # worktree can check out its branch exclusively (no --force needed).
    head_sha = repo.head.commit.hexsha
    (Path(repo.git_dir) / "HEAD").write_text(head_sha + "\n")

    worktree_repos = {}
    for branch in BRANCHES:
        wt_path = WORKTREE_DIR / branch
        repo.git.worktree("add", str(wt_path), branch)
        wt_repo = git.Repo(str(wt_path))
        # Ensure the worktree index is clean before use (avoids inheriting
        # staged files from a previous run or failed git am session).
        try:
            wt_repo.git.reset("--hard", "HEAD")
        except Exception:
            pass
        worktree_repos[branch] = wt_repo
        logger.info("Worktree ready: %s → %s", branch, wt_path)
    return worktree_repos


def teardown_worktrees(repo: git.Repo, quiet: bool = False) -> None:
    """Remove all managed worktrees."""
    for branch in BRANCHES:
        wt_path = WORKTREE_DIR / branch
        # Unregister the worktree from git's tracking (best-effort)
        try:
            repo.git.worktree("remove", str(wt_path), "--force")
        except Exception:
            pass
        # Remove directory manually — worktree may have data files that
        # prevent git worktree remove from deleting the directory itself.
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
    if WORKTREE_DIR.exists():
        shutil.rmtree(WORKTREE_DIR, ignore_errors=True)
    try:
        repo.git.worktree("prune")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    repo = git.Repo(REPO_ROOT)
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

    worktree_repos = setup_worktrees(repo)

    workers = []
    for branch in BRANCHES:
        w = GroupWorker(worktree_repos[branch], branch, branch, eq, rng,
                        enable_push=args.push)
        w._lock = repo_lock
        w.start()
        workers.append(w)

    cm = CycleManager(policies, eq, cycle_length=60)
    cm.start()

    engine = SyncEngine(repo, worktree_repos, policies, eq, repo_lock=repo_lock)
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
        teardown_worktrees(repo)
        logger.info("⏹  Simulation stopped.")


if __name__ == "__main__":
    main()
