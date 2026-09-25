# Stack Decisions & Environment Readiness

Companion to [implementation-plan.md](implementation-plan.md). Resolves the plan's open "or"
choices into concrete picks, based on checking actual availability of every external dependency
and the local machine. Dated 2026-09-12.

---

## 1. Local machine — ready/not ready

| Item | Status | Action |
|---|---|---|
| Docker 27.5 + Compose v2.32 | ✅ present | none |
| Git 2.43 | ✅ present | none |
| GPU | ✅ NVIDIA, CUDA 13.2 | changes the embeddings/reranker decision below — run locally, not via paid API |
| Python | ⚠️ 3.10, 3.12 (Windows Store), 3.13 all installed, no clean 3.12 | install Python 3.12 from python.org (not the Store build — it sandboxes filesystem access and complicates venvs); create the project venv against it explicitly |
| `psql` CLI | ❌ not on PATH | not needed — Postgres runs in the `pgvector` Docker container; exec in (`docker exec -it ... psql`) or use a GUI client (DBeaver/pgAdmin) if preferred |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `HF_TOKEN` | ❌ none set | must be provisioned before Phase 3 (LLM calls) — see §3 |
| `CANLII_API_KEY` | ✅ provisioned; live call verified 2026-09-24 | usage plan confirmed — see §2 (CanLII API). Unblocks Phase 8 |

---

## 2. External data sources — verified

### A2AJ (case corpus) — ✅ fully open, no registration
- Real project: `a2aj-ca/canadian-legal-data` on GitHub, `a2aj/canadian-case-law` +
  `a2aj/canadian-laws` on HuggingFace, hosted by Osgoode Hall Law School / TMU Lincoln Alexander
  School of Law. Also exposes a REST API (`api.a2aj.ca`) and an MCP server.
- Confirmed schema fields: `cases_cited` / `cases_citing` / `citing_cases_count` exist — Phase 4's
  graph-expansion step is buildable as designed.
- Confirmed coverage: SCC (10.9k), FCA (7.8k), FC (35.9k), TCC (8.1k), ONCA (24.1k), plus ~25
  tribunals (RAD/RPD, SST, CHRT, CIRB, CITT, etc.) — **~226k cases total**, 1877–2026.
- **Confirmed gap: ONSC (Ontario Superior Court) is NOT in the corpus.** The plan's Phase 8
  workaround (CanLII detection + user-upload bridge) is the right call, not a hedge against an
  uncertain gap — it's a real, confirmed one.
- **2026-09-14, re-confirmed for Phase 7:** `ONCA/train.parquet` has 24,121 rows, dated
  1998-06-08 to 2026-09-11 — the real coverage gap is pre-1998, not "pre-1990" as the original
  plan assumed. `scripts/ingest_a2aj.py` already takes any court code as a CLI arg, so loading it
  needs no new ingestion code, just `python -m scripts.ingest_a2aj ONCA`.
- ⚠️ **License wrinkle not in the original plan:** each document carries its own
  `upstream_license`, and the maintainers state some upstream sources "include limits on
  commercial use." If this tool is ever commercial (not just research/internal), audit
  `upstream_license` per source before shipping — don't assume blanket reuse rights.

### Justice Laws (federal legislation) — ✅ confirmed
- Bulk XML (and a "WebXML" variant) published on the Open Government Portal, refreshed every two
  weeks, pulled via FTP (`ftp://205.193.86.89/PITXML/`). Crown copyright, openly reusable — matches
  the plan's Phase 5 ingestion design directly. No registration.

### Ontario legislation — ✅ resolved 2026-09-14, no e-Laws scraper needed
- Originally flagged as the plan's one unresolved unknown (e-laws.gov.on.ca / ontario.ca/laws is
  JS-rendered, no confirmed bulk dump). **Resolved by using `a2aj/canadian-laws` instead** (a
  sibling HuggingFace dataset to the case-law one already in use, tentatively noted as a
  possibility on 2026-09-13 in §5 below and confirmed by direct inspection on 2026-09-14).
- Confirmed by downloading and reading `LEGISLATION-ON/train.parquet`: **856 Ontario Acts**,
  section-grained (`unofficial_sections_en`/`_fr` — a JSON dict keyed by section number), bilingual
  columns matching the case-law dataset's shape, `source_url_en` pointing at the official
  `ontario.ca/laws/api/v2/legislation/...` endpoint, `upstream_license` citing King's Printer for
  Ontario (permits free reproduction, unofficial-copy labelling required — the reuse-terms
  question is resolved favorably and now has a concrete source to cite it against).
