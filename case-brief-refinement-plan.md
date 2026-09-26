# Case brief refinement: plan

Status: **built and tested** (2026-09-26), not committed. See §0a for results and how the build
differs from the plan. This plan builds on `case-brief-plan.md`, which
describes the brief as built (`brief-v1`). Its source is `case_brief_transcript.txt`, a class in
which the instructor works through a brief of *R v Grayston*, 2016 ONCA 784, and then sets out the
assignment rubric. Line references below (l. N) are to that transcript.

## 0. Summary

*R v Grayston* is in the corpus and already has a cached Opus brief. Today's checks give that brief
**0 problems and 0 warnings**, but the instructor's answer disagrees with it in five places:

1. **Ratio mixes in the application.** The ratio has the test (¶16) plus ¶17, ¶19 and ¶20, which
   apply the test to these facts. The instructor's ratio is the test alone, and the application
   is the decision.
2. **The decision is missing a stage.** It gives only ¶20. The instructor's decision answers
   stage 1 of the test (¶17), stage 2 (¶20, first sentence), and then the issue itself (¶20, last
   sentence).
3. **Arguments and trial conduct listed as facts.** ¶8 (what the Crown relied on) and ¶9 (the
   appellant didn't testify) are `event` facts. The instructor calls both procedural.
4. **The test's stages became sub-issues.** The instructor's brief has one issue with no
   sub-issues. The two stages are the ratio, and their answers go in the decision.
5. **Too long for one page.** The brief runs to 639 words. The model answer for Kazemi is 354 words,
   and the assignment's brief must fit on one page.

The Kazemi brief (which scores 25/25) has the same first flaw: its ratio includes ¶15, where the court
rejects the appeal judge's reading. The model answer leaves ¶15 out, but the Kazemi gold doesn't
check for it.

The fix has four parts. Draw a clear line between ratio and application. Give decisions per-stage
answers. Split facts three ways. Add a one-page length budget. Then add Grayston as a second
scripted gold brief, so the next round of prompt changes is checked against two cases, not one.

## 0a. Implementation status (2026-09-26)

All four build steps are done, with D1–D4 taken as recommended: the outcome below is shown in the
brief; stage answers are in the decision; the length budget is a warning only; `brief-v1` rows
are left in place and simply no longer served. 149 unit tests pass.

| Run | Grayston | Kazemi |
|-----|----------|--------|
| Cached `brief-v1` (baseline, `eval/runs/briefs-v1-baseline.json`) | 26/31 | 27/28 |
| `brief-v2`, first prompt | 27/31 | 26/28 |
| `brief-v2` + application role + costs rule | 31/31 | 28/28 |
| Same prompt, rerun | 31/31 | 27/28 (decision added the ¶11 rule) |
| Decision rule tightened | 30/31 (a full-reading item merged ¶1 into ¶8) | 28/28 |
| Final prompt: examples that echoed the golds made neutral (`eval/runs/briefs-v2-opus.json`) | 31/31 | 28/28 |

The Grayston brief is now 332 words and reads like the instructor's answer:
- one issue from ¶2, with no sub-issues;
- facts from ¶4–7, plus the trial result;
- the ¶16 test as the only ratio item;
- stage answers from ¶17 and ¶20, then "The conviction is unreasonable".

2001 FCT 266 (unnumbered, passage anchors): 0 problems, 763 → 468 words. Three format
warnings remain, all genuine: two conclusions for one issue, one of which doesn't answer it, and
one rule item with a weak anchor. The headless `AppTest` renders all three briefs with no
exceptions, with the word count in the caption.

### Built differently from the plan

- **Ratio role `application`** (not in the plan). With only "leave it out", Opus kept putting the
  court's rejection of the reasoning below (Kazemi ¶15) and its application of the test into the
  ratio. Now those items have their own role, which is shown in the full reading only
  ("*Application:*") and never in the brief. This mirrors how `procedural_history` works for
  facts.
- **Costs are enforced in code.** The prompt rule was already there, but Opus listed the costs
  request as issue 2 again. `enforce_decided_issues` now drops a costs issue, with its decision,
  when it sits beside other issues. A costs-only appeal keeps it.
- **Two extra prompt rules** came out of the runs:
  - where the court reviews several tests and applies one, the others are law and not reasoning
    either (Grayston ¶14, *Mars*);
  - the conclusion uses the words of one statement and adds nothing from elsewhere (Kazemi's
    decision had picked up the ¶11 rule).
  
  Step labels are limited to a few words, because long labels had pushed Grayston over the budget.
- **Two extra warnings.** A sub-issue anchored to a paragraph that ends in a citation (the Charemski
  ¶13 trap reappeared as a sub-issue). A ratio reasoning item from a paragraph about the court
  below.
- **The word count** covers what the model writes: issues, facts in the brief, ratio in the brief,
  and decision. It leaves out headings and the preliminary lines, which are fixed. The warning is
  over 500 words, and the prompt asks for about 450.
- **Display logic in one place.** `brief_format.display()` now returns `brief_facts` (order:
  events, then the outcome below), `issue_groups` (ratio and decision by issue) and `word_count`.
  The Markdown export, the CLI and the Streamlit view all render from these.
- **Kazemi gold:** `ratio.not_anchors` gains ¶15, and the facts checks are now
  `outcome_below_only: [4, 5]` and `not_brief_anchors: [6, 7]`.

### Still open

- **Only two golds,** both ONCA appeals. Joly (a motion to strike) is still waiting on the text.
- **Run-to-run variance.** Across the last four Opus runs, at most one check failed per case per
  run, never the same one twice.
- **A Kazemi echo from before this change:** the Law rule's citation-format example is "Highway
  Traffic Act, R.S.O. 1990, c. H.8, s. 78.1(1)", Kazemi's own statute. It's harmless as a format
  example, but it's the gold case's wording, so it's worth swapping for a neutral statute.
- **The brief page hasn't been looked at in a browser** since this change (headless only).

---

## 1. What the transcript teaches, against what we have

| # | Rule taught | Transcript | Current plan / code | Gap |
|---|-------------|-----------|---------------------|-----|
| T1 | On appeal, the issue is the question for **the appeal court** about the decision below (was the verdict unreasonable?), not the trial's question (did he handle the balaclava?) | l. 83–108 | Prompt: "the substantive question the court resolves ... not as whether the court below erred" | **Partly conflicts.** That rule came from Kazemi, where the appeal question is a question of law decided afresh. In Grayston the question *is* about the verdict below. The rule needs to cover both kinds of appeal |
| T2 | A question stated while reviewing another case's test (¶13, ending in the *Charemski* citation) is that case's issue. This case's issue is where the court identifies it (¶2) | l. 113–133 | Prompt: "Where the court states 'the issue is whether ...', follow its words" | **Trap.** ¶13 literally says "the question is whether". There's no rule against taking an issue from a paragraph that reviews the law |
| T3 | The review of the law (*Biniaris*, *Charemski*, *Mars*, *D.D.T.*) is the ratios of **other** cases, and it stays out of the brief. This case's ratio is the test the court **chooses and applies to the facts**. You find it by looking for where the court applies it ("In this case ...") | l. 150–202 | Prompt puts the interpretive framework and authorities in `law` | Mostly fine. What's missing is the positive test ("the ratio is the test the court then applies"), which points the model at ¶15/¶16 |
| T4 | The test may be quoted word for word, from either the court's general statement (¶15) or its restatement for the case (¶16). The judge's wording carries more weight | l. 199–221 | "Take the rule ... in the court's own words" | Fine. The Grayston rule was rephrased into a general "accused ... object bearing his DNA", which isn't the court's wording |
| T5 | The application of the test to the facts is the **decision**: the answer to each stage, then the answer to the issue ("His conviction was therefore unreasonable") | l. 230–277 | Decision: "exactly one answer per decided issue" | **Gap.** No stage answers. The application ends up in the ratio as `reasoning` |
| T6 | Decision ≠ disposition. "Appeal allowed, conviction quashed" is disposition and stays out of the brief | l. 238–243, 402 | Same rule | Fine |
| T7 | The decision reads as **the answer to the issue**. Two issues mean two answers, two ratios and two decisions | l. 277, 401 | Ratio and decision are flat lists, tied to issues only by `issue_ids` | **Gap for multi-issue cases.** The brief doesn't group ratio and decision by issue |
| T8 | Pick out the concluding sentences. Don't paste whole paragraphs | l. 288–291 | "Use the court's own words ... trimmed" | The Grayston decision merges two sentences of ¶20. That's acceptable, but the prompt should ask for the concluding sentence(s) only |
| T9 | Facts are the events that brought the parties to court. Not every detail is essential ("a Sunbird, whatever") | l. 141–149, 292–299 | "One item may be a whole paragraph's narrative" | **Pulls toward whole paragraphs.** No rule on dropping incidental detail |
| T10 | Party **arguments** (¶8), and who testified or didn't (¶9), are procedure, not facts | l. 309–314 | `procedural_history` = only the decisions below and the grounds of appeal | **Gap.** Arguments and the conduct of the trial fall through as `event` |
| T11 | The charges matter. **The outcome below matters** ("the trial judgment matters"), but the process of the trial doesn't | l. 326–327, 397 | The trial outcome is `procedural_history`, which is hidden from the brief | **Conflicts with the Kazemi answer**, which leaves out the trial and appeal outcomes (¶4–5). See decision D1 |
| T12 | Date of decision is the release date (Oct 26), not the hearing date (Oct 3) | l. 29–40 | Database date wins for corpus cases. The prompt says only "date lines" | **Gap for uploads**, where the model reads the header. No rule and no check |
| T13 | Parties are the litigants only. Counsel, duty counsel and judges (JJ.A.) are not parties. The style of cause names the parties (R = the Crown) | l. 58–63, 350–383 | Prompt: "each party's full name as written" | Fine on both golds. No explicit rule and no check |
| T14 | One page (Times New Roman 11, single-spaced). Clear and concise, not an essay. Each part should be obvious at a glance | l. 290–291, 406, 411–414 | "Most decisions need 1-3 issues, 3-8 facts and 2-6 ratio items" | **No length budget.** Grayston: 639 words |
| T15 | No citations or paragraph numbers are needed inside the brief, and there's no reference list | l. 331–345, 416, 449 | ¶ references can be toggled. The Markdown export leaves them off by default | Fine |
| T16 | The brief has no purpose, disposition or review of the law | l. 277–279 | These are in "Full case reading" only | Fine |

---

## 2. Grayston: the brief we have, against the instructor's answer

| Part | Instructor | Cached `brief-v1` brief | Verdict |
|------|-----------|-------------------------|---------|
| Name, citation, date | R v Grayston, 2016 ONCA 784 · October 26, 2016 | same | ✓ |
| Parties | Frank Joseph Grayston – appellant; Her Majesty the Queen – respondent. Not Norton, Speyer, or MacPherson/Epstein/Lauwers JJ.A. | same (in header order) | ✓ |
| Issue | Whether the verdict was unreasonable or cannot be supported by the evidence (s. 686), from ¶2 | same, anchored ¶2 | ✓ |
| Sub-issues | none | two, restating the test's two stages (¶16) | ✗ T5 |
| Facts | ¶4, ¶5, ¶6 (+ the charges). ¶8 and ¶9 are procedure | ¶4, ¶5, ¶6, ¶7, and ¶8+¶9 merged as `event` | ✗ T10 |
| Ratio | the *D.D.T.* two-stage test, ¶15 or ¶16 | ¶16 test (rephrased), + ¶17, ¶19, ¶20 as `reasoning` | ✗ T3/T5 |
| Decision | stage 1: ¶17 ("not satisfied that the inference ... was a reasonable one"); stage 2: ¶20 first sentence; answer: ¶20 last sentence ("His conviction is therefore unreasonable") | ¶20 first + last sentence | ✗ T5 (no stage 1) |
| Excluded | disposition ¶21, the law review ¶12–¶15 | excluded from the brief | ✓ |
| Length | fits one page | 639 words | ✗ T14 |

---

## 3. Changes

`PROMPT_VERSION` becomes **`brief-v2`**. `get_cached` filters on the version, so `brief-v1` rows
stop being served and briefs regenerate when opened. No migration is needed (see D4).

### 3.1 Ratio and application (T3, T4, T5)

**Prompt, Ratio decidendi.** Replace the second and fourth bullets with:

- The ratio is the test or principle **this court chooses and applies to the facts** to reach its
  decision. When the court reviews what other cases held, those holdings are law, not ratio. The
  one the court then applies ("In this case ...", "Here ...") is the ratio. Take it in the court's
  words, either as the court states it generally or as the court restates it for this case, but
  don't blend the two.
- `reasoning` items say **why the rule is the right rule**: the words of the provision, its
  purpose, policy. They are stated in general terms. How the rule applies to this case's facts,
  and the court's rejection of the reasoning below, are not ratio. Their conclusions go in the
  decision.

**Prompt, Law.** Clarify `treatment`. Use `applied` only for an authority whose test the court applies
to the facts. An authority the court accepts as good law is `followed`, and background is
`referred`. The full reading marks the applied authority "test applied in this case" (T3's "which
line does our court go with").

### 3.2 Decision with stage answers (T5, T7, T8)

**Schema:** decision items gain `kind: step | conclusion` and `step` (a short label, e.g. "Stage 1:
reasonableness of the inference"; empty for a conclusion).

```text
decision  {status, items[] {issue_id, kind: step|conclusion, step, answer, anchor}}
```

**Prompt, Decision:**

- For each issue, give one `conclusion`: the court's statement that answers the issue, phrased so it
  reads as the answer to the issue's question.
- Where the ratio is a test with stages or elements, also give the court's answer on each one as a
  `step`, in order, before the conclusion.
- Take the concluding sentence, not the paragraph. Drop framing such as "In our view" and "I would
  therefore conclude that". Write in the third person: "the appeal judge erred", not "I am not
  persuaded". This also covers open-work item 4 in `case-brief-plan.md`.

**`enforce_decided_issues`** also drops the step decisions of a dropped issue.

### 3.3 Issues (T1, T2)

**Prompt, Legal issue(s).** Replace the first bullet and the sub-issue sentence with:

- The issue is the question **this court** must answer. On an appeal or judicial review, that is
  a question about the decision below, never the question the court below had to answer (such as
  identity, or whether the accused committed the offence).
  - Where the ground is a question of law the court decides for itself, such as the meaning of a
    provision, state that question directly.
  - Where the ground is itself a standard of review (an unreasonable verdict, a palpable and
    overriding error, the reasonableness of an administrative decision), state it that way.
- Take the issue from where the court identifies this case's question: the ground of appeal, or
  "the issue is whether". A question the court states while reviewing another case's test,
  typically followed by that case's citation, is law, not this case's issue.
- Sub-issues are narrower questions the parties dispute, such as which of two interpretations is
  right. The stages or elements of the court's test are ratio, not sub-issues. Their answers are
  `step` decisions.

The Kazemi sub-issue (sustained or momentary holding) still qualifies: it's a disputed choice
between two interpretations, not a stage of a test.

### 3.4 Facts: three kinds (T9, T10, T11)

**Schema:** `facts.kind` becomes `event | outcome_below | procedural_history`.

| Kind | Covers | In the brief? |
|------|--------|---------------|
| `event` | What happened, the charge or claim, what the parties admit | yes |
| `outcome_below` | The result of each decision under appeal or review, in one sentence each (convicted of X, acquitted of Y; the appeal judge allowed the appeal and dismissed the charge). The result only, not the reasoning | yes, after the events (per D1) |
| `procedural_history` | How the case was run: the arguments, the evidence called, who testified or didn't, the reasoning below, the grounds of appeal, hearing dates | no (full reading) |

**Prompt, Facts:**

- A party's argument is never a fact, and neither is whether a witness testified.
- Keep the details the court's analysis turns on, and drop incidental ones (the make of a car,
  street addresses, amounts) unless an issue turns on them.
- Facts go in chronological order.
- Replace "One item may be a whole paragraph's narrative" with "An item may combine one
  paragraph's related facts, trimmed."

### 3.5 Preliminary information (T12, T13)

**Prompt:**

- The date of decision is the release date (the "DATE:" or "Released:" line), never the "Heard:"
  date.
- Parties are the litigants only. Counsel ("for the respondent"), duty counsel and judges ("J.",
  "J.A.", "JJ.A.") are never parties. A party acting in person is still a party.

### 3.6 One-page budget (T14)

**Prompt, Style.** Replace the counts line with:

> The five-part brief must fit on one page: about 450 words or fewer across issues, facts, ratio and
> decision. Most decisions need 1–2 issues, 2–5 facts and 1–3 ratio items.

`verification` records the brief's word count (the five parts as the Markdown export renders them,
with no ¶ references). The UI shows it in the caption, e.g. "412 words · fits one page".

### 3.7 Grouping by issue (T7)

With **more than one issue**, `render_brief` and `to_markdown` put sub-labels under Ratio Decidendi
and under Decision ("Issue 1: whether ...", then that issue's items), using `issue_ids`. A ratio
item that bears on several issues is listed under the first one. With one issue, the layout stays
exactly as in the model answer. Step decisions render as bullets before the conclusion, in the
court's order.

### 3.8 Verification: new `rule_warnings`

All are heuristics: warnings, never `problems`.

| Check | Catches | Fires on the cached briefs? |
|-------|---------|-----------------------------|
| The five-part brief is over 500 words | T14 | Grayston (639) |
| A ratio `reasoning` item names a party or party role ("the appellant", "the accused", "the trial judge", "the appeal judge", a party's name) | application in the ratio (T5) | Grayston ¶17/¶19/¶20. Not Kazemi ¶15, which the model worded without naming anyone, so only the gold catches it |
| A decision item repeats a ratio item (≥ 0.9 overlap both ways) | open-work item 4 | — |
| An issue has no `conclusion`, or more than one | T5, T7 | replaces "issue N has no decision" |
| The rule reads as a staged test ("first ... second", "two-stage", "(1) ... (2)") but its issue has no `step` decisions | T5 | would fire on Grayston under v2 if steps were missing |
| A sub-issue largely restates a ratio item (overlap ≥ 0.6) | T5 (stages as sub-issues) | Grayston |
| An issue is anchored to a paragraph that ends in a case citation | T2 | — (a guard for the ¶13 trap) |
| A decision `conclusion` shares no content words with its issue's question | T7 | — |
| For user text: the preliminary date matches the header's "Heard:" line | T12 | — |
| For user text: a party name appears only in the counsel lines or the judges line, not in the BETWEEN block | T13 | — |

The date and party checks use the header block (text before ¶1 or [P1]). For corpus cases the
database already overrides the date.

### 3.9 Eval: Grayston gold and a tighter Kazemi gold

**New `eval/briefs/grayston.yaml`**, traced from the transcript. Like Kazemi, it must never be used
as a prompt example.

```yaml
citation: 2016 ONCA 784
preliminary:
  name_and_citation: R v Grayston, 2016 ONCA 784
  decision_date: October 26, 2016          # not the Heard date, October 3
  parties: [Her Majesty the Queen – respondent, Frank Joseph Grayston – appellant]
  not_parties: [Norton, Speyer, MacPherson, Epstein, Lauwers]
issues:
  count: 1
  anchors_any: [2]
  not_anchors: [13]                        # Charemski's question, not this case's
  include_terms: [unreasonable]
  max_sub_issues: 0                        # the test's stages are not sub-issues
facts:
  event_anchors: [4, 5, 6]
  brief_allowed: [1, 7, 10]                # charges; outcome below (as outcome_below)
  not_brief_anchors: [8, 9, 11, 18]        # arguments, not testifying, the trial judge's reasoning
ratio:
  anchors_any: [15, 16]                    # the D.D.T. two-stage test, either statement
  not_anchors: [12, 13, 14, 17, 20]        # other cases' tests; the application
decision:
  anchors: [17, 20]                        # stage 1; stage 2 and the answer
  conclusion_anchor: 20
  include_terms: [unreasonable]
  exclude_terms: [allowed, quashed, acquittal]
disposition: {anchors_any: [21]}
law: {terms: [Biniaris, Charemski, Mars, D.D.T., "686"]}
brief: {max_words: 500}
```

**Kazemi gold changes:**

- `ratio.not_anchors` gains **15**: the court rejecting the appeal judge's reading is application.
  The model answer leaves it out.
- The facts checks move to "in the brief" (`event` + `outcome_below`). ¶4–5 are allowed in the brief
  only as `outcome_below`, and ¶6–7 (grounds of appeal, costs request) must not be in the brief.
- `decision.conclusion_anchor: 16` and `brief.max_words: 500`.

**`scripts/eval_briefs.py`** gains these keys: `not_parties`, `issues.not_anchors`, `include_terms`,
`max_sub_issues`, `brief_allowed`/`not_brief_anchors`, `ratio.anchors_any`,
`decision.conclusion_anchor`/`include_terms`, and `brief.max_words`. It can score the cached
`brief-v1` rows with no model call, which gives the "before" numbers.

---

## 4. Not planned

- **An assignment-formatted Word export** (Times New Roman 11, one page, cover page). The
  transcript says the case-brief assignment must be done without AI (l. 417–420), so nothing here
  is shaped around producing that hand-in. The one-page limit is kept only as a concision target,
  which suits research briefs anyway.
- **Applying a brief to new facts** (l. 443–446: on the test, "how that precedent would or would
  not apply to our new facts"). This fits LawSearch's scenario search: compare a brief's ratio,
  element by element, with a scenario's facts. It's a separate feature, so if you want it, I'll log
  it in `IMPROVEMENTS.md` rather than fold it in here.

---

## 5. Build order

| Step | Scope | Done when |
|------|-------|-----------|
| 1 | `grayston.yaml`, Kazemi gold changes, `eval_briefs.py` keys; score the cached `brief-v1` briefs | Baseline recorded: Grayston fails the sub-issues, facts, ratio, decision and length checks; Kazemi fails ratio ¶15 |
| 2 | `brief-v2` schema (decision `kind`/`step`, facts `outcome_below`), prompt changes (§3.1–3.6), `enforce_decided_issues`, verification (§3.8), unit tests | Tests pass; every new warning has a firing and a non-firing test |
| 3 | `render_brief`, `to_markdown`, CLI: outcome-below facts, step decisions, grouping by issue, word count in the caption | Headless `AppTest` renders both golds and a synthetic two-issue brief |
| 4 | Opus runs: Kazemi, Grayston, then 2001 FCT 266 again (the unnumbered acceptance case) | Both golds pass every row, both briefs are ≤ 500 words, and 2001 FCT 266 has 0 problems |

Commit per step, only when you ask.

## 6. Decisions for you

1. **D1: The outcome below in the brief's facts.** The instructor says "the trial judgment matters"
   (l. 397), but the Kazemi model answer leaves out the trial and appeal results. **Recommended:**
   show `outcome_below` as the last fact, one sentence per decision below. The gold allows it but
   doesn't require it. The alternative is to keep it in the full reading only, as now.
2. **D2: Stage answers in the decision.** **Recommended:** yes, as in the instructor's Grayston
   answer. The cost is a longer decision for multi-stage tests.
3. **D3: The length budget is a warning only**, never truncation. **Recommended:** a 500-word
   warning, and about 450 words in the prompt.
4. **D4: Old `brief-v1` rows.** **Recommended:** leave them. They're never served once the version
   changes, and deleting them is a one-line migration whenever you like. The alternative is to
   delete them in a migration, as 011 did for `filac-v1`.

## 7. Risks

- **Two golds, both ONCA criminal or regulatory appeals.** The ratio/application line may look
  different in a tribunal decision or an SCC judgment. Joly (a motion to strike) is still the
  natural third gold, and it's waiting on the decision text.
- **Where the rule ends and its application begins is fuzzy.** ¶16 restates the test in terms of
  "the appellant" and is still ratio. That's why the party-name warning applies only to `reasoning`
  items, never to the `rule`.
- **Step answers against the length budget.** Extra decision bullets eat into the budget. Facts
  are where to cut: Grayston's facts are about 200 words now.
- **Concision against "the court's words".** Trimming pushes toward paraphrase, which lowers the
  overlap scores. `OVERLAP_MIN` (0.5) has only a lower bound, so it stays as it is. Re-check it on
  the Opus runs.
