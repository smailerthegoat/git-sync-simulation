"""
sync_engine.py — format-patch → policy filter → git am → policy-merge commit.
"""
from __future__ import annotations
import logging
import re
import tempfile
import queue
from pathlib import Path
from typing import Dict, Optional, Tuple

import git
from .policy import Policy, PatchInfo, FilterResult

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(self, repo: git.Repo, policies: Dict[str, Policy],
                 event_queue: queue.Queue, repo_lock: object = None):
        self.repo = repo
        self.policies = policies
        self.event_queue = event_queue
        self._lock = repo_lock
        self._last_synced: Dict[Tuple[str, str], str] = {}

    def sync(self, src_branch: str, dst_branch: str, cycle_number: int) -> Optional[FilterResult]:
        logger.info("[SyncEngine] cycle=%d  %s → %s", cycle_number, src_branch, dst_branch)

        with tempfile.TemporaryDirectory(prefix="gitsync_") as patch_dir:
            since_ref = self._last_synced.get((src_branch, dst_branch))
            patch_files = self._format_patches(src_branch, dst_branch, patch_dir, since_ref)

            if not patch_files:
                logger.info("[SyncEngine] No new commits %s → %s", src_branch, dst_branch)
                return None

            patches = self._build_patch_infos(patch_files)
            policy = self.policies[dst_branch]
            result = policy.filter_patches(patches, cycle_number=cycle_number)

            if result.accepted:
                self._apply_patches(dst_branch, result.accepted)
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

    def _format_patches(self, src, dst, patch_dir, since_ref):
        try:
            self.repo.git.checkout(dst)
            if since_ref:
                range_spec = f"{since_ref}..{src}"
            else:
                range_spec = f"-5 {src}"
            self.repo.git.execute(
                ["git", "format-patch", "--output-directory", patch_dir, range_spec])
            return sorted(str(p) for p in Path(patch_dir).glob("*.patch"))
        except git.GitCommandError as e:
            logger.error("[SyncEngine] format-patch failed: %s", e)
            return []

    def _build_patch_infos(self, patch_files):
        size_re = re.compile(r"total_size_mb:\s*([\d.]+)")
        infos = []
        for pf in patch_files:
            content = Path(pf).read_text(errors="replace")
            m = size_re.search(content)
            size_bytes = int(float(m.group(1)) * 1e6) if m else 524288
            infos.append(PatchInfo(filename=pf, size_bytes=size_bytes))
        return infos

    def _apply_patches(self, dst_branch, patches):
        self.repo.git.checkout(dst_branch)
        for patch in patches:
            try:
                self.repo.git.execute(["git", "am", "--ignore-whitespace", patch.filename])
            except git.GitCommandError as e:
                logger.warning("[SyncEngine] git am failed %s: %s", patch.filename, e)
                try:
                    self.repo.git.execute(["git", "am", "--abort"])
                except Exception:
                    pass
                break

    def _create_merge_commit(self, dst_branch, src_branch, result, cycle_number, policy_name):
        self.repo.git.checkout(dst_branch)
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
        self.repo.git.execute(["git", "commit", "--allow-empty", "-m", msg])
