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
| `CANLII_API_KEY` | ❌ not yet requested | **apply today** — see §2, it's a manual-review bottleneck for Phase 7 |

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
- **Confirmed gap: ONSC (Ontario Superior Court) is NOT in the corpus.** The plan's Phase 7
  workaround (CanLII detection + user-upload bridge) is the right call, not a hedge against an
  uncertain gap — it's a real, confirmed one.
- ⚠️ **License wrinkle not in the original plan:** each document carries its own
  `upstream_license`, and the maintainers state some upstream sources "include limits on
  commercial use." If this tool is ever commercial (not just research/internal), audit
  `upstream_license` per source before shipping — don't assume blanket reuse rights.

### Justice Laws (federal legislation) — ✅ confirmed
- Bulk XML (and a "WebXML" variant) published on the Open Government Portal, refreshed every two
  weeks, pulled via FTP (`ftp://205.193.86.89/PITXML/`). Crown copyright, openly reusable — matches
  the plan's Phase 5 ingestion design directly. No registration.

### e-Laws (Ontario legislation) — ⚠️ partially confirmed, needs a manual look
- Confirmed: King's Printer for Ontario copyright permits free reproduction of statutes,
  regulations, and judicial decisions without permission or charge, provided it's accurate and not
  represented as an official version — the reuse-terms risk in Phase 7 is resolved favorably.
- **Not confirmed:** whether e-laws.gov.on.ca / ontario.ca/laws exposes a bulk XML dump or API the
  way Justice Laws does (the site is JS-rendered, blocked automated fetch). Unlike Justice Laws,
  there's no equivalent Open Government Portal bulk dataset that turned up. **Action for Phase 7:**
  budget time to manually inspect the site's network requests / look for a documented e-Laws XML
  endpoint before assuming a bespoke scraper is required — this is the one item in the plan that
  may need more engineering than "parse hierarchy" implies.

### CanLII API — ✅ confirmed, but has a lead time
- Real, read-only REST API. Endpoints: case browse (by court/database), case metadata, citator
  (cited-by / cites, both directions), legislation browse/metadata. Matches the plan's "detection +
  link-out" use exactly — no full-text redistribution permitted, consistent with never caching
  full text (§5.4 of the plan).
- **Access requires a manual application** through CanLII's feedback form, describing project
  scope — not instant, self-serve signup. Since it only gates Phase 7 but the review could take
  a while, **apply now** so the key is ready when you get there.
- The plan's "2 req/s, ≤5000/day" limiter numbers weren't independently visible in the public docs
  (they may be stated in the agreement CanLII sends with the key) — treat as provisional until the
  key arrives and confirm the actual cap then.

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
   human-review lag; everything else is instant/self-serve.
4. Provision an `ANTHROPIC_API_KEY` (billing-enabled, separate from Claude Code's own login).
5. Confirm GPU is visible inside whatever runs the embedding/rerank code (Docker GPU passthrough on
   Windows needs WSL2 backend + NVIDIA Container Toolkit if those steps run in a container; simplest
   path is running the embedding/ingestion workers directly on the host venv instead of in Docker).
6. Manually check e-laws.gov.on.ca for a bulk/XML access path before Phase 7 starts (see §2) — this
   is the one unresolved unknown in the whole plan.

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
