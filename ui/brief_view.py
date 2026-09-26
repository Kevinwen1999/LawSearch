"""The case brief, as both pages show it.

The brief itself follows the case-brief model answer (case_brief_example_ans.txt): Preliminary
Information, Legal Issue(s), Facts of the case, Ratio Decidendi, Decision. The case's other
elements (purpose, law, disposition, obiter, separate opinions) follow in "Full case reading".
"""

import httpx
import streamlit as st

from lawsearch_client import get_text

RELIED_ON_LABELS = {
    "court": "the court", "party": "a party", "lower_court": "the lower court",
    "concurrence": "a concurrence", "dissent": "a dissent",
}
TREATMENT_LABELS = {
    "followed": "followed", "applied": "applied", "distinguished": "distinguished",
    "not_followed": "not followed", "referred": "referred to",
}


def esc(text: str) -> str:
    # "$200 ... $300" would otherwise render as LaTeX.
    return text.replace("$", "\\$")


def anchor_label(anchor: int, anchor_type: str) -> str:
    return f"¶{anchor}" if anchor_type == "paragraph" else f"passage {anchor}"


@st.cache_data(ttl=3600, max_entries=200, show_spinner=False)
def brief_markdown(case_id: str, created_at: str, anchors: bool, full_reading: bool) -> str:
    # created_at is part of the cache key, so a regenerated brief isn't served stale.
    return get_text(f"/cases/{case_id}/filac/markdown", params={"anchors": anchors, "full_reading": full_reading})


def render_brief(brief: dict, *, show_refs: bool = True, show_full: bool = True) -> None:
    summary, verification, shown = brief["summary"], brief["verification"], brief["display"]
    anchor_type = verification["anchor_type"]
    sections = verification["sections"]

    def ref(item: dict, check: dict | None = None) -> str:
        flag = "" if check is None or check["anchor_ok"] else " ⚠ *anchor not in text*"
        return (f" :gray[{anchor_label(item['anchor'], anchor_type)}]" if show_refs else "") + flag

    if shown["input_kind"] == "description":
        st.info("Based on a description of the case, not the decision itself. Parts the description "
                "doesn't state are marked \"Not stated\".", icon=":material/info:")
    if verification["problems"]:
        st.warning(
            f"{verification['problems']} item(s) could not be verified against the text (marked ⚠). "
            "Check them before relying on this brief.", icon=":material/warning:",
        )

    st.markdown("**Preliminary Information**")
    name = f"*{esc(shown['case_name'])}*" if shown["case_name"] else ""
    name_and_citation = ", ".join(p for p in (name, esc(shown["citation"])) if p) or "Not stated."
    lines = [
        f"- **Name and Citation of Case:** {name_and_citation}",
        f"- **Date of Decision:** {shown['decision_date'] or 'Not stated.'}",
        "- **Parties:**" + ("" if shown["parties"] else " Not stated."),
    ]
    for party, check in zip(shown["parties"], verification["preliminary"]["parties"]):
        lines.append(f"    - {esc(party)}" + ("" if check["found_in_document"] else " ⚠ *not found in text*"))
    st.markdown("\n".join(lines))

    st.markdown("**Legal Issue(s)**")
    lines = []
    for issue, check in zip(summary["issues"]["items"], sections["issues"]):
        lines.append(f"- {esc(issue['question'])}{ref(issue, check)}")
        for sub, sub_check in zip(issue["sub_issues"], check["sub_issues"]):
            lines.append(f"    - {esc(sub['question'])}{ref(sub, sub_check)}")
    st.markdown("\n".join(lines) or "Not stated.")

    st.markdown("**Facts of the case**")
    facts = [(f, c) for f, c in zip(summary["facts"]["items"], sections["facts"]) if f["kind"] == "event"]
    st.markdown("\n".join(f"- {esc(f['text'])}{ref(f, c)}" for f, c in facts) or "Not stated.")

    st.markdown("**Ratio Decidendi**")
    st.markdown("\n".join(
        f"- {esc(r['text'])}{ref(r, c)}" for r, c in zip(summary["ratio"]["items"], sections["ratio"])
    ) or "Not stated.")

    st.markdown("**Decision**")
    st.markdown("\n".join(
        f"- {esc(d['answer'])}{ref(d, c)}" for d, c in zip(summary["decision"]["items"], sections["decision"])
    ) or "Not stated.")

    if warnings := verification.get("rule_warnings"):
        with st.expander(f"Format checks ({len(warnings)})", icon=":material/rule:"):
            st.caption("Places where the brief departs from the case-brief format. The brief is still shown as generated.")
            st.markdown("\n".join(f"- {w['section'].replace('_', ' ').capitalize()}: {esc(w['message'])}" for w in warnings))

    if show_full:
        with st.expander("Full case reading", icon=":material/menu_book:"):
            render_full_reading(summary, sections, ref)

    with st.expander(f"Cited {anchor_type}s (unofficial text)", icon=":material/format_quote:"):
        for anchor, text in verification["anchor_text"].items():
            st.markdown(f"**{anchor_label(int(anchor), anchor_type)}**")
            st.text(text)

    usage = brief["usage"]
    with st.container(horizontal=True, vertical_alignment="center"):
        try:
            markdown = brief_markdown(brief["case_id"], brief["created_at"], show_refs, show_full)
            title = (shown["case_name"] or shown["citation"] or "case").replace(" ", "_")
            st.download_button(
                "Download as Markdown", markdown, file_name=f"Case_brief_{title}.md", mime="text/markdown",
                icon=":material/download:", key=f"download-{brief['case_id']}", on_click="ignore",
            )
        except httpx.HTTPError:
            st.caption("Markdown download unavailable.")
        st.caption(
            f"AI-generated by {usage.get('served_by', brief['model'])} via {brief['backend']} · "
            f"prompt {brief['prompt_version']} · {brief['created_at'][:10]}"
        )