- No per-section in-force dates in this dataset (unlike Justice Laws XML) — only one
  `document_date` per Act. Phase 5's "wording came into force after this decision" FILAC flag
  (§5.6) won't extend to Ontario sections without extra work; out of scope for Phase 7's DoD.
- Ingestion is **simpler** than Phase 5's Justice Laws XML pipeline: one parquet read + a JSON
  dict per Act, no XML hierarchy parsing. `legislation`/`legislation_sections` already has a
  `jurisdiction` column (from the Phase 0 schema) so no migration is needed, and
  `app/statute_refs.py`'s title index queries `legislation` with no jurisdiction filter — case→
  statute linking for ONCA decisions works the moment the titles are loaded, no new code.

### CanLII API — ✅ key provisioned, usage plan confirmed (2026-09-24)
- Real, read-only REST API. Endpoints: case browse (by court/database), case metadata, citator
  (cited-by / cites, both directions), legislation browse/metadata. Matches the plan's "detection +
  link-out" use exactly — no full-text redistribution permitted, consistent with never caching
  full text (§5.4 of the plan).
- Access required a manual application through CanLII's feedback form. The key is now in `.env`;
  a live `caseBrowse` call returned HTTP 200 with 409 case databases, including every Ontario one
  Phase 8 targets (`onsc`, `onscdc`, `oncj`, `onltb`, `onhrt`, `onwsiat`, `onlat`, …).
- **Usage plan, confirmed from CanLII's key agreement (2026-09-24):**
  - Access is to **metadata only**. Document text, and text-based searches within documents, are
    not available through the API.
  - **5,000 queries per day; 2 requests per second; 1 request at a time.**
  - CanLII states that requests to raise these limits or to allow direct access to content won't
    be granted and might be ignored, so design within them; don't plan on an increase.
- Responses carry no rate-limit headers, so the client has to enforce the limits itself rather
  than react to server signals.
- Consequence for Phase 8: with no text search, the API can't find ONSC decisions by topic.
  Detection has to go through the citator (decisions that cite the corpus cases our own retrieval
  already ranked highly), plus metadata (title, keywords) for ranking.

### Citation parsing — ⚠️ don't depend on `legal-citation-parser`
- It exists on PyPI and does what §5.5 describes (extracts/normalizes citations, can hit the CanLII
  API for metadata) — but it hasn't been released in over a year and reads as low-maintenance.
  Fine to prototype against, but plan on the custom regex + normalization path (as the plan already
  specifies) as the real implementation, not a fallback.

### ML tooling — ✅ all standard, actively maintained
`sentence-transformers` (embeddings + `CrossEncoder` reranking), `pgvector`, `lxml`, `pymupdf`,
`python-docx`, FastAPI/uvicorn — no availability concerns.
- ⚠️ `ocrmypdf` is not pip-only: it wraps Tesseract OCR + Ghostscript, which are **separate Windows
  installs** (not resolved by `pip install`). Budget a setup step for this in Phase 6, and note
  Ghostscript 10.5.1 specifically (later 2026 builds had a reported regression).

---

## 3. Decisions on the plan's open choices

