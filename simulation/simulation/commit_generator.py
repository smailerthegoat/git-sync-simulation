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
