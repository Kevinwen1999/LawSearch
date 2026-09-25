# Improvements backlog

Known gaps that aren't fixed yet, with the evidence behind each, so they don't get lost between
phases. Newest source first within each priority. When an item is done, move it to **Done** with
the commit, rather than deleting it.

Sources:
- **WD-eval**: `search_endpoint_eval_ontario_wrongful_dismissal.txt` (external frontend eval,
  2026-09-24), reproduced and diagnosed the same day; its scenario is `on-003`/`on-004` in
  `eval/scenarios.yaml`.
- **P6/P8**: follow-ups deferred when Phase 6 / Phase 8 shipped.

## High priority

### Resolve citations to pre-neutral-citation ONCA decisions (WD-eval)
6,201 of 24,121 ONCA decisions (1998 to ~2006) have only a docket number as their citation, and
**every one has `cited_by_count = 0`**: later decisions cite them by name, year and report
(`Hobbs v. TDI Canada Ltd. (2004), 246 D.L.R. (4th) 43 (Ont. C.A.)`), which nothing resolves.
So graph expansion and the citation prior never see them. *Hobbs* (the fresh-consideration
authority, gold for I2) ranked #121 on its own issue query.
- Fix: in `scripts/load_citations.py`, extract `<style of cause> (<year>), <report> (Ont. C.A.)`
  citations from decision text and resolve on normalized style of cause + decision year
  against ONCA rows without a neutral citation (the same pattern as the SCR extraction).
  Check a hand sample for false matches; common names (`R. v. Smith`) need the year and the
  report page, or should be skipped.
- Alternative for stragglers: CanLII case metadata carries `docketNumber`, so a CanLII id
  (`2004canlii…`) can be mapped to our docket-numbered row (costs one query per case).

### Flag frequently cited authorities that aren't in the corpus (WD-eval, eval's Fix 1 + 3)
*Bardal v. Globe & Mail* (1960, Ont. H.C., the reasonable-notice factors case) is named in 66
corpus decisions, including 4 of the scenario's top 7, but has no row, so it disappears
silently. Same for other ONSC/older authorities.
- Fix: CanLII `caseCitator/.../citedCases` for the scenario's seeds (~8 queries, cached 7 days),
  aggregate authorities cited by ≥ 2 top cases; those not in the corpus go in a "Frequently
  cited, not in LawSearch" list with a CanLII link (Bardal is `onsc/1960canlii294`). Those in the
  corpus but not retrieved could be boosted or listed too.
- Note: the eval's claim that *Bertsch* cites *Waksdale* is wrong (Bertsch is a 7k-char
  endorsement); only *Dufault* does among the top results.

## Medium priority

### Flag cited Ontario regulations that aren't covered (WD-eval, eval's Fix 4)
O. Reg. 288/01 (ESA termination and severance; "wilful misconduct") decides the for-cause issue,
and 2 of the top 7 cases cite it, but regulations aren't ingested and the UI only has a generic
caption.
- Fix: regex `O\. ?Reg\. ?\d+/\d+` over the top cases' text; list each regulation cited by ≥ 2
  of them as "cited, not covered" with an e-Laws link (`ontario.ca/laws/regulation/r01288`).
- Longer term: ingest Ontario regulations from e-Laws (A2AJ `canadian-laws` has Acts only).

### Fingerprint: remedies and defences as issues; more issue slots (WD-eval)
The fingerprint turned mitigation (30 job applications) into a search term and key fact, not
an issue, so no per-issue query ran for it; *Evans* (mitigation) only reached #18 via the
bad-faith issue. `MAX_ISSUE_QUERIES = 8` was exactly full for this scenario.
- Fix: prompt the fingerprint to state each remedy/defence the facts raise (mitigation,
  limitation periods, damages heads) as its own issue; raise `MAX_ISSUE_QUERIES` to 10
  (each extra issue ≈ 1–2 s of search). Bumps `fingerprint.PROMPT_VERSION`, which invalidates
  `eval/fingerprints/`.