| Concern | Plan said | Decision | Why |
|---|---|---|---|
| Python | 3.12 | **3.12**, installed fresh from python.org | avoid the Windows Store 3.12 (sandboxed FS) and the ambient 3.13 default (newer, more likely to hit a wheel that isn't built yet for ML deps) |
| Embeddings | "hosted API or sentence-transformers" | **Local, via `sentence-transformers` on the GPU** (e.g. `BAAI/bge-m3` — multilingual EN/FR, matches A2AJ's bilingual text) | you have an NVIDIA GPU and no embedding API key provisioned; embedding 191k cases × several chunks each through a paid API is real, avoidable cost and a new key to manage. Revisit only if local recall underperforms in Phase 4 eval. |
| Reranker | "cross-encoder or LLM-as-reranker" | **Local cross-encoder**, `BAAI/bge-reranker-v2-m3` (multilingual) via `sentence_transformers.CrossEncoder` | same GPU-first logic; LLM-as-reranker stays a fallback if cross-encoder quality is insufficient on the eval set |
| LLM (fingerprinting, FILAC) | "hosted model, structured/JSON output" | **Anthropic Claude API** (separate `ANTHROPIC_API_KEY` from console.anthropic.com, not the Claude Code session's own auth), structured output via tool-use/JSON schema | needs deliberate provisioning — flagging so it doesn't get forgotten before Phase 3 |
| Jobs/queue | "plain scripts... or RQ/Celery+Redis" | **Plain scripts + a `jobs` table** for now; add Redis to the compose file only when a real concurrency need shows up | matches the plan's own bias to keep infra minimal, and ingestion is batch not real-time |

---

## 4. Before Phase 0 — setup checklist

1. Install Python 3.12 from python.org; create the project venv against it.
2. `docker compose` scaffold: Postgres 16 + `pgvector`, MinIO (Redis deferred per §3).
3. Apply for the CanLII API key today (feedback form) — it's the one external dependency with a
   human-review lag; everything else is instant/self-serve. Only Phase 8 needs it now.
4. Provision an `ANTHROPIC_API_KEY` (billing-enabled, separate from Claude Code's own login).
5. Confirm GPU is visible inside whatever runs the embedding/rerank code (Docker GPU passthrough on
   Windows needs WSL2 backend + NVIDIA Container Toolkit if those steps run in a container; simplest
   path is running the embedding/ingestion workers directly on the host venv instead of in Docker).
6. ~~Manually check e-laws.gov.on.ca for a bulk/XML access path before Phase 7 starts~~ — resolved
   2026-09-14, see §2: use `a2aj/canadian-laws` instead, no e-Laws scraper needed.

Everything else in the original implementation plan checks out against real, currently-available
sources and packages.

---

## 5. Decisions revised during the build

| Date | Was | Now | Why |
|---|---|---|---|
| 2026-09-13 | `vector(1024)` | `halfvec(1024)` | Halves vector + HNSW storage for the 3.43M-chunk federal corpus; no measurable ranking change in the smoke test |
| 2026-09-13 | MinIO from Docker Hub | `quay.io/minio/minio` | Docker Hub `minio/minio` is no longer published |
| 2026-09-13 | Postgres 16, `tsvector` + `ts_rank` for lexical search | **Postgres 17 + pg_textsearch 1.4 (BM25)** | Measured on the loaded corpus: a prose query matched 1.7M chunks and took 51 s to count, and `ts_rank` has no IDF. pg_textsearch gives true BM25 top-k (block-max WAND) in 8–50 ms. Chosen over ParadeDB `pg_search` (AGPL-3.0) for its PostgreSQL license; it requires PG 17+, hence the upgrade (dump/restore, verified row-for-row). Still one Postgres, as the plan intended |
| 2026-09-13 | Unique `neutral_citation` | Unique `(court, citation)` | Tribunal file numbers are shared across RPD and RAD decisions |
| 2026-09-13 | Hosted LLM via API key only | **FILAC backend switch: `claude-cli` (Claude Code headless, subscription) for testing, `api` (Anthropic SDK) for real use** | No API key yet and test volume is low; both backends share prompt, JSON schema, verification and cache, so switching is one setting. The API backend is unit-tested against a fake client but not yet live |
| 2026-09-13 | Long judgments: extract per section, then reconcile (plan §5.3) | Whole decision in one call | Claude Opus 5's 1M-token context fits the longest judgment in the corpus (1.2M chars); no reconcile step needed |
| 2026-09-13 | Graph expansion from A2AJ `cases_cited` only | A2AJ lists + French court codes mapped to English + SCR citations regex-extracted from text | Raw A2AJ lists resolved only 50% (French duplicates) and omit pre-2000 SCC; after mapping and extraction 535,778 edges, 89% of A2AJ citations resolve |
| 2026-09-13 | Rerank against the scenario's fingerprinted issues (plan §4 Phase 4) | Rerank against the raw scenario text, 1,024-token input | Fingerprinting is Phase 6; at 512 tokens long scenarios squeezed out the passage and rerank hurt |
| 2026-09-13 | Signal weighting incl. subsequent treatment | Court level + in-corpus citation count only | No treatment data in A2AJ; classifying 535k citation contexts with an LLM is out of scope for now |
| 2026-09-13 | A2AJ ONCA/e-Laws as the only Ontario statute path | A2AJ `canadian-laws` also carries Ontario statutes + regulations | May replace the bespoke e-Laws scraper in Phase 7 |
| 2026-09-14 | Justice Laws bulk XML via FTP / Open Government Portal | `justicecanada/laws-lois-xml` GitHub repo (English, shallow sparse clone) + the Constitution Acts HTML page | Same official XML, versioned by commit so a load records exactly which consolidation it used. The Constitution Acts (incl. the Charter) are not in the XML set, so they get a small HTML parser |
| 2026-09-14 | Point-in-time legislation (plan §5 Phase 5) | Current consolidation only; each section stores its in-force start date | The repo holds the current consolidation; historical versions would multiply the corpus. FILAC flags a statute whose current wording came into force after the decision, so a stale link is visible rather than silent |
| 2026-09-14 | Statute chunks per section | Section-grained, packed at subsection boundaries up to 1,500 chars; results grouped back to one hit per section | Long sections (ITA s. 152: 37 chunks) would otherwise be one diluted embedding, or flood results with siblings |
| 2026-09-14 | Case→statute links from A2AJ metadata or an LLM | Regex/title-index extractor over decision text (`app/statute_refs.py`): full titles, common acronyms, "(the Act)" definitions, SC/RSC/SOR citations | A2AJ has no statute citations; a deterministic pass runs over all 116,847 decisions in ~13 min. Hand-checked sample: 37/40 correct before tightening the alias rules |
| 2026-09-14 | Statute gold hand-written only | Hand-written for SCC scenarios; for drafted scenarios, the sections their source decision cites most (2+ mentions) | Gives section recall a baseline without a lawyer's review; marked `source: extracted-from-decision` in the YAML |
| 2026-09-14 | "A2AJ `canadian-laws` may replace the e-Laws scraper" (tentative, row above) | **Confirmed**: `LEGISLATION-ON/train.parquet` has 856 Ontario Acts, section-grained JSON, King's Printer for Ontario license — e-Laws scraper dropped from the plan entirely | Downloaded and read the actual parquet schema/rows rather than relying on the dataset's existence alone; simpler to ingest than Justice Laws XML was |
| 2026-09-14 | Plan said "mind the pre-1990 gap" for ONCA | Confirmed real gap is pre-1998: `ONCA/train.parquet` is dated 1998-06-08 to 2026-09-11 (24,121 rows) | Read the actual date range instead of assuming; also confirmed `scripts/ingest_a2aj.py` needs no changes since it already takes any court code as an argument |
| 2026-09-14 | Single "Phase 7 — Ontario + gap handling" bundling ONCA/Ontario-legislation/CanLII/upload-FILAC | Split into **Phase 7** (ONCA + Ontario legislation + upload-driven FILAC — no CanLII needed) and **Phase 8** (CanLII-detected ONSC/tribunal candidates — blocked on `CANLII_API_KEY`, still empty); old Phase 8 (Hardening) renumbered to Phase 9 | `CANLII_API_KEY` still isn't provisioned and the application has a human-review lag; everything else in the old Phase 7 turned out not to depend on it, so splitting means real progress isn't blocked on an external approval |
| 2026-09-14 | Assumed Ontario statute citations in ONCA text would need new extraction logic | Confirmed `app/statute_refs.py`'s `StatuteIndex.from_db` already queries `SELECT title, code, kind FROM legislation` with no jurisdiction filter | Phase 5's case→statute linking generalizes to any jurisdiction for free once the titles are loaded; only the in-process index needs a restart to pick them up |
| 2026-09-14 | Phase 7 executed: ONCA (24,121 cases via `scripts/ingest_a2aj.py ONCA`, jurisdiction hardcoded 'federal' generalized to a `JURISDICTION` map) + Ontario legislation (856 Acts/50,260 sections via new `scripts/ingest_ontario_legislation.py`) + `scripts/link_statutes.py` re-run | 302,840 total case→section edges (was ~289k federal-only), 14,294 resolve to Ontario sections, including real ONCA→*Limitations Act, 2002* s.7 matches | Confirms the title-index linking genuinely generalizes, not just in theory |
| 2026-09-14 | (new finding, not in original plan) | `StatuteIndex.from_db()`'s title→code lookup is a plain dict — **8 titles are identical between federal and Ontario legislation** (`Income Tax Act`, `Financial Administration Act`, `Auditor General Act`, `Bridges Act`, `Forestry Act`, `Pay Equity Act`, `Public Officers Act`, `Statistics Act`); for those 8, whichever jurisdiction loads last silently shadows the other in citation matching | **Fixed the same day** (not deferred to Phase 9): a colliding title now resolves by its citation tail (R.S.O./S.O. vs R.S.C./S.C.), defaulting to federal; `link_statutes.py` re-run → 322,471 edges, unresolved refs down 49% |
| 2026-09-14 | (new finding) | 1,315 case→Ontario-statute edges come from pre-1990 decisions — not false jurisdiction matches (SCC legitimately hears Ontario appeals and cites Ontario statutes), but the already-documented "no per-section in-force dates" limitation (§Ontario legislation above) means these resolve to today's wording, not the era's | Confirms the accepted Phase 7 scope limitation is real and now has a concrete count, not just a theoretical caveat |
| 2026-09-14 | `scripts/load_citations.py` scanned only `FEDERAL_COURTS` for citing decisions | Scans every loaded court (`JURISDICTION`, so ONCA too) | Found by an external retrieval review ("Waksdale cited by 0"): no ONCA decision's `cases_cited` was ever read, so ONCA cases had no in-corpus citations and graph expansion from ONCA seeds found nothing. Re-run: +41,113 edges (597,582 total); Waksdale 0→7, Strudwick 8, Matthews 13. Phase-4 eval, default pipeline nDCG@10: tune 0.383→0.403, test 0.487→0.499, no group down |
| 2026-09-14 | Section search over one federal+Ontario pool; `/scenarios` ran one combined query | `/scenarios` (only — `/search` unchanged): sections scoped to the fingerprint's jurisdiction + Constitution + statutes it names; Ontario matters' cases scoped to ONCA+SCC unless the user picks courts; combined query plus one query per fingerprint issue (≤8), merged with the combined query's top 3 first, then round-robin | Same review: an Ontario employment scenario surfaced the Canada Labour Code, and its Human Rights Code branch vanished behind termination-clause case law. Eval on the 33 scoreable scenarios, fingerprinted (all single-issue federal, so a regression guard): recall@10 0.543→0.591, nDCG@10 0.414→0.435, section recall 0.725→0.850; tune split case nDCG@10 0.500→0.494 (inside the 0.01 near-tie rule), test 0.322→0.372. Issue queries only contribute cases with a cross-encoder score > 0, and sections > −5 from a law the combined query, a named statute or top cases' citations already support (without that, unrelated Acts' "damages" sections got in) |
| 2026-09-14 | (tried) Cross-encoder reranking of statute sections | Not used for ranking; scores only gate per-issue section results | Lowered section recall@5 on the tune split at every weight tried (0.574 → 0.463–0.537); long scenario queries likely crowd the passage out of the input |
| 2026-09-14 | "A2AJ `canadian-laws` also carries Ontario statutes + regulations" (row above) | Acts only — no Ontario regulations in the dataset | O. Reg. 288/01 (ESA termination/severance) and every other Ontario regulation are a coverage gap that needs another source (e-Laws), not a ranking fix; the legislation caption in the UI now says so |
| 2026-09-24 | CanLII limits "2 req/s, ≤5000/day" provisional (§2) | **Confirmed** by CanLII's key agreement: 5,000 queries/day, 2 req/s, 1 concurrent, metadata only, no increases granted | Phase 8's limiter and daily budget are sized against these as hard ceilings. No text search via the API, so ONSC detection runs through the citator from our own top-ranked cases |
| 2026-09-24 | CanLII "case browse + citator" to detect ONSC candidates (plan Phase 8) | Citator only, seeded by the scenario's top corpus cases; pooled by co-citation (`1/sqrt(candidates per seed)`, gentle rank decay), top 12 get a metadata call, then the cross-encoder scores the fingerprint query against CanLII's title/topics/keywords with a −4.0 floor | The API has no text search, and case browse only lists by date, so browse can't find by topic. Calibrated on the three Ontario eval scenarios: raw scenario text as the rerank query left on-point decisions at −5 to −6.5, mixed with noise; the fingerprint query lifted them to −4.2 and above and pushed noise (e.g. a Charter-evidence ruling citing a slip-and-fall appeal) below. Pre-neutral-citation SCC/ONCA seeds (`[1951] SCR 470`, dockets) can't be mapped to CanLII ids and are skipped |
| 2026-09-24 | Limiter "token bucket 2 req/s" (plan §5.4) | Postgres-backed: advisory lock (1 at a time across processes), `canlii_usage` (daily count, UTC day, capped at 4,500) and 0.65 s pacing, with a retry on 429 | Pacing at exactly 0.5 s drew a 429 in 1 of 8 live requests (jitter). No rate-limit headers to react to, and the API server, scripts and manual probes must share one budget, so the state lives in the database rather than in-process |
