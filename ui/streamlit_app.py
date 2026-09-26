"""LawSearch UI: scenario search, and case briefs on their own page.

Talks to the FastAPI backend over HTTP so the embedding model lives in one process.

    streamlit run ui/streamlit_app.py      (with `uvicorn app.main:app` running)
"""

import streamlit as st

st.set_page_config(page_title="LawSearch", page_icon="⚖️", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/search.py", title="Scenario search", icon=":material/travel_explore:", default=True),
        st.Page("app_pages/case_brief.py", title="Case brief", icon=":material/gavel:", url_path="case-brief"),
    ],
    position="top",
)

st.title(page.title)
st.caption(
    "Research assistance, not legal advice. Case text comes from unofficial copies and briefs are "
    "AI-generated — always verify against the official decision."
)

page.run()
