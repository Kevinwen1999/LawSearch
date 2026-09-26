"""Case brief page: brief a case from a description, from the database, or from an uploaded file.

The case being shown is in the URL (?case_id=...), so a brief can be reloaded, bookmarked or
opened from a search result.
"""

from uuid import UUID

import httpx
import streamlit as st

from brief_view import render_brief
from lawsearch_client import brief_user_text, fetch_cached_brief, generate_brief, get_json

READ_AS = {"Detect automatically": None, "Description": "description", "Full decision": "decision"}
MATCH_LABELS = {"citation": "citation", "name": "case name", "keywords": "keywords"}

briefs = st.session_state.setdefault("briefs", {})
# Corpus cases found on this page, by id, so a case without a brief yet can still be named.
known_cases = st.session_state.setdefault("brief_known_cases", {})


def current_case_id() -> str | None:
    case_id = st.query_params.get("case_id")
    try:
        return str(UUID(case_id)) if case_id else None
    except ValueError:
        return None


def show_case(case_id: str, brief: dict | None = None) -> None:
    if brief is not None:
        briefs[case_id] = brief
    st.query_params["case_id"] = case_id


def case_label(case: dict) -> str:
    date = case.get("decision_date") or "date unknown"
    return f"{case.get('citation') or 'No citation'} · {case.get('court') or ''} · {date} · {case.get('style_of_cause') or ''}"


@st.cache_data(ttl=600, max_entries=100, show_spinner=False)
def lookup(query: str) -> list[dict]:
    return get_json("/cases/lookup", params={"q": query, "limit": 10}, timeout=120) or []


def render_hits(hits: list[dict], key: str) -> None:
    for hit in hits:
        known_cases[hit["case_id"]] = hit
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.markdown(f"{case_label(hit)}  \n:gray[matched by {MATCH_LABELS[hit['match']]}]")
            if hit["has_brief"]:
                st.badge("Brief ready", icon=":material/check:", color="green")
            if st.button("Open", key=f"{key}-{hit['case_id']}", icon=":material/arrow_forward:"):
                show_case(hit["case_id"])
                st.rerun()


def submit_user_text(**kwargs) -> None:
    with st.spinner("Writing the brief — long decisions can take a few minutes..."):
        body, error = brief_user_text(**kwargs)
    if error:
        st.error(f"Brief failed: {error}")
        return
    st.session_state.brief_suggestions = None
    if body["input_kind"] == "description" and kwargs.get("text"):
        # A description gives a weaker brief than the decision; offer the decision if it's here.
        try:
            st.session_state.brief_suggestions = lookup(kwargs["text"])[:3]
        except httpx.HTTPError:
            pass
    show_case(body["case_id"], body["brief"])
    st.rerun()


describe_tab, search_tab, upload_tab = st.tabs([
    ":material/edit_note: Describe a case", ":material/search: Search the database", ":material/upload_file: Upload a file",
])

with describe_tab:
    text = st.text_area(
        "Description or decision text",
        height=220,
        key="brief_describe_text",
        placeholder="Paste a description of a case — a few lines of notes, a summary or a headnote — or the full text of a decision.",
    )
    read_as = st.segmented_control(
        "Read as", list(READ_AS), default="Detect automatically", key="brief_read_as",
        help="Text with numbered paragraphs or a case header is read as a full decision; anything else as a description.",
    )
    if st.button("Generate brief", type="primary", key="brief_describe_go", icon=":material/gavel:",
                 disabled=not text.strip()):
        submit_user_text(text=text, input_kind=READ_AS.get(read_as or "Detect automatically"))
    if suggestions := st.session_state.get("brief_suggestions"):
        st.markdown("**Possibly in the database.** Briefing the decision itself gives a better grounded brief:")
        render_hits(suggestions, key="suggest")

with search_tab:
    with st.form("brief_lookup_form", border=False):
        query = st.text_input(
            "Citation, case name, or keywords", key="brief_lookup_query",
            placeholder="e.g. 2013 ONCA 585, Kazemi, or cell phone holding while driving",
        )
        searched = st.form_submit_button("Search", icon=":material/search:")
    if searched and query.strip():
        try:
            st.session_state.brief_lookup_hits = lookup(query.strip())
        except httpx.HTTPError as exc:
            st.error(f"Search failed ({type(exc).__name__}).")
    hits = st.session_state.get("brief_lookup_hits")
    if hits is not None:
        if not hits:
            st.info("No case in the database matches.")
        render_hits(hits, key="hit")

with upload_tab:
    uploaded = st.file_uploader("Decision or description file", type=["pdf", "docx", "txt", "md"], key="brief_upload")
    st.caption("PDF (including scanned), Word or text. The file is briefed as it is; it is not added to the search corpus.")
    if st.button("Generate brief", type="primary", key="brief_upload_go", icon=":material/gavel:", disabled=uploaded is None):
        submit_user_text(file=(uploaded.name, uploaded.getvalue()))

st.divider()

case_id = current_case_id()
if case_id is None:
    st.caption("Describe a case, find one in the database, or upload a file to see its brief here.")
    st.stop()

with st.container(horizontal=True):
    show_refs = st.toggle("Show paragraph references", value=True, key="brief_show_refs", persist_state="session")
    show_full = st.toggle("Show full case reading", value=True, key="brief_show_full", persist_state="session")
    show_related = st.toggle("Show related authorities", value=False, key="brief_show_related", persist_state="session")

if case_id not in briefs or briefs[case_id] is None:
    try:
        briefs[case_id] = fetch_cached_brief(case_id)
    except httpx.HTTPError as exc:
        st.error(f"Cannot load the brief ({type(exc).__name__}).")
        st.stop()
brief = briefs[case_id]

if brief is None:
    known = known_cases.get(case_id)
    st.markdown(f"**{case_label(known)}**" if known else f"Case `{case_id}`")
    st.caption("No brief yet for this case.")
    if st.button("Generate brief", type="primary", key="brief_generate", icon=":material/gavel:"):
        with st.spinner("Reading the decision — long judgments can take a few minutes..."):
            brief, error = generate_brief(case_id)
        if brief:
            show_case(case_id, brief)
            st.rerun()
        st.error(error)
    st.stop()

with st.container(border=True):
    render_brief(brief, show_refs=show_refs, show_full=show_full)
    if st.button("Regenerate", key="brief_regenerate", icon=":material/refresh:", type="tertiary",
                 help="Write the brief again with the current model and prompt."):
        with st.spinner("Rewriting the brief..."):
            brief, error = generate_brief(case_id, force=True)
        if brief:
            show_case(case_id, brief)
            st.cache_data.clear()
            st.rerun()
        st.error(error)

if show_related:
    st.subheader("Related authorities in LawSearch")
    st.caption("Searched with this brief's own issues and facts.")
    with st.spinner("Searching..."):
        try:
            related = get_json(f"/cases/{case_id}/related", params={"k": 8}, timeout=120)
        except httpx.HTTPError as exc:
            related = None
            st.error(f"Related search failed ({type(exc).__name__}).")
    for case in (related or {}).get("results", []):
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.markdown(case_label(case))
            st.page_link("app_pages/case_brief.py", label="Brief", icon=":material/gavel:",
                         query_params={"case_id": case["case_id"]})
    if related is not None and not related.get("results"):
        st.caption("Nothing related found.")
