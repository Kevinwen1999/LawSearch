# Improvements backlog

Known gaps that aren't fixed yet, with the evidence behind each, so they don't get lost between
phases. When an item is done, move it to **Done** with the date and evidence, rather than
deleting it.

Sources:
- **WD-eval**: `search_endpoint_eval_ontario_wrongful_dismissal.txt` (external frontend eval,
  2026-09-24); its scenario is `on-003`/`on-004` in `eval/scenarios.yaml`.
- **P6/P8**: follow-ups deferred when Phase 6 / Phase 8 shipped.
- **B-25**: found while working through this backlog, 2026-09-25.
- **CB**: case-brief rework (`case-brief-plan.md` §0, 2026-09-25).

Measurements below are from `scripts/eval_scenarios.py --ignore-gate` (36 scenarios, Qwen v4
fingerprints; `eval/runs/scenarios-backlog.json`) unless stated.

## Open

### Needs a person
- **Lawyer review of the eval gold.** `on-003`/`on-004` gold was written from memory by the
  WD-eval author and every scenario is `verified: false`; corpus citations resolve, but relevance
  isn't checked. A wrong gold authority punishes a retriever that was right. (WD-eval)

### Coverage limits (no fix planned; recorded so they aren't rediscovered)
- **ONSC decisions that don't cite the top cases stay invisible.** CanLII has no text search, so
  ONSC/tribunal detection only finds decisions citing the scenario's top corpus cases. The
  WD-eval's *Dufault* trial decision (2024 ONSC 1029) and *Wilson v. Solis* (2013 ONSC 5799) aren't
  found; *Bardal* is (via "frequently cited"). Not-in-corpus gold flagged: 4 of 8. (WD-eval)
- **35 of the 132 cited Ontario regulations aren't on e-Laws any more** (revoked or renumbered),
  so decisions citing them can't link to text. (B-25)
- **Ontario regulations with long e-Laws titles match by citation only**, e.g. the Statutory
  Accident Benefits Schedule, whose e-Laws title carries an effective date. Courts usually give
  the citation too. (B-25)
