"""Display of a case brief in the layout of the case-brief model answer
(case_brief_example_ans.txt): Preliminary Information, Legal Issue(s), Facts of the case, Ratio
Decidendi, Decision, then the full reading of the case's other elements.

Used by the API (display fields and the Markdown export) and the CLI, so every surface shows the
same brief.
"""

import re
from datetime import date

from app.filac import BRIEF_FACT_KINDS, BRIEF_RATIO_ROLES, USER_SOURCES, FilacRecord

# Common abbreviations written without periods in case names ("R. v. Kazemi" -> "R v Kazemi").
_ABBREVIATION = re.compile(
    r"\b(v|R|Ltd|Ltée|Inc|Co|Corp|Cie|Bros|No|Nos|St|Ste|Mr|Mrs|Ms|Dr|Jr|Sr|Assn|Dept|Govt|Int'l)\.",
)
_INITIALISM = re.compile(r"\b(?:[A-Z]\.){2,}")

PARTY_STATUS_LABELS = {
    "moving_party": "moving party", "responding_party": "responding party",
}
RELIED_ON_LABELS = {
    "court": "the court", "party": "a party", "lower_court": "the lower court",
    "concurrence": "a concurrence", "dissent": "a dissent",
}


def case_name(name: str) -> str:
    """A case name without periods in its abbreviations and initialisms ("A.G." -> "AG")."""
    name = _INITIALISM.sub(lambda m: m.group(0).replace(".", ""), name)
    return _ABBREVIATION.sub(r"\1", name).strip()


