#!/usr/bin/env bash
# =============================================================================
# Run this from INSIDE the git-sync-simulation/ directory:
#   cd git-sync-simulation
#   chmod +x bootstrap_inside_repo.sh
#   bash bootstrap_inside_repo.sh
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[·]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# Guard: must be inside the repo
[[ -d ".git" ]] || die "Run this from inside git-sync-simulation/ (no .git found here)"

PYTHON="python3.12"
command -v "$PYTHON" &>/dev/null || die "python3.12 not found. Run: sudo apt install python3.12 python3.12-venv -y"

# ── 1. CONFIGURE GIT ──────────────────────────────────────────────────────────
info "Configuring git..."
git config core.autocrlf false
# Make sure we're on main
git checkout -B main

# ── 2. WRITE ALL PROJECT FILES ────────────────────────────────────────────────
info "Writing project files..."

# requirements.txt
cat > requirements.txt << 'EOF'
streamlit>=1.35.0
gitpython>=3.1.43
pyyaml>=6.0.1
pydantic>=2.7.0
EOF

# .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.env
.DS_Store
*.log
EOF

# README.md
cat > README.md << 'EOF'
# Git Sync Simulation

A research demo simulating three independent groups evolving in parallel
Git branches, synchronizing via policy-filtered patch exchange.

## Branches

