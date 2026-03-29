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
