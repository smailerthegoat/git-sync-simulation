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