| Branch | Policy |
|--------|--------|
| `group-storage` | Storage + bandwidth constrained (12 MB/cycle, 5 MB/sync) |
| `group-privacy` | Privacy constrained (stub — ready for extension) |
| `group-unconstrained` | No constraints — accepts everything |

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python simulation/main.py --duration 60
streamlit run simulation/app.py
```
EOF

# ── 3. DIRECTORY STRUCTURE ────────────────────────────────────────────────────
info "Creating directories..."
mkdir -p simulation/config simulation/simulation

touch simulation/__init__.py
touch simulation/simulation/__init__.py

# ── 4. YAML CONFIGS ───────────────────────────────────────────────────────────
info "Writing YAML configs..."

cat > simulation/config/group_storage.yaml << 'EOF'
policy: storage
group_name: group-storage
initial_quota: 12582912
bandwidth_limit: 5242880
carry_over_enabled: true
log_filtering_decisions: true
EOF

cat > simulation/config/group_privacy.yaml << 'EOF'
policy: privacy
group_name: group-privacy
redact_emails: false
exclude_patterns: []
EOF

cat > simulation/config/group_unconstrained.yaml << 'EOF'
policy: unconstrained
group_name: group-unconstrained
EOF

# ── 5. policy.py ──────────────────────────────────────────────────────────────
info "Writing simulation/simulation/policy.py..."
cat > simulation/simulation/policy.py << 'EOF'
"""
policy.py — Policy base class and concrete implementations.
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List
import yaml

logger = logging.getLogger(__name__)


@dataclass
class PatchInfo:
    filename: str
    size_bytes: int
    commit_hash: str = ""
    commit_message: str = ""


@dataclass
class FilterResult:
    accepted: List[PatchInfo] = field(default_factory=list)
    rejected: List[PatchInfo] = field(default_factory=list)
    accepted_bytes: int = 0
    rejected_bytes: int = 0
    reason: str = ""


class Policy(ABC):
    def __init__(self, config: dict):
        self.config = config
        self.group_name: str = config.get("group_name", "unknown")

    @abstractmethod
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        pass

    @classmethod
    def from_yaml(cls, path: str) -> "Policy":
        with open(path) as f:
            cfg = yaml.safe_load(f)
        kind = cfg.get("policy", "unconstrained")
        if kind == "storage":
            return StoragePolicy(cfg)
        elif kind == "privacy":
            return PrivacyPolicy(cfg)
        else:
            return UnconstrainedPolicy(cfg)


class StoragePolicy(Policy):
    def __init__(self, config: dict):
        super().__init__(config)
        self.quota_bytes: int = config.get("initial_quota", 12 * 1024 * 1024)
        self.bandwidth_limit: int = config.get("bandwidth_limit", 5 * 1024 * 1024)
        self.carry_over_enabled: bool = config.get("carry_over_enabled", True)
        self.log_decisions: bool = config.get("log_filtering_decisions", True)
        self.accepted_this_cycle: int = 0

    @property
    def quota_remaining(self) -> int:
        return max(0, self.quota_bytes - self.accepted_this_cycle)

    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        result = FilterResult()
        sync_budget = min(self.bandwidth_limit, self.quota_remaining)
        accumulated = 0

        for patch in patches:
            if accumulated + patch.size_bytes <= sync_budget:
                result.accepted.append(patch)
                accumulated += patch.size_bytes
            else:
                result.rejected.append(patch)
                result.rejected_bytes += patch.size_bytes
                if self.log_decisions:
                    logger.info(
                        "[StoragePolicy cycle=%d] DROP %s (%.2f MB) — budget=%.2f MB",
                        cycle_number, patch.filename, patch.size_bytes / 1e6, sync_budget / 1e6,
                    )

        result.accepted_bytes = accumulated
        self.accepted_this_cycle += accumulated
        result.reason = (
            f"accepted {accumulated/1e6:.2f} MB / rejected {result.rejected_bytes/1e6:.2f} MB | "
            f"quota used {self.accepted_this_cycle/1e6:.2f}/{self.quota_bytes/1e6:.2f} MB"
        )
        return result

    def recalculate_quota(self, next_cycle_length_seconds: int) -> None:
        remaining = self.quota_remaining
        if self.carry_over_enabled and next_cycle_length_seconds > 0:
            new_quota = remaining // next_cycle_length_seconds
            logger.info(
                "[StoragePolicy] Carry-over: %.2f MB remaining → new quota %.2f MB",
                remaining / 1e6, new_quota / 1e6,
            )
            self.quota_bytes = new_quota
        else:
            self.quota_bytes = self.config.get("initial_quota", 12 * 1024 * 1024)
        self.accepted_this_cycle = 0


class PrivacyPolicy(Policy):
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        return FilterResult(
            accepted=list(patches),
            accepted_bytes=sum(p.size_bytes for p in patches),
            reason="privacy filter: no-op stub — all patches accepted",
        )

    def apply_privacy_filter(self, patch_content: str) -> str:
        return patch_content  # no-op stub


class UnconstrainedPolicy(Policy):
    def filter_patches(self, patches: List[PatchInfo], cycle_number: int = 0) -> FilterResult:
        total = sum(p.size_bytes for p in patches)
        return FilterResult(
            accepted=list(patches),
            accepted_bytes=total,
            reason=f"unconstrained — accepted all {total/1e6:.2f} MB",
        )
EOF

# ── 6. commit_generator.py ────────────────────────────────────────────────────
info "Writing simulation/simulation/commit_generator.py..."
cat > simulation/simulation/commit_generator.py << 'EOF'
"""
commit_generator.py — Dummy file creation and git commit logic.
"""
from __future__ import annotations
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import git

logger = logging.getLogger(__name__)

MIN_TOTAL_BYTES = int(0.8 * 1024 * 1024)
MAX_TOTAL_BYTES = int(3.2 * 1024 * 1024)


def _random_file_sizes(rng: random.Random) -> List[int]:
    total = rng.randint(MIN_TOTAL_BYTES, MAX_TOTAL_BYTES)
    n = rng.randint(1, 4)
    if n == 1:
        return [total]
    cuts = sorted(rng.sample(range(1, total), k=n - 1))
    boundaries = [0] + cuts + [total]
    return [boundaries[i + 1] - boundaries[i] for i in range(n)]


def generate_commit(
    repo: git.Repo,
    branch_name: str,
    group_name: str,
    rng: random.Random,
    data_dir: str = "data",
) -> Tuple[str, List[Tuple[str, int]]]:
    sizes = _random_file_sizes(rng)
    timestamp = datetime.now(timezone.utc).isoformat()
    safe_ts = timestamp.replace(":", "-").replace("+", "Z")

    subdir = Path(repo.working_dir) / data_dir / group_name / safe_ts
    subdir.mkdir(parents=True, exist_ok=True)

    file_meta: List[Tuple[str, int]] = []
    for i, size in enumerate(sizes):
        ext = rng.choice(["bin", "dat", "txt"])
        fname = subdir / f"file_{i:02d}.{ext}"
        if ext == "txt":
            chunk = f"[{group_name}] dummy payload block {i}\n" * (size // 40 + 1)
            fname.write_bytes(chunk.encode()[:size])
        else:
            fname.write_bytes(rng.randbytes(size))
        rel_path = str(fname.relative_to(repo.working_dir))
        file_meta.append((rel_path, size))
        repo.index.add([rel_path])

    file_lines = "\n".join(f"  {n}  ({s/1024:.1f} KB)" for n, s in file_meta)
    total_mb = sum(s for _, s in file_meta) / 1e6
    msg = (
        f"[{group_name}] auto-commit {safe_ts}\n\n"
        f"timestamp: {timestamp}\n"
        f"total_size_mb: {total_mb:.3f}\n"
        f"files:\n{file_lines}\n"
    )

    commit = repo.index.commit(msg)
    logger.info("[CommitGenerator] %s → %.2f MB (%d files) %s",
                group_name, total_mb, len(file_meta), commit.hexsha[:8])
    return commit.hexsha, file_meta
EOF

# ── 7. group_worker.py ────────────────────────────────────────────────────────
info "Writing simulation/simulation/group_worker.py..."
cat > simulation/simulation/group_worker.py << 'EOF'
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
EOF

# ── 8. sync_engine.py ─────────────────────────────────────────────────────────
info "Writing simulation/simulation/sync_engine.py..."
cat > simulation/simulation/sync_engine.py << 'EOF'
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
EOF

# ── 9. cycle_manager.py ───────────────────────────────────────────────────────
info "Writing simulation/simulation/cycle_manager.py..."
cat > simulation/simulation/cycle_manager.py << 'EOF'
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
EOF

# ── 10. sync_scheduler.py ─────────────────────────────────────────────────────
info "Writing simulation/simulation/sync_scheduler.py..."
cat > simulation/simulation/sync_scheduler.py << 'EOF'
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
EOF

# ── 11. app.py (skeleton) ─────────────────────────────────────────────────────
info "Writing simulation/app.py (skeleton)..."
cat > simulation/app.py << 'EOF'
"""
app.py — Streamlit dashboard skeleton.
Full UI implemented in Step 7.
Run: streamlit run simulation/app.py
"""
import streamlit as st

st.set_page_config(page_title="Git Sync Simulation", layout="wide")
st.title("🔀 Git Sync Simulation")
st.success("✅ All core modules scaffolded and ready. Full dashboard coming in Step 7.")

st.markdown("""
| Module | File | Status |
|--------|------|--------|
| Policy engine | `simulation/policy.py` | ✅ |
| Commit generator | `simulation/commit_generator.py` | ✅ |
| Group worker | `simulation/group_worker.py` | ✅ |
| Sync engine | `simulation/sync_engine.py` | ✅ |
| Cycle manager | `simulation/cycle_manager.py` | ✅ |
| Sync scheduler | `simulation/sync_scheduler.py` | ✅ |
| Dashboard | `app.py` | 🔧 Step 7 |
""")
EOF

# ── 12. main.py ───────────────────────────────────────────────────────────────
info "Writing simulation/main.py..."
cat > simulation/main.py << 'EOF'
"""
main.py — Headless CLI entry point for testing without Streamlit.

Usage:
    python simulation/main.py --duration 60 --seed 42
"""
import argparse
import logging
import queue
import random
import threading
import time
from pathlib import Path

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
EOF

# ── 13. COMMIT TO main ────────────────────────────────────────────────────────
info "Staging and committing to main..."
git add -A
git commit -m "chore: full project scaffold — all core modules (Steps 1-6)"
git push origin main
success "main pushed to GitHub."

# ── 14. CREATE AND PUSH GROUP BRANCHES ───────────────────────────────────────
info "Creating group branches..."
for branch in group-storage group-privacy group-unconstrained; do
    # Delete local branch if it already exists (clean slate)
    git branch -D "$branch" 2>/dev/null || true
    git checkout -b "$branch"
    printf "# %s\n\nSimulation branch — diverges from main via auto-commits.\n" "$branch" > BRANCH.md
    git add BRANCH.md
    git commit -m "chore: initialise $branch"
    git push -u origin "$branch"
    success "  '$branch' pushed."
    git checkout main
done

# ── 15. PYTHON VENV ───────────────────────────────────────────────────────────
info "Creating .venv with Python 3.12..."
"$PYTHON" -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
success "Virtual environment ready."

# ── 16. SMOKE TEST ────────────────────────────────────────────────────────────
info "Running smoke test..."
python - << 'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("simulation")))

from simulation.simulation.policy import (
    Policy, StoragePolicy, PrivacyPolicy, UnconstrainedPolicy, PatchInfo
)
from simulation.simulation.commit_generator import generate_commit
from simulation.simulation.group_worker import GroupWorker
from simulation.simulation.sync_engine import SyncEngine
from simulation.simulation.cycle_manager import CycleManager
from simulation.simulation.sync_scheduler import SyncScheduler

print("  [1/3] All imports OK")

sp = Policy.from_yaml("simulation/config/group_storage.yaml")
assert isinstance(sp, StoragePolicy)

# 4 MB patch — over 5 MB bw limit? No (4 < 5). Should be accepted.
patches = [PatchInfo("a.patch", 4*1024*1024)]
r = sp.filter_patches(patches, cycle_number=1)
assert len(r.accepted) == 1, f"Expected accepted, got {r}"
print("  [2/3] StoragePolicy 4 MB patch accepted (within 5 MB bw limit): PASS")

# 6 MB patch — over bw limit. Should be rejected.
sp2 = Policy.from_yaml("simulation/config/group_storage.yaml")
patches2 = [PatchInfo("b.patch", 6*1024*1024)]
r2 = sp2.filter_patches(patches2, cycle_number=1)
assert len(r2.rejected) == 1, f"Expected rejected, got {r2}"
print("  [3/3] StoragePolicy 6 MB patch rejected (exceeds 5 MB bw limit): PASS")

print("\n  ✅  Smoke test PASSED")
PYEOF

# ── DONE ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Bootstrap complete!                             ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  4 branches pushed to GitHub:                    ║${NC}"
echo -e "${GREEN}║    main · group-storage                          ║${NC}"
echo -e "${GREEN}║    group-privacy · group-unconstrained           ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  Test headless mode:                             ║${NC}"
echo -e "${GREEN}║    source .venv/bin/activate                     ║${NC}"
echo -e "${GREEN}║    python simulation/main.py --duration 60       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