### CanLII detection seeded per issue (WD-eval)
CanLII candidates are seeded from the top of the flat case list, which a dominant issue fills
(termination-clause cases here). The human-rights issue gets no seeds, so no Human Rights
Tribunal of Ontario decisions come back, and the SCC human-rights cases rank low on this
wording (*Meiorin* #6 on its issue, reranker −1.75, below the issue gate; *Moore* and
*Hydro-Québec* ~#35–39).
- Fix: build seeds from each issue group's top 1–2 cases (still ≤ 8–10 seeds per scenario),
  and label candidates with the issue whose seed found them. Group results are now returned by
  `/scenarios` (`results.groups`), so this is a UI + request change.
- Consider whether the issue gate (`MIN_ISSUE_CASE_RERANK = 0`) is too strict for issues where
  the corpus's best authorities are phrased very differently from the scenario (human rights).

### Legislation ranking noise and per-issue sections (WD-eval, eval's Fix 5)
- Sections with 0 citations get in on lexical/vector match alone (ESA s. 141, s. 74.11, s. 49
  across two runs; s. 74.11 is about temporary help agencies). Option: require a citing top case
  or a strong reranker score for 0-citation sections. The last attempt to rank sections by the
  reranker lowered recall, so measure on the eval set.
- Sibling sections that travel together aren't pulled in (ESA 64 with 65, 60 with 61; HRC
  10/11/17 with 5).
- Sections still share one list of 8 across all issues (the same structural cap cases had
  before grouping): group sections by issue like cases.
- Section recall on `on-003`: see the latest `eval/runs/scenarios-*.json`.

### A third of the eval set is stopped by the clarification gate
With the Sonnet-fallback fingerprints, 12 of 36 eval scenarios came back `needs_clarification`
(admin-001/002, neg-001/002, contract-001/002, employ-001, crim-001/002, lc-sst-01/02,
lc-fpslreb-01), so `/scenarios` wouldn't search them at all. Several are plainly federal
(SST, FPSLREB, criminal law) or general common-law questions where the province doesn't
change the answer. The earlier fingerprinted merge eval scored 33 scenarios, so this may be
Sonnet-vs-Qwen behaviour.
- Check with LM Studio running; if the local model also over-asks, tighten the prompt's rule
  for when jurisdiction "matters" (federal subject matter or SCC-level common law shouldn't
  ask).
- `scripts/eval_scenarios.py` could score gated scenarios anyway, to keep the eval's coverage
  independent of the gate.

## Low priority

### "Current to" label on Ontario Acts (WD-eval, eval's Fix 6)
Not stale data: A2AJ gives one `document_date` per Ontario Act, which is that Act's version
date (Human Rights Code 2025-07-01, ESA 2026-01-01). The UI's "current to" wording implies a
consolidation check date. Relabel for Ontario Acts ("version of …") and show the A2AJ snapshot
date separately.

### Waksdale scores low on its own issue query (WD-eval)
*Waksdale* was #15 on the termination-clause issue query with reranker 0.89 (vs *Dufault* 6.46),
despite 7 citing seeds. It's a short decision; check which passage the reranker sees
(`RERANK_PASSAGES = 2`) and whether short decisions need a different passage choice.

### Eval set hygiene
- `eval/fingerprints/` was generated by the Sonnet fallback (LM Studio was off, 2026-09-24),
  not the default local Qwen model; regenerate with LM Studio running for results that match
  production.
- `on-003`/`on-004` gold was written from memory by the eval's author; corpus citations resolve
  and the three CanLII-only ones exist, but relevance isn't lawyer-verified (`verified: false`).
- Only `on-003`/`on-004` tag gold authorities with `issues`, so per-issue coverage is measured
  on those two only. Tag other multi-issue scenarios as they're added.
- The eval can't score "flagged as not in corpus" yet (Bardal, Wilson v. Solis, O. Reg.
  288/01); add that once the flags above exist.

### Carried over
- (P8) CanLII rerank floor (−4.0) calibrated on only three Ontario scenarios.
- (P8) CanLII usage counts per UTC day; CanLII's own day boundary is unknown (the 4,500 cap
  leaves headroom).
- (P6) Persistent fingerprint cache table in Postgres; a with/without-fingerprint eval
  comparison (now possible with `scripts/eval_scenarios.py`); DOCX/image-PDF fixtures in the
  eval set.

## Done
- 2026-09-24: issue-grouped scenario results (WD-eval, the eval's Fix 2). Per-issue search
  already existed, but the merge interleaved 9 lists into 10 slots, so each issue got about one
  case. `/scenarios` now returns the same best `k` overall (default 10) plus each issue's best
  `issue_k` (default 3), labelled by issue in the UI. Measured with the new
  `scripts/eval_scenarios.py` (`eval/runs/scenarios-grouping.json`, 24 scoreable scenarios):
  top 10 unchanged (nDCG@10 tune 0.387, test 0.400); recall@25 tune 0.491 → 0.531, test
  0.583 → 0.608; issue coverage on on-003/on-004 0.42 → 0.58. Leading with the combined query's
  top 8 instead was tried and rejected (test nDCG@10 0.400 → 0.340).
