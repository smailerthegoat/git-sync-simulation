"""
clean.py — Reset simulation branches to pre-data state.

Run this between simulation runs to start fresh:
    python clean.py

What it does:
  1. Tears down any leftover git worktrees from a previous run.
  2. For each simulation branch, finds the last commit that predates any
     data/ file and force-resets the branch ref to that commit.
  3. Deletes the data/ directories on the current working tree if present.
"""
from __future__ import annotations
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import git

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

REPO_ROOT  = Path(__file__).parent
WORKTREE_DIR = REPO_ROOT / ".worktrees"
BRANCHES   = ["group-storage", "group-privacy", "group-unconstrained"]


def find_clean_base(repo: git.Repo, branch: str) -> str:
    """Return the SHA of the last commit on branch that predates any data/ file.

    Strategy: walk commits oldest-first that touch data/; the parent of the
    very first such commit is the clean base.  If no data commits exist the
    branch is already clean — return its current HEAD.
    """
    try:
        log_out = repo.git.log(
            "--format=%H", "--reverse", branch, "--", "data/")
        shas = [s.strip() for s in log_out.strip().splitlines() if s.strip()]
    except git.GitCommandError:
        shas = []

    if not shas:
        logger.info("  %s: no data commits found — already clean", branch)
        return repo.commit(branch).hexsha

    first_data_sha = shas[0]
    commit_obj = repo.commit(first_data_sha)
    if not commit_obj.parents:
        # Extremely unlikely: data was added in the very first commit
        return first_data_sha
    clean_sha = commit_obj.parents[0].hexsha
    logger.info("  %s: clean base = %s  (before %s)",
                branch, clean_sha[:8], first_data_sha[:8])
    return clean_sha


def teardown_worktrees(repo: git.Repo) -> None:
    for branch in BRANCHES:
        wt_path = WORKTREE_DIR / branch
        try:
            repo.git.worktree("remove", str(wt_path), "--force")
        except Exception:
            pass
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
    if WORKTREE_DIR.exists():
        try:
            shutil.rmtree(WORKTREE_DIR, ignore_errors=True)
        except Exception:
            pass
    try:
        repo.git.worktree("prune")
    except Exception:
        pass


def main() -> None:
    repo = git.Repo(REPO_ROOT)

    # ── 0. Remove stale git lock files ──────────────────────────────────────
    for lock_file in Path(repo.git_dir).glob("*.lock"):
        try:
            lock_file.unlink()
            logger.info("Removed stale lock: %s", lock_file.name)
        except Exception:
            pass

    # ── 1. Teardown worktrees ────────────────────────────────────────────────
    logger.info("Tearing down worktrees …")
    teardown_worktrees(repo)

    # ── 2. Switch to main so all simulation branch refs are free to be reset ──
    try:
        repo.git.checkout("main")
        logger.info("Checked out main")
    except Exception as e:
        # Fall back to detached HEAD
        head_sha = repo.head.commit.hexsha
        (Path(repo.git_dir) / "HEAD").write_text(head_sha + "\n")
        logger.warning("Could not checkout main, detached HEAD: %s", e)

    # ── 3. Force-reset each branch to its pre-data base commit ───────────────
    logger.info("Finding clean bases …")
    for branch in BRANCHES:
        clean_sha = find_clean_base(repo, branch)
        repo.git.branch("-f", branch, clean_sha)
        logger.info("  Reset %s → %s", branch, clean_sha[:8])

    # ── 4. Delete data/ from working tree (main repo, current checkout) ──────
    data_dir = REPO_ROOT / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
        logger.info("Deleted %s", data_dir)

    logger.info("Clean complete — ready for a fresh simulation run.")


if __name__ == "__main__":
    main()
