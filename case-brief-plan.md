# Case brief rework + standalone brief page — plan

Status: **steps 1–3 built and tested; step 4 partly done** (2026-09-25). Committed and pushed. See
§0 for what's done and what's still open.

## 0. Implementation status (2026-09-25)

| Step | Status | Evidence |
|------|--------|----------|
| 1. Schema, prompt, verification, `reverify` fix, old FILAC code removed, CLI, Kazemi eval | **Done** | `eval_briefs` on Opus (production model): **25/25** against `case_brief_example_ans.txt` (`eval/runs/briefs-kazemi-opus.json`); 138 unit tests pass |
| 2. Migration 011, `/cases/lookup`, `/briefs`, `/cases/{id}/related`, Markdown export, `/uploads/decisions` refactor | **Done** | Migration applied (7 old `filac-v1` briefs deleted). Kazemi found by citation, by "Kazemi" (#5 of 10 same-name cases) and by keywords (#1, ~4 s). The same text pasted and uploaded maps to one case; a repeat returns the cached brief in 0.24 s |
| 3. Multipage UI, shared renderer, three-tab brief page, toggles, search cards switched over | **Done** | Headless `AppTest` through the entrypoint: brief renders in the model answer's layout, toggles hide ¶ refs and the full reading, related authorities load, citation search, a description brief shows its notice, no exceptions. **Not yet looked at in a browser** |
| 4. README, acceptance set | **Partly done** | README updated. Unnumbered decision (2001 FCT 266, passage anchors): 0 problems, 0 warnings. Joly, French and dissent cases not run (see Open work) |

### Built differently from the plan

- **Law `treatment` field** (`followed | applied | distinguished | not_followed | referred`)
  instead of the plan's `followed`. `kind` also gains **`secondary`**: the first Qwen run filed
  Driedger's *Construction of Statutes* as a "treaty".
- **Undecided issues are enforced in code** (`filac.enforce_decided_issues`): an issue the model
  also lists under `undecided_issues` is dropped with its decision, and the removal is recorded in
  `verification.dropped_undecided_issues`. Qwen kept listing Kazemi's unargued misapprehension
  ground as an issue even after the prompt said not to.
- **Prompt rules added after the Kazemi runs:**
  - Issues are the substantive question, not "whether the court below erred". Qwen and Opus both
    first framed the issue from the Crown's grounds (¶6) instead of ¶3.
  - The charge or claim is an `event` fact. Qwen dropped ¶2.
  - Costs are never an issue, ratio item or decision. Opus listed the costs request as issue 2.
  - The order ("appeal allowed", "conviction restored") stays out of the decision.
  - The rule is taken in the court's own words. Opus rephrased ¶11, which the overlap check
    flagged at 47%.
- **Verifier fix:** authorities written as "s. 78.1(1) of the Highway Traffic Act" had no search
  terms and were flagged as not in the text. The leading pinpoint is now stripped.
- **API:** `FilacOut` gains `case_meta` and `display`, the preliminary information as shown.
  `GET /cases/{id}/filac/markdown` serves the Markdown export, and both the UI download and the
  CLI use it (`app/brief_format.py`). `/uploads/decisions` keeps its response shape.
- **UI files:** pages live in `ui/app_pages/`, not `ui/pages/`, per Streamlit's guidance, since
  `pages/` triggers the legacy auto-discovery. The HTTP helpers are in `ui/lawsearch_client.py`.
  The "possibly in the database" suggestion runs after a description is briefed, not while typing.
- **Eval backend override:** `scripts/eval_briefs.py` and `filac_cli` take
  `--backend/--model/--effort`, keyed in the cache by model, so local Qwen runs never mix with
  the served Opus briefs.

### Open work

1. **Joly v Pelletier acceptance:** needs the decision text from you. Paste it on the Describe
   tab or run `filac_cli --text-file`, then compare with slides 19–26. A gold file like
   `eval/briefs/kazemi.yaml` would make it scripted, but Joly isn't in the corpus, so
   `eval_briefs.py` would first need a `text_file:` option.
2. **French decision and SCC-with-dissent acceptance** (`separate_opinions`) are not run. Long
   SCC decisions take minutes on Opus each.
3. **Browser check of both pages:** only headless-tested. The layout and spacing of the brief
   card, badges and toggles haven't been looked at.
4. **Brief quality notes from 2001 FCT 266** (no format rule broken, so no warning fires):
   - a Decision item repeated a Ratio sentence word for word;
   - a decision is phrased in the judge's first person ("I am not persuaded");
   - facts are not in chronological order.

   Candidates for a new warning or prompt line once more gold briefs exist.
5. **Only one gold brief.** A single scripted case can overfit the prompt. Add gold files for
   2–3 more decisions of different shapes (tribunal, SCC, a motion like Joly) before tuning the
   prompt further. Kazemi must never be used as a prompt example.
6. **Qwen is not production quality** for briefs: its best run was 21/25 (it still missed the
   charge and listed an undecided ground). Briefs stay on Opus, per the existing model decision.
7. **The 7 deleted `filac-v1` briefs** regenerate on demand only. Nothing re-briefs them in bulk.
8. **Test user text in the database:** one pasted/uploaded description case (the Kazemi
   description used to test dedup) is left as a demo. Delete it from `cases` if unwanted.

---

## Overview

Two changes:

1. **Replace the FILAC brief** with a case brief that follows the case-brief rules in *Reading
   Cases — Chapter 4* (the course slides) and the model answer `case_brief_example_ans.txt`. The
   old Facts / Issues / Law / Analysis / Conclusion brief is **removed completely**: its schema,
   prompt, renderers and cached rows all go. There is no side-by-side period.
2. **Add a standalone "Case brief" page** to the frontend with three ways in:
   - **Describe a case:** paste a description of any length (a few lines up to a full judgment)
     and it is briefed directly.
   - **Search the database:** find a corpus case by citation, name or keywords, and brief it.
   - **Upload a file:** PDF / DOCX / TXT / MD, the same file input as the scenario search, and it
     is briefed.

   The page uses the same brief code and renderer as the case cards on the search page, and can
   be called on its own.

**Naming:** user-facing text says "Case brief". Code identifiers keep `filac` (`app/filac.py`,
`filac_summaries`, `FILAC_*` settings, `/cases/{id}/filac`), so there is no rename churn. New
endpoints use `brief` in their paths.

---

## 1. What the slides require

The slides define two related things. The brief has to follow both without mixing them up.

### 1a. The case brief (slides 28–32): strict format and order

| # | Part | Rule from the slides |
|---|------|----------------------|
| 1 | **Preliminary information** | Name and citation of the case · date of decision · names of the parties · **status** of each party (e.g. "Her Majesty the Queen – appellant", "Khojasteh Kazemi – respondent"). |
| 2 | **Legal issue(s)** | The legal questions the court must decide. They may be matters of fact or of law. Each is a **concise question**, introduced by **"Whether …"**. Sub-issues are allowed (Joly: "Whether the claims disclose a cause of action → Whether someone who is not a human or corporation can be a plaintiff"). |
| 3 | **Relevant facts** | Only the facts **essential to the issues identified**. **Procedural history is left out** of the brief. |
| 4 | **Ratio decidendi** | The rule or principle of law the judge relied on to reach the decision, with the reasoning behind it. This is the binding part, stated as a principle (e.g. "a person owes a duty of care to those who he can reasonably foresee will be affected by his actions"). Often introduced by "here", "in this case", "in my view". |
| 5 | **Decision** | The result of applying the ratio to each issue (e.g. "Neither pleading discloses a cause of action"; "The appeal judge erred in his interpretation that 'holding' … required sustained holding"). |

### 1b. The elements of a case (slides 5–15): the full reading

| Element | Rule from the slides |
|---------|----------------------|
| **Purpose** | Why the case is before the court, procedurally (action for damages, motion to strike under r. 21.01(3)(b) / r. 25.11, appeal from the Superior Court…). |
| **Facts** | What happened: the events leading to the cause of action **plus** the procedural history (decision below, grounds of appeal). |
| **Issues** | Same as 1a. |
| **Law** | What the law said before this dispute: the statutes, rules and cases the decision relies on, **each with the proposition it stands for** (Joly: *Nash* → "accept the facts as alleged unless patently ridiculous…"). It also covers competing lines of authority, and which line the court follows. |
| **Ratio decidendi** | Same as 1a. |
| **Decision** | Same as 1a. |
| **Disposition** | The procedural outcome: where the parties stand ("appeal dismissed", "statements of claim struck and actions dismissed"), **including costs**. |
| **Obiter dicta** | Remarks not necessary to the decision (examples, hypotheticals). Not binding. |
| **Dissents** | Disagreement on the disposition or the ratio. Not binding, but may be persuasive. |
| **Headnotes** | A reporter's summary. Do not rely on it; anchor to the reasons. |

### 1c. Model answer: `case_brief_example_ans.txt` (*R v Kazemi*, 2013 ONCA 585)

This file is the **gold standard** for the brief's content and layout. The case is in the corpus
(`R. v. Kazemi`, ONCA, 2013-09-27, 18 numbered paragraphs, no brief cached yet). Tracing each line
of the answer back to the judgment gives the rules below. Sections 2–5 are written to meet them.

| Answer | Source in the judgment | Rule it sets |
|--------|------------------------|--------------|
| "R v Kazemi, 2013 ONCA 585" | DB `style_of_cause` "R. v. Kazemi" + `citation` | Name and citation on **one line**, name without periods ("R. v." → "R v"). |
| "September 27, 2013" | DB `decision_date` | Date in long form. |
| "Her Majesty the Queen – appellant" / "Khojasteh Kazemi – respondent" | Case **header** ("BETWEEN … Appellant … Respondent"), *before* ¶1 | Parties' **full names** and lower-case status come from the header, which has **no paragraph number**. |
| No court line | — | The court isn't a brief field (it is implied by the citation). |
| Issue: "Whether the respondent was 'holding' the cell phone for the purposes of s.78.1 of the HTA" | ¶3, near word for word | The main issue starts with "Whether". |
| Sub-issue: "Specifically, whether there must be sustained holding…" | ¶5 (the appeal judge's test) / ¶15 | A sub-issue only needs to **contain** "whether". |
| *Not listed:* misapprehension of facts; costs request | ¶6–7 raise them; ¶17 leaves the first undecided; ¶18 decides costs | List **only the issues the court decides**. Grounds the court declines to decide go to the full reading. A costs request goes to disposition. |
| Facts ×3 | ¶1 (without "The facts of this case are simple" and the date), ¶2 first sentence (without the reporter citation or quoted section), ¶3 first sentence | Facts are the court's own words, trimmed. The **charge** (¶2) and the **parties' concessions** (¶3) are facts, not procedural history. One bullet can be a whole paragraph's narrative. |
| *Not listed:* trial conviction, appeal to the OCJ, Crown's grounds | ¶4–¶6 | Procedural history means **decisions below and grounds of appeal** only (slide 8), and it is left out. |
| Ratio ×3 peer bullets | ¶11, ¶12 (without the *Raham* cite), ¶14 first sentence | Ratio is several **flat** bullets, each tied to its own paragraph, in the court's words: the interpretive rule first, then the reasoning (purpose, policy). It is stated at the level the court decided, not made more abstract. |
| *Not in ratio:* Driedger (¶9), *Legislation Act* s. 64 (¶10), Hansard (¶13) | — | The interpretive framework and authorities are **Law**, not ratio. |
| Decision: "The appeal judge erred in his interpretation that 'holding'… required sustained holding." | ¶16, first sentence | The decision is the court's answer to the issue. "Appeal allowed and conviction restored" (¶16, second sentence) is **disposition** and is left out of the brief. |
| Headings | — | Use the exact labels: *Preliminary Information* (*Name and Citation of Case*, *Date of Decision*, *Parties*), *Legal Issue(s)*, *Facts of the case*, *Ratio Decidendi*, *Decision*. There are no issue tags on facts and no paragraph references. |

### How the current FILAC maps onto this

| Current FILAC | Problem against the slides |
|---------------|-----------------------------|
| No preliminary information | Parties and their status are missing entirely. For uploads, even citation and date are missing (`create_upload_case` stores only the text). |
| `issues` are free text | Not phrased as "Whether …" questions, and no sub-issues. |
| `facts` | Mixes events with procedural history, and isn't tied to the issues. |
| `law` | Lists authorities without **the proposition each stands for**. |
| `analysis` (mixed attributions) | No ratio. The binding principle isn't separated from the reasoning, party arguments or obiter. |
| `conclusion` | Merges decision (per issue) with disposition (procedural outcome + costs). |
| — | No purpose, no obiter, no dissent section. |

---

## 2. Backend: new brief schema, prompt and verification

All in `app/filac.py`. The existing machinery stays: paragraph/passage anchors,
`extract_with_fallback`, the cache keyed by `(case_id, prompt_version, model)`, and statute/case
resolution. **Model and backend don't change** (still `claude-opus-5` via the configured backend).

### 2.1 Schema (`BRIEF_SCHEMA`, replacing `FILAC_SCHEMA`)

It stays strict: every object has `additionalProperties: false` and every property is required.
Optional values are `["string", "null"]` or `["integer", "null"]`. Every item carries an `anchor`.

```text
preliminary:                                    # read from the case header, which has no ¶ number
  case_name        text|null                    # as written, e.g. "R. v. Kazemi"
  citation         text|null
  decision_date    text|null                    # ISO date when stated
  court            text|null                    # full reading / caption only, not a brief line
  parties[]        {name, status, status_as_written}
                   status ∈ appellant | respondent | plaintiff | defendant | applicant |
                            moving_party | responding_party | accused | crown | intervener | other
purpose            {status, items[] {text, anchor}}
issues             {status, items[] {id, question, kind: law|fact|mixed, anchor,
                                     sub_issues[] {question, anchor}}}      # decided issues only
undecided_issues   {status, items[] {question, reason, anchor}}             # e.g. Kazemi ¶17
facts              {status, items[] {text, kind: event|procedural_history, issue_ids[], anchor}}
law                {status, items[] {authority, kind, proposition, relied_on_by,
                                     treatment: followed|applied|distinguished|not_followed|referred, anchor}}  # as built (§0)
ratio              {status, items[] {text, role: rule|reasoning, issue_ids[], anchor}}  # flat, in order
decision           {status, items[] {issue_id, answer, anchor}}
disposition        {status, items[] {text, kind: outcome|order|costs, anchor}}
obiter             {status, items[] {text, anchor}}
separate_opinions  {status, items[] {judge, kind: dissent|concurrence, text, anchor}}
```

Preliminary fields have **no anchor**. The header before ¶1 isn't a numbered paragraph, so they
are checked by finding them in the text instead (§2.3). `kind: event` covers the charge or claim
itself and the parties' concessions. `procedural_history` is only the decisions below and the
grounds of appeal.

`status` stays `stated | not_stated_in_text`, as today.

### 2.2 Prompt (`SYSTEM_PROMPT`)

Rewrite it around the slide rules. Each rule becomes an explicit instruction, and the existing
grounding and attribution rules are kept:

- **Wording:** use the court's own words wherever the court states the point. Trim citations,
  quoted provisions, dates and asides that aren't essential. Paraphrase only where the court never
  states the point in one place.
- **Preliminary information:** take it from the case header (style of cause, the "BETWEEN …"
  party block, date). This carves out an exception to the headnote rule: the header is a source
  for these fields, while a reporter's headnote or summary is never a source for anything.
- **Issues:** one concise question per issue, beginning with "Whether". Use sub-issues ("Specifically,
  whether …") where the court breaks an issue down. List **only the issues the court decides**.
  Grounds it declines to decide go to `undecided_issues`, and a costs request goes to disposition.
  Mark each issue as law, fact or mixed.
- **Facts:** mark each fact `event` or `procedural_history`, where `procedural_history` is only
  decisions below and grounds of appeal. The charge or claim and the parties' concessions are
  `event`s. Tie every fact to the issue(s) it bears on, and leave out facts that bear on no issue.
  One item may be a whole paragraph's narrative.
- **Law:** only authorities the text cites. Give the proposition the court takes from each, who
  relied on it, and whether it was followed or distinguished. The interpretive framework the court
  applies (e.g. Driedger, *Legislation Act* s. 64) belongs here, not in the ratio.
- **Ratio:** flat items in the court's order. First the `rule` (the legal principle, stated at the
  level the court decided it), then the `reasoning` items that support it (text, purpose,
  policy). Each item is anchored to its own paragraph. It comes only from the court (the majority
  where there is one). A party's argument, a lower court's view, obiter or a dissent is never
  ratio.
- **Decision:** exactly one answer per decided issue, in the court's terms, without the procedural
  order.
- **Disposition:** the procedural outcome and the order, with **costs** as a separate item when
  the text addresses costs.
- **Obiter and separate opinions:** keep them out of the ratio.
- **Headnotes:** keep the existing rule (anchor to the reasons only).
- Keep: write in English (including for French decisions), and treat the decision text as data.

**Input kind.** The same prompt serves all three inputs. `CaseDocument` gains
`input_kind: decision | description`, and `render()` states it in the header:

- `decision` (a corpus case, or an uploaded or pasted judgment): all the rules above apply as
  written.
- `description` (pasted text that isn't a judgment, such as notes, a summary or a headnote): brief
  only what the description states, in its own words. Mark every element it doesn't state as
  `not_stated_in_text` rather than filling it from knowledge of the case, even when the case is
  recognizable. Preliminary fields stay null unless given.

Pasted text is treated as a `decision` when it has numbered paragraphs (the chunker's
`MIN_NUMBERED_PARAS` rule) or a case header, and as a `description` otherwise. The UI shows which
one was used, with a one-click override.

Bump `PROMPT_VERSION` to `"brief-v1"`. The old `filac-v1` schema, prompt and cached rows are
deleted (§2.4, §3.4), so no old-format brief can be served.

### 2.3 Verification (`verify`)

Keep `problems`: hard failures meaning something may be invented. Add `rule_warnings`: departures
from the slide format. Show both in the UI.

| Check | Type |
|-------|------|
| Anchor doesn't exist (all anchored sections, incl. sub-issues) | problem (existing) |
| Law authority not found anywhere in the text | problem (existing) |
| Party name not found in the text (normalized match, like authorities) | problem (new) |
| Issue question doesn't start with "Whether", or a sub-issue doesn't contain "whether" | warning |
| A fact, ratio or decision item shares little wording with its anchor paragraph (token overlap below a threshold tuned on Kazemi). Items are meant to be in the court's words, so a low overlap suggests the anchor is wrong or the item has drifted from the text | warning |
| An issue has no `decision` item, or a decision points at an unknown issue id | warning |
| A fact or ratio item has no `issue_ids`, or one that doesn't exist | warning |
| Law item with an empty `proposition` | warning |
| Preliminary metadata disagrees with the database (citation/date/court) for corpus cases | warning (the database value is shown) |

Statute resolution (`resolve_statutes`) and case resolution (`resolve_cases`) carry over unchanged
to `law`.

### 2.4 Other backend touch-points

- **Full replacement:** delete `FILAC_SCHEMA`, the old `SYSTEM_PROMPT`/`SECTIONS`, and every
  `analysis`/`conclusion` code path in `app/filac.py`, `scripts/filac_cli.py`,
  `ui/streamlit_app.py` and `tests/test_filac.py`. Nothing reads the old shape afterwards.
- `related_authority_query`: use the issue questions (without the leading "Whether") plus the
  `event` facts.
- **Fix `reverify` for uploaded decisions.** `load_document` builds passages from `case_chunks`,
  which uploads never write. An unnumbered upload therefore gets no anchors on re-verify, and every
  item is flagged. Build the upload's passages in memory (`chunk_judgment`), the same way
  `generate_for_upload` does.
- `scripts/filac_cli.py`: print the new format, in brief order first and then the full reading.
  Add `--text-file PATH` to brief a local decision through the upload path.

---

## 3. API

| Page input | Endpoint(s) |
|------------|-------------|
| Search the database | `GET /cases/lookup`, then `GET/POST /cases/{case_id}/filac` |
| Describe a case | `POST /briefs` with `text` |
| Upload a file | `POST /briefs` with `file` |
| "Show related authorities" toggle (any input) | `GET /cases/{case_id}/related` |

### 3.1 `GET /cases/lookup?q=…&limit=10`: the database search bar

Candidates are resolved in this order. Each result carries `match: citation | name | keywords`
and `has_brief`, which says whether a `brief-v1` brief is already cached.

1. **Citation:** parse `q` with `app.citations.case_citations`/`canonical` (handles
   "2013 ONCA 585", "[1991] 2 S.C.R. 456", French "CSC/CAF"), then match `citation` or `citation2`.
2. **Name:** match `style_of_cause` with trigram similarity (`pg_trgm`), e.g. "Kazemi",
   "Joly v Pelletier".
3. **Keywords:** if nothing matched, run `retrieval.search(conn, q, k)` and return its cases. This
   finds the case you remember but can't name, e.g. "cell phone holding red light HTA".

Only corpus cases are returned (`source` not `upload`/`pasted`).

### 3.2 `POST /briefs`: brief a description or an uploaded file

- Form fields: `text` **or** `file`, plus `force: bool = false` and an optional
  `input_kind: decision | description` override (default: detected, see §2.2).
- **Text of any length is accepted.** There is no minimum, because a short description is a valid
  input: whatever it doesn't state comes back `not_stated_in_text`.
- A file goes through `intake.extract_text` (PDF / DOCX / TXT / MD, OCR fallback), the same as
  `/scenarios`, and the original is stored in MinIO.
- The text is stored as a minimal `cases` row with `source = 'pasted'` or `'upload'`, which never
  gets chunked or embedded, as today. It is **deduplicated by content hash**, so the same text,
  whether pasted or uploaded, returns the cached brief instead of another Opus call.
- Returns `{case_id, source, input_kind, extracted_chars, brief}`.
- `POST /uploads/decisions` (Phase 7) is refactored onto the same helper and keeps its response
  (brief + related search), now with the new brief shape.

### 3.3 `GET /cases/{case_id}/related?k=10`: related authorities, on demand

This builds `related_authority_query` from the **cached** brief and runs `retrieval.search`. It
returns 404 if there is no brief yet. Keeping it separate from brief generation means the page
toggle can turn related authorities on or off after a brief exists, with no regeneration.

`GET/POST /cases/{case_id}/filac` keep their paths and return the new shape. The search page's case
cards and the brief page both use them.

### 3.4 Migration `011_case_brief.sql`

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX cases_style_of_cause_trgm_idx ON cases USING gin (style_of_cause gin_trgm_ops);

ALTER TABLE cases ADD COLUMN content_hash text;
CREATE UNIQUE INDEX cases_user_text_hash_key ON cases (content_hash)
    WHERE source IN ('upload', 'pasted');

-- The FILAC brief is replaced outright: no old-format brief survives.
DELETE FROM filac_summaries WHERE prompt_version <> 'brief-v1';
```

(Check that `pg_trgm` is available in `docker/postgres/Dockerfile`'s image. It is contrib in the
official image.)

---

## 4. Frontend (Streamlit 1.63)

### 4.1 Multipage with real routes

`ui/streamlit_app.py` becomes the entrypoint only: `st.set_page_config`, disclaimer, and
`st.navigation` with two `st.Page`s. `run.ps1` keeps launching the same file.

| Page | File | Route |
|------|------|-------|
| Scenario search (default) | `ui/pages/search.py` | `/` |
| Case brief | `ui/pages/case_brief.py` | `/case-brief` |

Shared code moves out of the page script:

- `ui/api.py`: `API` base URL, httpx helpers (`post_json`, error-detail extraction).
- `ui/brief_view.py`: `render_brief(brief)` and its labels. This is the one renderer both pages
  use, which gives the "reuse the summary code for each case" requirement.

The search page's filters sidebar moves into `search.py` so it doesn't appear on the brief page.

**The search page switches over completely.** Each case card's "FILAC brief" expander becomes
"Case brief", rendered by the same `render_brief`, and the button reads "Generate case brief".
Each card also gets an **"Open on brief page"** link to `/case-brief?case_id=<id>`.

### 4.2 Case brief page

Three tabs, one per input. Each tab ends in the same brief view (§4.3).

```
Case brief
[ Describe a case ]  [ Search the database ]  [ Upload a file ]

Describe a case:     [ text area: any length — a few lines of notes up to a full judgment ]
                     Read as: (•) detected: description  ( ) full decision      (Generate brief)
                     Possibly in the database: 2013 ONCA 585 R. v. Kazemi → [Brief the full decision]

Search the database: [ citation, case name, or keywords                   ] (Search)
                     → 2013 ONCA 585 · ONCA · 2013-09-27 · R. v. Kazemi       [brief ready]
                       (pick one) → [Generate brief] or cached brief + [Regenerate]

Upload a file:       [ file: PDF / DOCX / TXT / MD ]                              (Generate brief)
```

- **Describe:** a brief made from a description is labelled "Based on your description, not the
  decision itself". A short description gives a partial brief, with the elements it doesn't
  state shown as "Not stated". The "possibly in the database" line runs `/cases/lookup` on the
  text (cheap, no model call) and suggests briefing the full decision, which is better grounded.
  It is only a suggestion and never blocks.
- **Search:** results show a "brief ready" badge where a brief is cached, so opening those is
  instant.
- **Upload:** the same accepted types and OCR as the scenario search's upload.
- **Toggles** (all modes, remembered per session):
  - "Show related authorities": **off by default**; calls `/cases/{id}/related` when turned on.
  - "Show full case reading": on.
  - "Show paragraph references": on.

  No toggle can hide any part of the five-part brief in the model answer (§1c). Those parts are
  always shown and always complete.
- `?case_id=` in the URL loads that case's brief directly. After a description or upload, the page
  sets `?case_id=` to the new case, so reload and bookmarking work.
- The spinner copy and 20-minute timeout are the same as today's generate button.

### 4.3 How a brief is shown (`render_brief`)

The brief section reproduces the **layout of `case_brief_example_ans.txt`**: its headings,
order and nesting.

1. **Preliminary Information**
   - *Name and Citation of Case:* `R v Kazemi, 2013 ONCA 585`. The name has its periods removed
     ("R. v." → "R v"), done in code rather than by the model. Database metadata wins for corpus
     cases, and the model's header values fill in for uploads.
   - *Date of Decision:* long form, `September 27, 2013`
   - *Parties:* `Her Majesty the Queen – appellant`, one party per line
2. **Legal Issue(s):** bulleted "Whether …" questions, with sub-issues nested beneath
3. **Facts of the case:** `event` facts only, with no procedural history and no issue tags
4. **Ratio Decidendi:** flat bullets in the model's order (rule, then reasoning)
5. **Decision:** one bullet per decided issue

The ¶ reference for each item is shown as a small muted suffix, which a "Show paragraph
references" toggle can hide, so the on-screen brief can look exactly like the answer. The court,
kinds and attributions don't appear in the brief section.

Then an expander, **"Full case reading"**, in slide-5 order: Purpose · Facts (incl. procedural
history) · Issues (incl. undecided grounds and why) · Law (authority → proposition, relied on by,
followed/distinguished, statute links as today) · Ratio · Decision · Disposition (incl. costs) ·
Obiter · Dissents / separate opinions.

Unchanged: the ⚠ markers for `problems`, the "Cited paragraphs" expander and the model/prompt
caption. New: a short list of `rule_warnings`, and a **Download as Markdown** button. The download
uses the answer file's exact layout, starting with "Case Brief of *R v Kazemi*", with paragraph
references optional.

---

## 5. Tests and acceptance

**Unit tests** (`tests/test_filac.py`; no model calls):

- The schema walker test is updated for `BRIEF_SCHEMA`, including nullable fields.
- `verify`: missing anchors in preliminary/sub-issues, a party name not in the text, a non-"Whether"
  issue, an issue with no decision, dangling `issue_ids`.
- `related_authority_query` builds from issues and event facts.
- `reverify` on an unnumbered upload keeps its passage anchors.
- Lookup: citation parsing precedence (citation > name > keywords).
- Input-kind detection: numbered paragraphs or a case header → `decision`; short notes →
  `description`; the override wins.
- Content-hash dedup: the same text pasted, then uploaded as .txt, maps to one case row.
- No test, module or renderer still refers to the old `analysis`/`conclusion` shape (grep check).

**Acceptance against the slides' own examples:**

- ***R v Kazemi*, 2013 ONCA 585** (in the corpus): **scripted** against `case_brief_example_ans.txt`.
  `eval/briefs/kazemi.yaml` records the answer as anchors, and `scripts/eval_briefs.py` scores a
  generated brief against it with no human judgement needed:

  | Section | Must include | Must not include |
  |---------|--------------|------------------|
  | Preliminary | "R v Kazemi, 2013 ONCA 585"; 2013-09-27; Her Majesty the Queen = appellant; Khojasteh Kazemi = respondent | — |
  | Issues | one decided issue anchored ¶3, with a sub-issue on sustained vs momentary holding | the misapprehension ground (¶6/¶17 → `undecided_issues`); costs (¶7/¶18 → disposition) |
  | Facts (`event`) | ¶1, ¶2, ¶3 | ¶4–¶7 (must be `procedural_history` or absent) |
  | Ratio | ¶11, ¶12, ¶14 (rule first, anchored ¶11) | ¶9, ¶10 (→ Law) |
  | Decision | ¶16 | "appeal allowed / conviction restored" (→ disposition) |
  | Law (full reading) | Driedger, *Legislation Act* s. 64, *Raham*, *Felderhof* | — |

  Also: the Markdown export, with paragraph references off, is compared by eye with the answer
  file for layout.
- ***Joly v Pelletier*** (Ontario Superior Court, not in the corpus). Paste its text on the new page
  and compare against slides 19–26, e.g. sub-issue "whether someone who is not a human or a
  corporation can be a plaintiff", *Nash*/*Carey Canada*/*Steiner* with their propositions, and a
  disposition of claims struck and actions dismissed. **Needs the decision text from you.**
- One unnumbered tribunal decision (passage anchors), one French decision, and one SCC decision with
  a dissent, to exercise `separate_opinions`.
- **Descriptions**, each through the Describe tab:
  - A short one: the Kazemi summary written in a few lines. Expect a partial brief, with
    unstated parts marked "Not stated", nothing filled in from knowledge of the case, and Kazemi
    suggested from the database.
  - A long one: the Joly summary from slide 18. Expect issues, ratio and decision that match
    slides 21–25 where the summary states them.

---

## 6. Build order

| Step | Scope | Done when |
|------|-------|-----------|
| 1 | New schema and prompt (incl. input kinds), verification, `reverify` fix, **old FILAC code removed**, CLI, `eval/briefs/kazemi.yaml` + `scripts/eval_briefs.py` | Unit tests pass; Kazemi passes every row of the §5 table |
| 2 | Migration 011 (incl. deleting old briefs), `/cases/lookup`, `/briefs`, `/cases/{id}/related`, `/uploads/decisions` refactor | Lookup finds Kazemi by citation, by "Kazemi" and by keywords; the same text pasted twice returns the cached brief |
| 3 | Multipage UI, `brief_view.py`, three-tab brief page with toggles, search-page cards switched to the new brief + "Open on brief page" | Both pages render the same brief; `?case_id=` deep link works; related authorities toggle on and off without regenerating |
| 4 | README (architecture diagram, endpoints, "FILAC" → "case brief" in user-facing text), acceptance set above | Joly, the descriptions and the three extra cases reviewed |

Commit per step, only when you ask (per the usual workflow).

---

## 7. Decisions (settled 2026-09-25)

1. **Brief and full reading, both from one model call.** The five-part brief matches the model
   answer, and the slide-5 elements go in the "Full case reading" section (§4.3).
2. **Naming: minimal.** "Case brief" in user-facing text. Code, table, settings and existing routes
   keep `filac`, and only new endpoints use `brief` (§ intro).
3. **Three inputs on the brief page:** describe a case (any length, briefed directly), search the
   database (citation / name / keywords), and upload a file (§3, §4.2).
4. **The old FILAC brief is replaced completely:** its code is removed, and cached `filac-v1` rows
   are deleted in migration 011. The search page uses the new brief too (§2.4, §3.4, §4.1).
5. **Related authorities are off by default** and can be switched on or off at any time without
   regenerating (§3.3). Display toggles never hide any part of the model answer's brief (§4.2).

## 8. Risks

- **Longer output:** about double the fields, so more output tokens and a slower first generation,
  on the same Opus model. Mitigation: the cache, and content-hash deduplication.
- **Dangling issue ids** (a fact pointing at issue 4 when there are 3) are caught as
  `rule_warnings`, not silently dropped.
- **Ratio versus the court's reasoning restated:** this is the hardest rule for the model to meet.
  Kazemi (scripted) and Joly are the checks. If it drifts, add a worked example to the prompt, but
  **not Kazemi itself**, which would make its eval meaningless. Use Joly (slide 24) or another
  decision instead.
- **Word-for-word bias:** "use the court's words" can pull in whole paragraphs. The Kazemi answer
  trims (it drops "The facts of this case are simple", the date, citations and the quoted
  section), so the prompt must ask for trimming, and the overlap check has only a lower bound.
- **Descriptions invite filling in from memory:** a recognizable case (e.g. "the Kazemi cell phone
  case") tempts the model to fill gaps from what it knows. The `description` prompt forbids it, and
  the overlap check flags items with little wording in common with the description. A description
  gives a weaker brief than the decision, which is why the Describe tab suggests the database match.
- **Deleting old briefs is one-way:** anything cached under `filac-v1` has to be regenerated
  (Opus time), and only when someone opens it. This is accepted per decision 4.
- **The "strip periods" name rule** is a display rule for common forms ("R. v.", "v.", "Ltd.",
  "Inc."), not a full McGill citation engine.