def render_full_reading(summary: dict, sections: dict, ref) -> None:
    def block(title: str, lines: list[str]) -> None:
        st.markdown(f"**{title}**")
        st.markdown("\n".join(lines) or "Not stated.")

    block("Purpose", [f"- {esc(p['text'])}{ref(p, c)}" for p, c in zip(summary["purpose"]["items"], sections["purpose"])])
    block("Facts", [
        f"- {'*Procedural history:* ' if f['kind'] == 'procedural_history' else ''}{esc(f['text'])}{ref(f, c)}"
        for f, c in zip(summary["facts"]["items"], sections["facts"])
    ])

    issues = []
    for issue, check in zip(summary["issues"]["items"], sections["issues"]):
        issues.append(f"- Issue {issue['id']} ({issue['kind']}): {esc(issue['question'])}{ref(issue, check)}")
        issues += [f"    - {esc(s['question'])}{ref(s, sc)}" for s, sc in zip(issue["sub_issues"], check["sub_issues"])]
    issues += [
        f"- *Not decided:* {esc(u['question'])} ({esc(u['reason'])}){ref(u, c)}"
        for u, c in zip(summary["undecided_issues"]["items"], sections["undecided_issues"])
    ]
    block("Issues", issues)

    law = []
    for item, check in zip(summary["law"]["items"], sections["law"]):
        flag = "" if check["found_in_document"] else " ⚠ *not found in text*"
        in_corpus = " · in LawSearch" if check.get("resolved_case") else ""
        statutes = ""
        for resolved in check.get("resolved_sections", []):
            link = f"[{resolved['title']} s. {resolved['section']}]({resolved['url']})"
            note = ""
            if resolved["in_force_after_decision"]:
                note = f" ⚠ *current wording in force since {resolved['in_force_start']}, after this decision*"
            statutes += f"\n    - → {link}{note}"
        law.append(
            f"- **{esc(item['authority'])}** ({item['kind']}; {TREATMENT_LABELS[item['treatment']]}, relied on by "
            f"{RELIED_ON_LABELS[item['relied_on_by']]}): {esc(item['proposition'])}{ref(item, check)}"
            f"{in_corpus}{flag}{statutes}"
        )
    block("Law", law)

    block("Ratio decidendi", [
        f"- {'**Rule:** ' if r['role'] == 'rule' else ''}{esc(r['text'])}{ref(r, c)}"
        for r, c in zip(summary["ratio"]["items"], sections["ratio"])
    ])
    block("Decision", [
        f"- Issue {d['issue_id']}: {esc(d['answer'])}{ref(d, c)}"
        for d, c in zip(summary["decision"]["items"], sections["decision"])
    ])
    block("Disposition", [
        f"- {'*Costs:* ' if d['kind'] == 'costs' else ''}{esc(d['text'])}{ref(d, c)}"
        for d, c in zip(summary["disposition"]["items"], sections["disposition"])
    ])
    block("Obiter dicta", [f"- {esc(o['text'])}{ref(o, c)}" for o, c in zip(summary["obiter"]["items"], sections["obiter"])])
    block("Dissents and separate opinions", [
        f"- *{o['kind'].capitalize()} ({esc(o['judge'])}):* {esc(o['text'])}{ref(o, c)}"
        for o, c in zip(summary["separate_opinions"]["items"], sections["separate_opinions"])
    ])