def long_date(value: date | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return f"{value:%B} {value.day}, {value.year}"


def party_line(party: dict) -> str:
    status = party["status_as_written"].strip() or PARTY_STATUS_LABELS.get(party["status"], party["status"])
    return f"{party['name']} – {status.lower()}"


def decision_line(item: dict) -> str:
    return f"{item['step']}: {item['answer']}" if item.get("step") else item["answer"]


def issue_groups(summary: dict) -> list[dict]:
    """The brief's ratio and decision items (as indexes) under the issue they answer, in issue
    order. A ratio item bearing on several issues goes under the first; items tied to no known
    issue go last, under issue_id None. The ratio's application stays out of the brief."""
    ids = [issue["id"] for issue in summary["issues"]["items"]]
    groups = {i: {"issue_id": i, "question": q["question"], "ratio": [], "decision": []}
              for i, q in zip(ids, summary["issues"]["items"])}
    other = {"issue_id": None, "question": "", "ratio": [], "decision": []}
    for index, item in enumerate(summary["ratio"]["items"]):
        if item["role"] not in BRIEF_RATIO_ROLES:
            continue
        known = [i for i in item["issue_ids"] if i in groups]
        (groups[known[0]] if known else other)["ratio"].append(index)
    for index, item in enumerate(summary["decision"]["items"]):
        groups.get(item["issue_id"], other)["decision"].append(index)
    return [*groups.values(), *([other] if other["ratio"] or other["decision"] else [])]


def display(record: FilacRecord) -> dict:
    """The brief as shown: the preliminary information, which facts appear in the brief and in
    what order (events, then the outcome below), and the ratio and decision grouped by issue
    (shown grouped only when there is more than one issue).

    For corpus cases the database record wins over what the model read from the header; for
    pasted or uploaded text only the header is available."""
    meta, prelim = record.case_meta, record.summary["preliminary"]
    corpus = meta.get("source") not in USER_SOURCES
    name = (corpus and meta.get("style_of_cause")) or prelim["case_name"]
    citation = (corpus and meta.get("citation")) or prelim["citation"]
    decided = (corpus and meta.get("decision_date")) or prelim["decision_date"]
    name = case_name(name) if name else ""
    return {
        "case_name": name,
        "citation": citation or "",
        "name_and_citation": ", ".join(p for p in (name, citation) if p),
        "decision_date": long_date(decided),
        "parties": [party_line(p) for p in prelim["parties"]],
        "input_kind": meta.get("input_kind") or "decision",
        "source": meta.get("source"),
        "brief_facts": [
            index for kind in BRIEF_FACT_KINDS
            for index, fact in enumerate(record.summary["facts"]["items"]) if fact["kind"] == kind
        ],
        "issue_groups": issue_groups(record.summary),
        "word_count": record.verification.get("word_count"),
    }


def anchor_label(anchor: int, anchor_type: str) -> str:
    return f"¶{anchor}" if anchor_type == "paragraph" else f"passage {anchor}"


def to_markdown(record: FilacRecord, *, anchors: bool = False, full_reading: bool = False) -> str:
    summary, verification = record.summary, record.verification
    anchor_type = verification["anchor_type"]
    shown = display(record)

    def ref(item: dict) -> str:
        return f" ({anchor_label(item['anchor'], anchor_type)})" if anchors else ""

    def bullets(items: list[str]) -> list[str]:
        return [f"- {text}" for text in items] or ["- Not stated."]

    title = shown["case_name"] or shown["citation"] or "Case"
    lines = [f"# Case Brief of {title}", ""]
    if shown["input_kind"] == "description":
        lines += ["*Based on a description of the case, not the decision itself.*", ""]

    lines += ["## Preliminary Information"]
    name_line = f"*{shown['case_name']}*" if shown["case_name"] else ""
    name_line = ", ".join(p for p in (name_line, shown["citation"]) if p) or "Not stated."
    lines += [f"- **Name and Citation of Case:** {name_line}"]
    lines += [f"- **Date of Decision:** {shown['decision_date'] or 'Not stated.'}"]
    lines += ["- **Parties:**" + ("" if shown["parties"] else " Not stated.")]
    lines += [f"  - {p}" for p in shown["parties"]]

    lines += ["", "## Legal Issue(s)"]
    issue_lines = []
    for issue in summary["issues"]["items"]:
        issue_lines.append(f"- {issue['question']}{ref(issue)}")
        issue_lines += [f"  - {sub['question']}{ref(sub)}" for sub in issue["sub_issues"]]
    lines += issue_lines or ["- Not stated."]

    facts = summary["facts"]["items"]
    lines += ["", "## Facts of the case"]
    lines += bullets([f"{facts[i]['text']}{ref(facts[i])}" for i in shown["brief_facts"]])

    ratio, decision = summary["ratio"]["items"], summary["decision"]["items"]
    groups = shown["issue_groups"]
    for heading, section, text in (
        ("Ratio Decidendi", "ratio", lambda r: r["text"]), ("Decision", "decision", decision_line),
    ):
        items = ratio if section == "ratio" else decision
        lines += ["", f"## {heading}"]
        if len(groups) > 1:
            for group in groups:
                lines += [f"- *{issue_label(group)}*"]
                lines += [f"  - {text(items[i])}{ref(items[i])}" for i in group[section]] or ["  - Not stated."]
        else:
            shown_items = [items[i] for group in groups for i in group[section]]
            lines += bullets([f"{text(item)}{ref(item)}" for item in shown_items])

    if full_reading:
        lines += ["", "---", "", "## Full case reading", ""]
        lines += _full_reading(summary, ref)
    return "\n".join(lines).rstrip() + "\n"


def issue_label(group: dict) -> str:
    return f"Issue {group['issue_id']}: {group['question']}" if group["issue_id"] is not None else "Other"


RATIO_PREFIXES = {"rule": "**Rule:** ", "reasoning": "", "application": "*Application:* "}
FACT_PREFIXES = {"event": "", "outcome_below": "*Outcome below:* ", "procedural_history": "*Procedural history:* "}


def _full_reading(summary: dict, ref) -> list[str]:
    def section(title: str, items: list[str]) -> list[str]:
        # Items already indented (sub-issues) nest under the item before them.
        body = [i if i.startswith("  ") else f"- {i}" for i in items]
        return [f"### {title}", *(body or ["- Not stated."]), ""]

    issues = []
    for issue in summary["issues"]["items"]:
        issues.append(f"Issue {issue['id']} ({issue['kind']}): {issue['question']}{ref(issue)}")
        issues += [f"  - {sub['question']}{ref(sub)}" for sub in issue["sub_issues"]]
    undecided = [f"Not decided: {u['question']} ({u['reason']}){ref(u)}" for u in summary["undecided_issues"]["items"]]
    facts = [f"{FACT_PREFIXES[f['kind']]}{f['text']}{ref(f)}" for f in summary["facts"]["items"]]
    law = [
        f"{a['authority']} ({a['kind']}; {a['treatment'].replace('_', ' ')}, relied on by "
        f"{RELIED_ON_LABELS[a['relied_on_by']]}): {a['proposition']}{ref(a)}"
        for a in summary["law"]["items"]
    ]
    ratio = [f"{RATIO_PREFIXES[r['role']]}{r['text']}{ref(r)}" for r in summary["ratio"]["items"]]
    decision = [f"Issue {d['issue_id']}: {decision_line(d)}{ref(d)}" for d in summary["decision"]["items"]]
    disposition = [
        f"{'*Costs:* ' if d['kind'] == 'costs' else ''}{d['text']}{ref(d)}" for d in summary["disposition"]["items"]
    ]
    opinions = [
        f"*{o['kind'].capitalize()} ({o['judge']}):* {o['text']}{ref(o)}" for o in summary["separate_opinions"]["items"]
    ]
    return [
        *section("Purpose", [f"{p['text']}{ref(p)}" for p in summary["purpose"]["items"]]),
        *section("Facts", facts),
        *section("Issues", issues + undecided),
        *section("Law", law),
        *section("Ratio decidendi", ratio),
        *section("Decision", decision),
        *section("Disposition", disposition),
        *section("Obiter dicta", [f"{o['text']}{ref(o)}" for o in summary["obiter"]["items"]]),
        *section("Dissents and separate opinions", opinions),
    ]
