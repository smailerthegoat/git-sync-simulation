"""
sync_engine.py — git log → policy filter → file copy → commit.

Each branch has a dedicated git worktree so no checkout juggling is needed.
The main repo is used read-only (git log, merge_base).  All writes go
through the per-branch worktree repos stored in self.worktrees.

Sync strategy: instead of format-patch / git am (which is fragile on
WSL2/NTFS due to stat-cache dirty-index false positives), we:
  1. Walk new commits on src via git log.
  2. For each commit extract the data/ files it introduced.
  3. Copy those files directly from the src worktree into the dst worktree.
  4. Stage + commit in the dst worktree.
This avoids all git am / index-lock / dirty-index issues entirely.
"""
from __future__ import annotations
import logging
import re
import shutil
import queue
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import git
from .policy import Policy, PatchInfo, FilterResult

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"total_size_mb:\s*([\d.]+)")


class SyncEngine:
    def __init__(self, repo: git.Repo, worktree_repos: Dict[str, git.Repo],
                 policies: Dict[str, Policy],
                 event_queue: queue.Queue, repo_lock: object = None):
        self.repo = repo                  # main repo — read-only
        self.worktrees = worktree_repos   # {branch_name: Repo at worktree path}
        self.policies = policies
        self.event_queue = event_queue
        self._lock = repo_lock
        self._last_synced: Dict[Tuple[str, str], str] = {}

    def sync(self, src_branch: str, dst_branch: str, cycle_number: int) -> Optional[FilterResult]:
        logger.info("[SyncEngine] cycle=%d  %s → %s", cycle_number, src_branch, dst_branch)

        lock = self._lock if self._lock is not None else _noop_lock
        with lock:
            since_ref = self._last_synced.get((src_branch, dst_branch))
            patches = self._get_new_commit_infos(src_branch, dst_branch, since_ref)

            if not patches:
                logger.info("[SyncEngine] No new commits %s → %s", src_branch, dst_branch)
                return None

            policy = self.policies[dst_branch]
            result = policy.filter_patches(patches, cycle_number=cycle_number)

            if result.accepted:
                self._copy_accepted_commits(src_branch, dst_branch, result.accepted)
                self._last_synced[(src_branch, dst_branch)] = \
                    self.repo.commit(src_branch).hexsha

            self._create_merge_commit(dst_branch, src_branch, result, cycle_number,
                                      policy.__class__.__name__)

            self.event_queue.put({
                "type": "sync",
                "src": src_branch, "dst": dst_branch,
                "cycle": cycle_number,
                "accepted_mb": round(result.accepted_bytes / 1e6, 3),
                "rejected_mb": round(result.rejected_bytes / 1e6, 3),
                "accepted_patches": len(result.accepted),
                "rejected_patches": len(result.rejected),
                "reason": result.reason,
            })
            return result

    # ── internal helpers ──────────────────────────────────────────────────────

    def _get_new_commit_infos(self, src: str, dst: str, since_ref: Optional[str]) -> List[PatchInfo]:
        """Return PatchInfo for each new commit on src (data/ only), oldest first."""
        if since_ref:
            range_spec = f"{since_ref}..{src}"
        else:
            try:
                bases = self.repo.merge_base(dst, src)
                base_sha = bases[0].hexsha if bases else None
            except Exception:
                base_sha = None
            if base_sha is None:
                logger.info("[SyncEngine] No common base found %s → %s, skipping", src, dst)
                return []
            range_spec = f"{base_sha}..{src}"

        try:
            log_out = self.repo.git.log("--format=%H", range_spec, "--", "data/")
        except git.GitCommandError as e:
            logger.error("[SyncEngine] git log failed: %s", e)
            return []

        shas = [s.strip() for s in log_out.strip().splitlines() if s.strip()]
        if not shas:
            return []

        infos: List[PatchInfo] = []
        for sha in reversed(shas):   # oldest first
            try:
                commit_obj = self.repo.commit(sha)
                m = _SIZE_RE.search(commit_obj.message)
                size_bytes = int(float(m.group(1)) * 1e6) if m else 524288
                infos.append(PatchInfo(
                    filename=sha,
                    size_bytes=size_bytes,
                    commit_hash=sha,
                    commit_message=commit_obj.message,
                ))
            except Exception as e:
                logger.warning("[SyncEngine] Could not read commit %s: %s", sha, e)
        return infos

    def _copy_accepted_commits(self, src_branch: str, dst_branch: str,
                                accepted: List[PatchInfo]) -> None:
        """Copy data/ files from each accepted commit into the dst worktree."""
        src_root = Path(self.worktrees[src_branch].working_dir)
        dst_repo = self.worktrees[dst_branch]
        dst_root = Path(dst_repo.working_dir)

        for patch in accepted:
            sha = patch.commit_hash
            try:
                # Files introduced/modified in this commit
                diff_out = self.repo.git.diff_tree(
                    "--no-commit-id", "-r", "--name-only", sha)
                data_files = [f for f in diff_out.splitlines()
                              if f.startswith("data/")]
                if not data_files:
                    continue

                copied: List[str] = []
                for rel_path in data_files:
                    src_file = src_root / rel_path
                    dst_file = dst_root / rel_path
                    if not src_file.exists():
                        logger.warning("[SyncEngine] src file missing: %s", src_file)
                        continue
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_file), str(dst_file))
                    copied.append(rel_path)

                if not copied:
                    continue

                dst_repo.git.add("data/")
                # Re-use original commit message so traceability is preserved
                dst_repo.git.commit("-m", patch.commit_message)
                logger.info("[SyncEngine] Copied %d file(s) from %s into %s",
                            len(copied), sha[:8], dst_branch)

            except git.GitCommandError as e:
                logger.warning("[SyncEngine] commit failed for %s: %s", sha[:8], e)
            except Exception as e:
                logger.warning("[SyncEngine] copy failed for %s: %s", sha[:8], e)

    def _create_merge_commit(self, dst_branch, src_branch, result, cycle_number, policy_name):
        """Record a policy-merge commit in the destination worktree."""
        dst_repo = self.worktrees[dst_branch]
        policy = self.policies.get(dst_branch)
        quota_info = ""
        if hasattr(policy, "quota_bytes"):
            quota_info = (
                f"\nquota_used_mb: {getattr(policy,'accepted_this_cycle',0)/1e6:.3f}"
                f"\nquota_remaining_mb: {getattr(policy,'quota_remaining',0)/1e6:.3f}"
            )
        msg = (
            f"[policy-merge] cycle={cycle_number} {src_branch} → {dst_branch}\n\n"
            f"policy: {policy_name}\n"
            f"accepted_mb: {result.accepted_bytes/1e6:.3f}\n"
            f"rejected_mb: {result.rejected_bytes/1e6:.3f}\n"
            f"accepted_patches: {len(result.accepted)}\n"
            f"rejected_patches: {len(result.rejected)}\n"
            f"{quota_info}\n"
            f"summary: {result.reason}\n"
        )
        dst_repo.git.execute(["git", "commit", "--allow-empty", "-m", msg])


class _NoopLock:
    def __enter__(self): return self
    def __exit__(self, *a): pass

_noop_lock = _NoopLock()