- **Name-and-year ONCA citations with no court marker stay unresolved** (e.g. "(2004), 246 DLR
  (4th) 43. There, this court found..."), by design: without the marker, "R. v. Smith (2004)"
  could be any court. *Hobbs* has 4 of its 5 citing decisions resolved. (B-25)

### Case briefs (CB)
- **More gold briefs.** Only *R v Kazemi* is scripted (`eval/briefs/`, 25/25 on Opus); one case can
  overfit the prompt. Add a tribunal, an SCC decision with a dissent, a French decision, and
  *Joly v Pelletier* (needs its text and a `text_file:` option in `eval_briefs.py`).
- **Browser check of the Case brief page**: only headless `AppTest` so far.
- **Quality notes not yet checked** (2001 FCT 266): a decision repeating a ratio sentence, a
  decision in the judge's first person, facts out of chronological order.

### Small
- **Tag gold `issues` on future multi-issue scenarios**, so per-issue coverage is measured beyond
  `on-003`/`on-004`. (WD-eval)

## Done

- **2026-09-25: citations to pre-2007 ONCA decisions resolved** (WD-eval). All 6,201 docket-cited
  ONCA decisions had `cited_by_count = 0`. `load_citations` now resolves name-and-year citations
  with an Ontario Court of Appeal marker; 1,584 now have citations (6,507 edges), *Hobbs* 0 → 4.
  30-citation hand sample all correct.
- **2026-09-25: frequently cited authorities, including ones not in the corpus** (WD-eval, the
  eval's Fix 1 + 3). `POST /canlii/cited` lists authorities cited by 2+ top cases that the results
  don't show, labelled in LawSearch or not. *Bardal* is listed for on-002/003/004; *Rasaratnam* and
  *Thirunavukkarasu* (pre-2001 FCA, not in the corpus) for imm-001. Not-in-corpus gold flagged:
  0 of 8 → 4 of 8.
- **2026-09-25: Ontario regulations** (WD-eval, the eval's Fix 4). Flag: `/scenarios` returns
  `uncovered_regulations` (cited by 2+ result cases, not loaded) with e-Laws links. Load:
  `scripts/ingest_ontario_regulations.py` brought in the 97 current regulations the corpus cites
  2+ times (12,015 section chunks), incl. O. Reg. 288/01 and the Rules of Civil Procedure; linked
  by citation, 3+-word title and rule number (r. 21.01: 251 decisions, r. 20.04: 58).
- **2026-09-25: fingerprint v4** (WD-eval). Remedies and defences (mitigation, limitation periods)
  become their own issues; up to 10 issues.
- **2026-09-25: CanLII seeds per issue, candidates labelled by issue** (WD-eval). The UI picks
  seeds round-robin across the issue groups; duplicate seeds take one slot. The issue gate was
  loosened from 0.0 to -2.0 (tune nDCG@10 0.410 → 0.420 with issue coverage kept; test 0.370 →
  0.383), so authorities phrased unlike the scenario (*Meiorin*, -1.75) can take a slot.
- **2026-09-25: legislation** (WD-eval, the eval's Fix 5). Sections no decision cites are dropped
  unless the scenario names their law (legislation recall unchanged; uncited sections shown on
  test 1.36 → 0.73). Each issue now brings its own top 2 sections, listed under the issue in the
  UI. "Often cited together" siblings were tried and not built: co-citation is too sparse for
  Ontario sections (ESA s. 65: 5 citing decisions) and the query took 12 s with the Charter.
- **2026-09-25: clarification gate** (B-24). Blocks only when jurisdiction is `unknown`; other
  clarifications come back as a note. Sonnet v3 stopped 12 of 36 eval scenarios, Qwen v4 stops 5,
  each a private-law question with no province.
- **2026-09-25: "current to" label** (WD-eval, Fix 6). Ontario Acts and regulations show "version
  of" their version date; the caption explains.
- **2026-09-25: *Waksdale* on its own issue** (WD-eval). Root cause: the reranker only sees the
  retrieved passages, which were its facts (0.9) while its ¶3 scores 2.65. Reranking short
  decisions whole fixed that case but lowered test nDCG@10 0.370 → 0.343; not adopted. With v4
  fingerprints *Waksdale* ranks #3 on its issue.
- **2026-09-25: eval hygiene** (WD-eval). Fingerprints regenerated with the production Qwen model
  (LM Studio needed `reasoning_effort`, see below); `--ignore-gate` keeps coverage independent of
  the gate; `--canlii` scores whether not-in-corpus gold gets flagged.
- **2026-09-25: with/without-fingerprint comparison** (P6). On the same 36 scenarios raw-text
  search beat the fingerprinted pipeline on test nDCG@10 (0.477 vs 0.383). The scenario text is
  now searched too and its top 7 lead the best-overall list: nDCG@10 tune 0.420 → 0.427, test
  0.383 → 0.483 (above raw text alone, from ~22 cases shown instead of 30); legislation recall tune
  0.624 → 0.675, test 0.742 → 0.803. Leading with 3 or 10 of them did worse on both splits.
- **2026-09-25: "para. N" after a section** (B-25). "O. Reg. 288/01, s. 2(1), para. 3" linked s. 2
  and s. 3; only section-level words now continue a list, and a paragraph/clause right after a
  reference is read as part of it. Statute links rebuilt.
- **2026-09-25: CanLII floor re-checked** (P8) on on-002/003/004 with per-issue seeds: everything
  above -4.0 was on point or close. Kept.
- **2026-09-25: CanLII day boundary** (P8). The cap is enforced over any rolling 24 hours in
  hourly buckets (`canlii_usage_hourly`), which holds whatever CanLII's day is.
- **2026-09-25: fingerprint cache table** (P6). `fingerprint_cache`, keyed by text hash, prompt
  version and configured model.
- **2026-09-25: DOCX/scanned-PDF fixtures, and an OCR fix they exposed** (P6).
  `scripts/eval_intake.py` renders every scenario as DOCX and image-only PDF. Scanned PDFs read
  back at 0.25 word agreement: RapidOCR's bundled recognizer dropped all spaces and its direction
  classifier flipped lines. English recognizer, classifier off: 0.998 (DOCX 1.000).
- **2026-09-25: LM Studio reasoning budget** (B-25). Qwen ignored no effort hint and reasoned
  until the 8,000-token budget ran out, so every fingerprint had silently fallen back to Sonnet;
  `reasoning_effort` is now passed (~35 s per fingerprint).
- **2026-09-24: issue-grouped scenario results** (WD-eval, Fix 2), `5c767cb`. The merge had
  interleaved 9 lists into 10 slots; `/scenarios` returns the best `k` overall plus each issue's
  best `issue_k`, labelled by issue.
