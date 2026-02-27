# MedBrief — Minimal MVP Plan

> Goal: Working demo in ~3h. Ruthlessly cut anything that isn't the core loop.

---

## What We're Actually Building

Input: medical condition string
Output: 4-section HTML report with citations, streamed to browser

---

## File Structure (12 files total)

```
medbrief/
  app.py                  # Flask routes + SSE
  orchestrator.py         # thin controller
  research_loop.py        # fetch → evaluate → repeat
  report_builder.py       # Claude synthesis → ReportJSON
  citation_registry.py    # dedup + index
  sources/
    clinicaltrials.py
    pubmed.py
    openfda.py
    web_search.py         # Anthropic tool — replaces europepmc + edgar for MVP
  templates/
    index.html
    report.html
  static/report.js        # SSE consumer
  .env
```

**Cut from Phase 1:** `europepmc.py`, `sec_edgar.py`, `query_builder.py`, `html_renderer.py` (inline render in report_builder), `session.py` (inline dict in app.py)

---

## Phase 1 — Core MVP (3 sources + web search)

### P1: App skeleton (30 min)

- `app.py`: 3 routes only
  - `GET /` → index.html
  - `POST /generate` → start thread, return session_id ([System-Design.md:184-202](System-Desing.md#L184))
  - `GET /stream/<sid>` → SSE generator consuming `queue.Queue`
- Session store: plain dict + `threading.Lock` — inline in app.py, no separate file
- SSE event types: `status`, `section`, `complete`, `error` ([System-Design.md:172-177](System-Desing.md#L172))

### P2: 3 source clients (45 min)

All inherit same minimal pattern:

```python
def fetch(params: dict) -> list[dict]:
    r = requests.get(URL, params=params, timeout=15)
    r.raise_for_status()
    return normalize(r.json())
```

**`sources/clinicaltrials.py`**
- Single call: `query.cond`, `filter.overallStatus=RECRUITING,ACTIVE_NOT_RECRUITING`, `filter.phase=PHASE2,PHASE3`, `pageSize=15`
- Fields: NCTId, BriefTitle, OverallStatus, Phase, InterventionName, LeadSponsorName, BriefSummary
- Ref: [datasrc.md:102-136](datasrc.md#L102)

**`sources/pubmed.py`**
- ESearch → EFetch two-step (keep as-is from datasrc)
- Two calls: one for reviews/guidelines, one for recent clinical trials
- Ref: [datasrc.md:212-291](datasrc.md#L212)

**`sources/openfda.py`**
- Two calls only: `drug/label.json` (indications) + `drug/drugsfda.json` (approvals)
- Skip adverse events for MVP
- Ref: [datasrc.md:381-409](datasrc.md#L381)

**`sources/web_search.py`**
- Anthropic tool call — covers what europepmc + edgar would have covered
- Pass 2 queries: recent developments + key players pipeline
- Ref: [datasrc.md:727-773](datasrc.md#L727)

### P3: Research loop — MAX 3 iterations (30 min)

Cut from 8 → **3 iterations**. Loop logic:

```
iteration 1: broad fetch from all 3 sources + web_search
iteration 2: if score < 0.65, Claude refines queries and retries
iteration 3: forced synthesis regardless
```

Sufficiency eval: same Claude call as design ([System-Design.md:264-285](System-Desing.md#L264)) but simplified threshold — just check counts:
- ≥2 trials → emerging_treatments sufficient
- ≥2 pubmed articles → standard_of_care sufficient
- web_search returned text → recent_developments sufficient

Skip per-dimension scores. Single `sufficient: bool` return.

### P4: Report builder + synthesis (45 min)

- `report_builder.py`: pack raw data → single Claude call with `SYNTHESIS_SYSTEM_PROMPT`
- System prompt: use verbatim from [System-Design.md:851-869](System-Desing.md#L851)
- Output: JSON matching schema at [System-Design.md:894-993](System-Desing.md#L894)
- **Inline citation validation**: strip any NCT/PMID not in registry before render
- Render JSON → HTML inside `report_builder.py` (no separate html_renderer.py)

### P5: Frontend (30 min)

- `index.html`: single text input + submit button, plain HTML
- `report.html`: 4-section divs, references list at bottom, caveats footer
- `report.js`: SSE consumer, appends sections as they stream in — ~50 lines

---

## Cut completely from Phase 1

| Feature | Why cut |
|---|---|
| Europe PMC | Web search covers recency; pubmed covers literature |
| SEC EDGAR | Key players from ClinicalTrials sponsors is enough |
| `query_builder.py` | Iteration 1 uses hardcoded params; Claude generates iter 2+ queries inline |
| MeSH synonym lookup | Claude handles synonym expansion in refined queries |
| Domain-tier scoring for web | Too much plumbing; just pass web results with source URL |
| Session cleanup thread | Not needed for demo |
| `[WEB_ONLY]` labeling pipeline | Flag in synthesis prompt instead |

---

## Phase 2 Add-ons (after demo, in order of effort)

### Add-on A: Chat on report (~1h)
- `POST /chat/<sid>` route
- Full report JSON as context prefix
- Client-side history (JS array)
- Ref: [System-Design.md:1006-1033](System-Desing.md#L1006)

### Add-on B: Europe PMC client (~30 min)
- Adds high-citation academic literature
- Drop-in alongside pubmed.py
- Ref: [datasrc.md:529-572](datasrc.md#L529)

### Add-on C: SEC EDGAR client (~45 min)
- Enrich key_players section with financial context
- Only queried after company names extracted from ClinicalTrials/OpenFDA
- Ref: [datasrc.md:627-695](datasrc.md#L627)

### Add-on D: PDF export (~1h)
- WeasyPrint: report HTML → PDF
- `POST /export/<sid>?format=pdf`
- Ref: [System-Design.md:1054-1076](System-Desing.md#L1054)

---

## Data Integrity — Non-Negotiable Minimums

These stay in regardless of time pressure:

1. All OpenFDA approval claims labeled "FDA-approved" not "approved" — enforced in synthesis prompt
2. Citation IDs cross-validated against registry before render — strip hallucinated ones
3. Preprint check in pubmed normalize (`publication_types` contains "Preprint")
4. Synthesis prompt: no dosing info, no fabricated identifiers
5. Report footer: jurisdiction caveat + "not for clinical use"

---

## Environment

```bash
pip install flask anthropic requests python-dotenv
```

```env
ANTHROPIC_API_KEY=...
NCBI_API_KEY=...       # optional but get 10 req/sec vs 3
OPENFDA_API_KEY=...    # optional but 120k/day vs 1k
```

---

## Build Order

1. `app.py` skeleton with SSE smoke test
2. All 4 source clients, test each with `type 2 diabetes`
3. `research_loop.py` wired to clients
4. `report_builder.py` + synthesis call
5. `index.html` + `report.html` + `report.js`
6. End-to-end run, fix citation validation
7. Phase 2 add-ons in order of value
