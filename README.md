# Git Sync Simulation

A research demo simulating three independent groups evolving in parallel
Git branches, synchronizing via policy-filtered patch exchange.

## Overview

This simulation models a multi-branch Git workflow where independent teams
(groups) generate commits on their own branches and periodically synchronize
data between branches. Each branch enforces a different policy that controls
what data is accepted during sync operations.

The simulation uses **git worktrees** to allow all branches to operate
concurrently without checkout contention — a design choice driven by
WSL2/NTFS compatibility requirements.

## Architecture

```
main.py (CLI entry point)
  ├── 3x GroupWorker threads    → generate random commits (12-22s intervals)
  ├── 1x CycleManager thread   → 60-second quota cycles
  ├── 1x SyncScheduler thread  → random sync triggers (25-45s intervals)
  └── 1x SyncEngine            → policy-filtered file-copy sync
```

## Branches & Policies

| Branch | Policy | Description |
|--------|--------|-------------|
| `group-storage` | Storage | Bandwidth + quota constrained (12 MB/cycle, 5 MB/sync) |
| `group-privacy` | Privacy | Privacy constrained (stub — ready for extension) |
| `group-unconstrained` | Unconstrained | No constraints — accepts everything |

## Project Structure

```
git-sync-simulation/
├── README.md                        # This file
├── BRANCH.md                        # Branch description
├── requirements.txt                 # Python dependencies
├── bootstrap_inside_repo.sh         # Full project setup script
├── clean.py                         # Reset branches between runs
│
└── simulation/
    ├── __init__.py
    ├── app.py                       # Streamlit dashboard (skeleton)
    ├── main.py                      # Headless CLI entry point
    │
    ├── config/
    │   ├── group_storage.yaml       # Storage policy config
    │   ├── group_privacy.yaml       # Privacy policy config
    │   └── group_unconstrained.yaml # Unconstrained policy config
    │
    └── simulation/
        ├── __init__.py
        ├── policy.py                # Policy engine (base + 3 implementations)
        ├── commit_generator.py      # Random file & commit generation
        ├── group_worker.py          # Per-branch worker thread
        ├── sync_engine.py           # Patch filtering & file-copy sync
        ├── cycle_manager.py         # 60-second cycle timer + quota reset
        └── sync_scheduler.py        # Random sync trigger scheduler
```

## Quick Start

```bash
# Setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run headless simulation (60 seconds)
python simulation/main.py --duration 60 --seed 42

# Or run the Streamlit dashboard
streamlit run simulation/app.py

# Clean between runs
python clean.py
```

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--duration` | 180 | Simulation duration in seconds |
| `--seed` | 42 | Random seed for reproducibility |
| `--push` | off | Push commits to remote |

## How It Works

1. **Startup**: Creates git worktrees for each branch, loads policies from YAML
2. **Commit Generation**: Three worker threads generate random 0.8-3.2 MB commits every 12-22 seconds
3. **Sync Scheduling**: Every 25-45 seconds, a random pair of branches is selected for sync
4. **Policy Filtering**: The destination branch's policy decides which patches to accept or reject
5. **Quota Management**: Every 60 seconds, the CycleManager recalculates quotas (carry-over logic)
6. **Event Logging**: All events (COMMIT, SYNC, CYCLE_END, ERROR) are logged in real time

## Dependencies

- Python 3.12+
- GitPython >= 3.1.43
- PyYAML >= 6.0.1
- Pydantic >= 2.7.0
- Streamlit >= 1.35.0 (for dashboard only)
