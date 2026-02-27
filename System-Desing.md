# MedBrief — Medical Intelligence Briefing Tool
## System Design & Architecture Document

**Version:** 1.0.0
**Status:** Draft — Pre-Implementation
**Author:** Senior Engineering Lead
**Last Updated:** 2026-02-26

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement & Goals](#2-problem-statement--goals)
3. [System Architecture Overview](#3-system-architecture-overview)
4. [Component Breakdown — High Level](#4-component-breakdown--high-level)
5. [Component Breakdown — Low Level](#5-component-breakdown--low-level)
   - 5.1 Flask Web Server & Routes
   - 5.2 Orchestrator Agent
   - 5.3 Research Loop Engine
   - 5.4 Data Source Clients
   - 5.5 Report Builder & Schema
   - 5.6 Session Manager
6. [Data Source Reference](#6-data-source-reference)
   - 6.1 ClinicalTrials.gov v2
   - 6.2 PubMed / NCBI E-Utilities
   - 6.3 OpenFDA
   - 6.4 Europe PMC
   - 6.5 SEC EDGAR
   - 6.6 Web Search
7. [Agent System Prompt Design](#7-agent-system-prompt-design)
8. [Data Integrity Constraints](#8-data-integrity-constraints)
9. [Output Schema & Report Structure](#9-output-schema--report-structure)
10. [Phase 2 Add-ons Design](#10-phase-2-add-ons-design)
    - 10.1 Chat Agent on Report
    - 10.2 PDF/DOCX Export
11. [Non-Goals & Deferred Scope](#11-non-goals--deferred-scope)
12. [Implementation Plan](#12-implementation-plan)

---

## 1. Executive Summary

MedBrief is a Flask-based agentic web application that generates structured, cited medical intelligence briefings for health strategy teams. Given a medical condition as input, a single Claude-powered orchestrator agent runs a multi-iteration research loop across six data sources, synthesizes findings into a structured four-section report, and streams the result to the browser via Server-Sent Events.

The system prioritizes data traceability: every claim in the report carries a citation object with source type, URL, identifier (DOI / PMID / NCT / CIK), and retrieval timestamp. The retry loop (up to 8 iterations) is governed by Claude's own self-evaluation of data sufficiency — a deliberate design choice that produces adaptive query refinement rather than blind retries.

Target dev time for Phase 1 MVP with agentic coding tools: 3–4 hours.

---

## 2. Problem Statement & Goals

### 2.1 Problem

A health system strategy team analyst needs a comprehensive briefing on a medical condition — covering standard of care, emerging pipeline treatments, key institutional and commercial players, and recent developments — before a board meeting. Currently, this requires 4–6 hours of manual research across PubMed, ClinicalTrials.gov, press release aggregators, and financial filings, with no systematic citation tracking.

### 2.2 Primary Users

Non-clinical health strategists, policy researchers, and pharma/biotech intelligence analysts who need clinical-grade structured summaries with traceable sourcing — not patient-facing clinical advice.

### 2.3 In-Scope Goals

- Accept a medical condition string as input, generate a fully structured briefing with citations
- Research loop queries all data sources in parallel, retries up to 8× with LLM-driven query refinement
- Every factual claim carries a structured citation (DOI, PMID, NCT number, URL, retrieval timestamp)
- Report streams section-by-section via SSE so the user sees progress
- All state is in-memory / session only — no database required for MVP
- Phase 2: chat agent on the report, PDF/DOCX export

### 2.4 Non-Goals (MVP)

- Clinical decision support or patient-facing use
- Authentication or multi-user sessions
- Persistent storage or report history
- Highlight/comment annotation system (deferred)
- Patient history pairing (deferred)
- Real-time guideline sync or versioned knowledge base
- Dosing recommendations (excluded per data integrity constraints)

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         BROWSER CLIENT                          │
│                                                                 │
│  [Search Input] → POST /generate                                │
│  SSE stream     ← GET  /stream/<session_id>                     │
│  Report view    ← rendered HTML with citation superscripts      │
│  (Phase 2)      ← GET  /chat  POST /export                      │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────▼────────────────────────────────────┐
│                      FLASK WEB SERVER                           │
│                                                                 │
│  app.py          — route registration, SSE queue management     │
│  session.py      — in-memory session store (dict + threading)   │
└────────────────────────────┬────────────────────────────────────┘
                             │ Python function call
┌────────────────────────────▼────────────────────────────────────┐
│                    ORCHESTRATOR AGENT                           │
│                                                                 │
│  orchestrator.py — manages research loop, calls Claude API      │
│  research_loop.py — iteration controller, sufficiency evaluator │
│  query_builder.py — builds source-specific query params         │
└──────┬─────────────────────┬──────────────────────────┬─────────┘
       │                     │                          │
       │  parallel calls     │                          │
┌──────▼──────┐  ┌───────────▼───────┐   ┌─────────────▼─────────┐
│  DATA SOURCE│  │  DATA SOURCE      │   │   CLAUDE API           │
│  CLIENTS    │  │  CLIENTS (cont.)  │   │                        │
│             │  │                   │   │  - Tool-use / synthesis │
│ clintrials  │  │ europepmc.py      │   │  - Sufficiency eval     │
│ .py         │  │ sec_edgar.py      │   │  - Report generation    │
│ pubmed.py   │  │ web_search.py     │   │  - Chat endpoint        │
│ openfda.py  │  │                   │   │                        │
└──────┬──────┘  └───────────┬───────┘   └─────────────┬─────────┘
       │                     │                          │
       └──────────────┬──────┘                          │
                      │  raw structured data            │
              ┌───────▼─────────────────────────────────▼──────┐
              │              REPORT BUILDER                     │
              │                                                 │
              │  report_builder.py — JSON schema assembly       │
              │  citation_registry.py — dedup + index citations │
              │  html_renderer.py — JSON → HTML with spans      │
              └─────────────────────────────────────────────────┘
```

---

## 4. Component Breakdown — High Level

| Component | File | Responsibility |
|---|---|---|
| Flask App | `app.py` | Route registration, SSE queue, error handling |
| Session Manager | `session.py` | In-memory store per session_id, thread-safe |
| Orchestrator Agent | `orchestrator.py` | Top-level loop controller, Claude API calls |
| Research Loop | `research_loop.py` | Iteration logic, sufficiency gate, query mutation |
| Query Builder | `query_builder.py` | Per-source query construction from condition string |
| ClinicalTrials Client | `sources/clinicaltrials.py` | API v2 wrapper, retry, response normalization |
| PubMed Client | `sources/pubmed.py` | E-Utilities ESearch + EFetch, rate limiting |
| OpenFDA Client | `sources/openfda.py` | Drug labels, approvals, adverse events |
| Europe PMC Client | `sources/europepmc.py` | REST search, full-text open access |
| SEC EDGAR Client | `sources/sec_edgar.py` | Full-text search, company lookup |
| Web Search Client | `sources/web_search.py` | Anthropic web search tool or Brave API fallback |
| Report Builder | `report_builder.py` | Assembles structured JSON report from raw data |
| Citation Registry | `citation_registry.py` | Deduplication, index assignment, validation |
| HTML Renderer | `html_renderer.py` | Report JSON → HTML with citation spans |
| Templates | `templates/` | Jinja2 index.html, report.html |
| Static | `static/` | report.js (SSE consumer, UI logic) |

---

## 5. Component Breakdown — Low Level

### 5.1 Flask Web Server & Routes

```
app.py

Routes:
  GET  /                        → render index.html (search input)
  POST /generate                → validate input, create session, start background thread, return session_id
  GET  /stream/<session_id>     → SSE endpoint, yields events from session queue
  GET  /report/<session_id>     → render full report HTML from session store
  POST /chat/<session_id>       → Phase 2: chat endpoint
  POST /export/<session_id>     → Phase 2: PDF/DOCX export

SSE Event Types:
  { "type": "status",   "data": "Querying ClinicalTrials.gov..." }
  { "type": "section",  "data": { "key": "standard_of_care", "html": "..." } }
  { "type": "loop_info","data": { "iteration": 3, "sufficiency": 0.6 } }
  { "type": "complete", "data": { "session_id": "...", "references_count": 24 } }
  { "type": "error",    "data": { "message": "...", "source": "pubmed" } }
```

**Threading model:** `POST /generate` spawns a `threading.Thread` that runs the orchestrator and pushes events into a `queue.Queue` stored on the session. The SSE route consumes that queue with a generator. This avoids Flask needing async and keeps the MVP simple.

```python
# app.py — session bootstrap pattern
@app.route("/generate", methods=["POST"])
def generate():
    condition = request.json.get("condition", "").strip()
    if not condition:
        return jsonify({"error": "condition required"}), 400
    session_id = str(uuid.uuid4())
    session_store[session_id] = {
        "queue": queue.Queue(),
        "report": None,
        "status": "running",
        "created_at": datetime.utcnow().isoformat()
    }
    thread = threading.Thread(
        target=run_orchestrator,
        args=(session_id, condition),
        daemon=True
    )
    thread.start()
    return jsonify({"session_id": session_id})
```

---

### 5.2 Orchestrator Agent

The orchestrator is the central controller. It does not itself write the report — it manages the research loop and delegates all LLM calls to structured functions.

```
orchestrator.py

Class: MedBriefOrchestrator

__init__(session_id, condition, event_queue)
  - stores session state
  - initializes all data source clients
  - initializes citation_registry
  - sets loop parameters: max_iterations=8, sufficiency_threshold=0.75

run()
  - calls research_loop.run() which returns raw_data dict
  - calls report_builder.build(raw_data, citation_registry)
  - calls html_renderer.render(report_json)
  - pushes "complete" event to queue

Responsibilities:
  - Owns the event_queue — all SSE pushes route through here
  - Catches all exceptions from data source clients — pushes error events but continues
  - Passes structured intermediate data to research_loop at each iteration
```

**Key design principle:** The orchestrator never calls Claude directly for report synthesis mid-loop. Claude is called twice: once per loop iteration for sufficiency evaluation + query refinement, and once at the end for full report synthesis. This keeps token usage predictable.

---

### 5.3 Research Loop Engine

This is the most architecturally significant component. It orchestrates the iterative data gathering and governs when to stop.

```
research_loop.py

Class: ResearchLoop

__init__(condition, clients, event_queue, max_iterations=8)

run() -> RawDataBundle
  loop for iteration in range(max_iterations):
    1. Build queries for this iteration (query_builder.build())
    2. Run all source clients in parallel (ThreadPoolExecutor)
    3. Accumulate results into RawDataBundle
    4. Push "loop_info" SSE event
    5. Call _evaluate_sufficiency(accumulated_data) -> SufficiencyResult
    6. If SufficiencyResult.sufficient or iteration == max_iterations-1: break
    7. Else: refine queries based on SufficiencyResult.missing_areas
  return accumulated RawDataBundle
```

**Sufficiency Evaluation — Claude Call:**

```python
def _evaluate_sufficiency(self, data: RawDataBundle) -> SufficiencyResult:
    """
    Calls Claude with a compact summary of what was retrieved.
    Claude returns structured JSON assessing coverage across 4 report dimensions.
    """
    summary = data.to_evaluation_summary()  # strips full text, keeps counts/titles
    
    prompt = SUFFICIENCY_EVAL_PROMPT.format(
        condition=self.condition,
        iteration=self.current_iteration,
        data_summary=json.dumps(summary, indent=2)
    )
    
    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system=SUFFICIENCY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Response is structured JSON — parse it
    return SufficiencyResult.from_claude_response(response.content[0].text)
```

**SufficiencyResult schema:**
```json
{
  "sufficient": false,
  "overall_score": 0.55,
  "dimension_scores": {
    "standard_of_care": 0.8,
    "emerging_treatments": 0.4,
    "key_players": 0.5,
    "recent_developments": 0.6
  },
  "missing_areas": [
    "Phase III trial data for emerging treatments is sparse",
    "No FDA approval dates found for standard-of-care drugs"
  ],
  "refined_queries": {
    "clinicaltrials": { "query.cond": "...", "filter.phase": "PHASE3" },
    "pubmed": { "term": "...[MeSH] AND clinical trial[pt]" },
    "openfda": { "search": "..." },
    "europepmc": { "query": "..." },
    "web_search": ["...", "..."]
  }
}
```

**Query Mutation Strategy per iteration:**

| Iteration | Strategy |
|---|---|
| 1 | Exact condition string, all sources, broad filters |
| 2 | LLM-refined queries from iteration 1 sufficiency eval |
| 3 | Add MeSH synonyms (from PubMed MeSH lookup), broaden ClinicalTrials phase filter |
| 4–6 | LLM continues refining; may pivot to related conditions or drug class names |
| 7–8 | Fallback: web search with explicit recency filter, EDGAR company name search |

---

### 5.4 Data Source Clients

All clients share a base class enforcing consistent interface:

```python
# sources/base.py

class BaseSourceClient:
    SOURCE_NAME: str = ""
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_BASE: float = 1.5  # seconds

    def fetch(self, query_params: dict) -> SourceResult:
        raise NotImplementedError

    def _execute_with_retry(self, url: str, params: dict, headers: dict = None) -> requests.Response:
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.get(url, params=params, headers=headers, timeout=15)
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    wait = self.RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"{self.SOURCE_NAME}: rate limited, waiting {wait}s")
                    time.sleep(wait)
                elif e.response.status_code >= 500:
                    time.sleep(self.RETRY_BACKOFF_BASE ** attempt)
                else:
                    raise
            except requests.exceptions.Timeout:
                logger.warning(f"{self.SOURCE_NAME}: timeout on attempt {attempt+1}")
                if attempt == self.MAX_RETRIES - 1:
                    raise
        raise RuntimeError(f"{self.SOURCE_NAME}: all retry attempts exhausted")

class SourceResult:
    source_name: str
    items: list[dict]          # normalized records
    raw_count: int             # total matching in source (not just returned)
    query_used: dict           # exact params used
    retrieved_at: str          # ISO timestamp
    errors: list[str]          # non-fatal warnings
```

---

### 5.5 Report Builder & Schema

The report builder takes the accumulated `RawDataBundle` and a final Claude synthesis call, then structures the output.

```
report_builder.py

Class: ReportBuilder

build(raw_data: RawDataBundle, citation_registry: CitationRegistry) -> ReportJSON
  1. Partition raw_data by report dimension
  2. Call Claude for synthesis — single large call with all data
  3. Parse Claude's structured JSON response
  4. Assign citation indices from citation_registry
  5. Return complete ReportJSON
```

**Final Synthesis Claude Call — key design decision:**

This call receives ALL accumulated raw data (trimmed to abstracts, not full text) and instructs Claude to produce a structured JSON report with inline citation markers. The system prompt enforces that every claim must reference a source from the provided data bundle — Claude is explicitly instructed not to add claims without a corresponding source in the bundle.

```python
SYNTHESIS_SYSTEM_PROMPT = """
You are a medical intelligence analyst producing structured briefings for health strategy teams.
You will be given structured data retrieved from authoritative medical databases.
Your task is to synthesize this data into a four-section briefing.

CRITICAL RULES:
1. Every factual claim MUST reference a source from the provided data bundle using [SOURCE_INDEX] notation.
2. Do NOT introduce any drug names, trial IDs, approval dates, or statistics not present in the provided data.
3. Clearly distinguish approved therapies from investigational ones.
4. Scope all approval status claims to the specific regulatory authority (FDA vs EMA vs other).
5. Flag any claims derived from preprints with [PREPRINT] notation.
6. Do NOT include dosing information — this is explicitly out of scope.
7. Respond ONLY with valid JSON conforming to the ReportJSON schema below.

OUTPUT SCHEMA: [see Section 9]
"""
```

---

### 5.6 Session Manager

```python
# session.py — thread-safe in-memory store

import threading

_store: dict = {}
_lock = threading.Lock()

def create_session(session_id: str, condition: str) -> None:
    with _lock:
        _store[session_id] = {
            "condition": condition,
            "queue": queue.Queue(),
            "report": None,
            "status": "running",   # running | complete | error
            "created_at": datetime.utcnow().isoformat(),
            "loop_iterations": 0,
            "final_sufficiency_score": None
        }

def get_session(session_id: str) -> dict | None:
    with _lock:
        return _store.get(session_id)

def update_report(session_id: str, report: dict) -> None:
    with _lock:
        if session_id in _store:
            _store[session_id]["report"] = report
            _store[session_id]["status"] = "complete"
```

Sessions are never persisted — they live until process restart. For the MVP, this is sufficient. A cleanup thread can purge sessions older than 2 hours to prevent memory growth.

---

## 6. Data Source Reference

### 6.1 ClinicalTrials.gov v2

**Base URL:** `https://clinicaltrials.gov/api/v2/studies`
**Auth:** None required
**Rate Limit:** Not officially documented — be conservative at 5 req/sec

**Primary query parameters:**

| Param | Type | Description | MVP Usage |
|---|---|---|---|
| `query.cond` | string | Condition/disease search | Primary filter |
| `query.term` | string | General keyword search | Broadening fallback |
| `filter.overallStatus` | enum | RECRUITING, ACTIVE_NOT_RECRUITING, COMPLETED, TERMINATED | Filter to active/recruiting for pipeline |
| `filter.phase` | enum | PHASE1, PHASE2, PHASE3, PHASE4 | Focus on PHASE2,PHASE3 for emerging treatments |
| `filter.advanced` | string | Advanced Elasticsearch-style filters | sponsor name, date ranges |
| `pageSize` | int | Max 1000 | Use 20 for MVP |
| `format` | enum | json, csv | Always json |
| `countTotal` | bool | Include total match count | Always true |
| `fields` | comma-list | Specific fields to return | See below |

**Recommended fields parameter for MVP:**
```
fields=NCTId,BriefTitle,OfficialTitle,OverallStatus,Phase,Condition,
       InterventionName,InterventionType,LeadSponsorName,
       CollaboratorName,PrimaryOutcomeMeasure,BriefSummary,
       StartDate,CompletionDate,EnrollmentCount,EligibilityCriteria,
       CentralContactName,LocationFacility,StudyType
```

**Example call:**
```
GET https://clinicaltrials.gov/api/v2/studies
  ?query.cond=type+2+diabetes
  &filter.overallStatus=RECRUITING,ACTIVE_NOT_RECRUITING
  &filter.phase=PHASE3,PHASE4
  &pageSize=20
  &format=json
  &countTotal=true
  &fields=NCTId,BriefTitle,OverallStatus,Phase,InterventionName,LeadSponsorName,BriefSummary,StartDate,CompletionDate
```

**Normalized output schema per record:**
```json
{
  "source": "clinicaltrials",
  "id": "NCT05XXXXXXX",
  "title": "...",
  "status": "RECRUITING",
  "phase": "PHASE3",
  "condition": "Type 2 Diabetes",
  "intervention": "Semaglutide 2.4mg",
  "sponsor": "Novo Nordisk A/S",
  "summary": "...",
  "start_date": "2023-06",
  "completion_date": "2025-12",
  "enrollment": 450,
  "url": "https://clinicaltrials.gov/study/NCT05XXXXXXX",
  "retrieved_at": "2026-02-26T10:00:00Z"
}
```

**Report dimension:** Emerging Treatments (primary), Key Players (sponsor extraction)

**Data integrity notes:**
- Always query ClinicalTrials directly — never trust web search snippets for trial status
- TERMINATED and WITHDRAWN trials must be clearly labeled, not silently filtered
- Phase I trials should be included in data bundle but flagged as early-stage in synthesis

---

### 6.2 PubMed / NCBI E-Utilities

**Base URL:** `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`
**Auth:** Free NCBI API key — store in `.env` as `NCBI_API_KEY`
**Rate Limit:** 3 req/sec without key; 10 req/sec with key

**Two-step workflow:**

**Step 1 — ESearch (get PMIDs):**
```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
  ?db=pubmed
  &term=type+2+diabetes[MeSH+Terms]+AND+treatment[Title]
  &retmax=20
  &retmode=json
  &sort=relevance
  &datetype=pdat
  &mindate=2020
  &maxdate=2026
  &api_key=<NCBI_API_KEY>
```

**Step 2 — EFetch (get abstracts by PMID list):**
```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
  ?db=pubmed
  &id=36754560,37400836,37223279
  &rettype=abstract
  &retmode=xml
  &api_key=<NCBI_API_KEY>
```

**Publication type filters for each report section:**

| Report Section | PubMed Filter Strategy |
|---|---|
| Standard of Care | `AND (systematic review[pt] OR review[pt] OR guideline[pt])` |
| Emerging Treatments | `AND (clinical trial[pt] OR randomized controlled trial[pt])`, sort by date |
| Recent Developments | `AND ("last 2 years"[pdat])`, no type filter |

**Important: MeSH term lookup for query refinement**

On iteration 1 failure, use ESearch with `&usehistory=y` and `&rettype=uilist` to retrieve actual MeSH terms the condition maps to. These feed back into iteration 2 queries:
```
GET .../esearch.fcgi?db=pubmed&term=type+2+diabetes&field=mesh&retmode=json
```

**Normalized output schema per record:**
```json
{
  "source": "pubmed",
  "id": "PMID:36754560",
  "pmid": "36754560",
  "title": "...",
  "abstract": "...",
  "authors": ["Smith J", "Doe A"],
  "journal": "New England Journal of Medicine",
  "pub_date": "2023-04-15",
  "publication_types": ["Systematic Review"],
  "mesh_terms": ["Diabetes Mellitus, Type 2", "Hypoglycemic Agents"],
  "doi": "10.1056/NEJMoa2300xxx",
  "is_preprint": false,
  "url": "https://pubmed.ncbi.nlm.nih.gov/36754560/",
  "retrieved_at": "2026-02-26T10:00:00Z"
}
```

**Preprint detection:** Check `publication_types` for "Preprint" value. Flag explicitly — never silently include as peer-reviewed evidence.

**Report dimension:** Standard of Care (primary), Emerging Treatments (clinical trials), Recent Developments

---

### 6.3 OpenFDA

**Base URL:** `https://api.fda.gov/`
**Auth:** Free API key — store in `.env` as `OPENFDA_API_KEY`
**Rate Limit:** 240 req/min, 120,000 req/day with key

**Three endpoints used:**

**A) Drug Labels — Standard of Care approved therapies:**
```
GET https://api.fda.gov/drug/label.json
  ?search=indications_and_usage:"type 2 diabetes"
  &limit=10
  &api_key=<OPENFDA_API_KEY>
```
Returns: brand/generic name, indications text, warnings, drug class, manufacturer

**B) Drugs@FDA — Approval history:**
```
GET https://api.fda.gov/drug/drugsfda.json
  ?search=products.marketing_status:"prescription"+AND+openfda.pharm_class_epc:"Dipeptidyl+Peptidase+4+Inhibitor"
  &limit=10
  &api_key=<OPENFDA_API_KEY>
```
Returns: NDA/BLA number, approval date, sponsor name, product list

**C) Adverse Events (FAERS) — Safety signal context:**
```
GET https://api.fda.gov/drug/event.json
  ?search=patient.drug.openfda.generic_name:"metformin"
  &count=patient.reaction.reactionmeddrapt.exact
  &limit=10
  &api_key=<OPENFDA_API_KEY>
```
Returns: top adverse event terms with count

**Critical disclaimer per FDA:** OpenFDA data has not been validated for clinical use. The synthesis prompt must include the caveat that approval status from OpenFDA is informational only and should be verified with DailyMed for production use.

**Jurisdiction scoping:** OpenFDA covers FDA (US) approvals only. Synthesis must explicitly state "FDA-approved" not "approved" generically.

**Normalized output schema per record:**
```json
{
  "source": "openfda",
  "endpoint": "drugsfda",
  "id": "NDA021977",
  "brand_name": "Januvia",
  "generic_name": "sitagliptin",
  "drug_class": "Dipeptidyl Peptidase-4 Inhibitor",
  "sponsor": "Merck Sharp & Dohme",
  "approval_date": "2006-10-16",
  "jurisdiction": "FDA",
  "indications_summary": "...",
  "url": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=021977",
  "retrieved_at": "2026-02-26T10:00:00Z"
}
```

**Report dimension:** Standard of Care (approved drugs), Key Players (sponsors)

---

### 6.4 Europe PMC

**Base URL:** `https://www.ebi.ac.uk/europepmc/webservices/rest/`
**Auth:** None required
**Rate Limit:** Not officially published — conservative 3 req/sec

**Primary endpoint:**
```
GET https://www.ebi.ac.uk/europepmc/webservices/rest/search
  ?query=type+2+diabetes+AND+(treatment+OR+therapy)
  &format=json
  &resultType=core
  &pageSize=20
  &sort=CITED+desc
  &filter=open_access:y
```

**Key query operators:**

| Operator | Example | Purpose |
|---|---|---|
| `TITLE:` | `TITLE:"type 2 diabetes"` | Title-only match |
| `ABSTRACT:` | `ABSTRACT:SGLT2` | Abstract keyword |
| `PUB_YEAR:` | `PUB_YEAR:2023` | Year filter |
| `SRC:MED` | `SRC:MED` | Restrict to MEDLINE/PubMed |
| `OPEN_ACCESS:y` | filter param | Full text available |
| `HAS_FULLTEXT:y` | filter | Full-text in Europe PMC |

**resultType options:**
- `lite` — title, authors, source only (fast)
- `core` — full metadata including abstract, citations, MeSH (use this)

**Annotations API** (bonus — text-mined entities):
```
GET https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds
  ?articleIds=MED:36754560
  &type=Gene_Proteins,Diseases,Chemicals
  &format=JSON
```

This returns structured entity mentions (genes, chemicals, diseases) extracted from full-text — useful for "Key Players" company mentions if needed.

**Preprint filter:** Europe PMC indexes preprints from bioRxiv, medRxiv. Always include `NOT (SRC:PPR)` in query to exclude preprints, or explicitly flag `source_type: preprint` in normalized output.

**Normalized output schema per record:**
```json
{
  "source": "europepmc",
  "id": "PMID:36754560",
  "pmid": "36754560",
  "pmcid": "PMC9876543",
  "title": "...",
  "abstract": "...",
  "journal": "Lancet Diabetes Endocrinology",
  "pub_year": 2023,
  "citation_count": 147,
  "is_open_access": true,
  "full_text_url": "https://europepmc.org/article/MED/36754560",
  "is_preprint": false,
  "source_type": "journal_article",
  "retrieved_at": "2026-02-26T10:00:00Z"
}
```

**Report dimension:** Standard of Care (high-citation reviews), Recent Developments (open-access full text)

---

### 6.5 SEC EDGAR

**Usage context:** Used only for "Key Players" section — enriches company profiles identified from ClinicalTrials sponsors and OpenFDA drug sponsors with financial and pipeline disclosure context from 10-K/10-Q filings.

**Two-step flow:**
1. Extract company names from ClinicalTrials sponsor field and OpenFDA sponsor field
2. Look up those companies in EDGAR, retrieve recent 10-K excerpts mentioning the condition

**Full-Text Search API:**
```
GET https://efts.sec.gov/LATEST/search-index
  ?q="type+2+diabetes"+AND+"semaglutide"
  &dateRange=custom
  &startdt=2024-01-01
  &enddt=2026-02-26
  &forms=10-K,10-Q
  &hits.hits._source=period_of_report,display_names,file_date,form_type
```

**Company Lookup (get CIK for a company name):**
```
GET https://www.sec.gov/cgi-bin/browse-edgar
  ?company=Novo+Nordisk
  &action=getcompany
  &type=10-K
  &dateb=&owner=include
  &count=5
  &search_text=
  &output=atom
```

**Filing Retrieval (by CIK):**
```
GET https://data.sec.gov/submissions/CIK0001545654.json
```
Returns full submission history for a company, including all recent filings.

**EDGAR is deprioritized in the retry loop:** Only queried after iteration 3 when company names are established from other sources. It is not part of iteration 1.

**Normalized output schema per record:**
```json
{
  "source": "sec_edgar",
  "company_name": "Novo Nordisk A/S",
  "cik": "0001545654",
  "filing_type": "10-K",
  "period": "2024-12-31",
  "file_date": "2025-02-06",
  "relevant_excerpt": "...our GLP-1 pipeline for type 2 diabetes...",
  "filing_url": "https://www.sec.gov/Archives/edgar/data/1545654/...",
  "retrieved_at": "2026-02-26T10:00:00Z"
}
```

**Report dimension:** Key Players (financial context, pipeline disclosures)

---

### 6.6 Web Search

**Usage:** Catch-all for recent developments, press releases, guideline updates, and anything not in structured databases. Also used in later loop iterations when structured sources return insufficient data.

**Implementation:** Use Anthropic's native web search tool via the Claude API `tools` parameter. No external API key needed.

```python
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search"
}
```

**Query strategy per report section:**

| Section | Web Search Query Pattern |
|---|---|
| Standard of Care | `"{condition}" treatment guidelines {current_year} site:who.int OR site:nih.gov OR site:nejm.org` |
| Emerging Treatments | `"{condition}" new treatment clinical trial results {current_year}` |
| Key Players | `"{condition}" pharmaceutical company pipeline {current_year}` |
| Recent Developments | `"{condition}" breakthrough research news {current_year}` |

**Critical filtering — mitigate SEO content:**
Web search results are passed to Claude with explicit instructions to:
- Reject sources from patient portals (WebMD, Healthline, Mayo Clinic patient pages)
- Prefer `.gov`, `.edu`, journal domains, institutional press offices
- Flag any claim sourced only from a web result (not cross-validated with a structured source) with `[WEB_ONLY]` in the citation

---

## 7. Agent System Prompt Design

### 7.1 Sufficiency Evaluation Prompt

```python
SUFFICIENCY_SYSTEM_PROMPT = """
You are a quality control agent evaluating whether a medical intelligence briefing 
has sufficient data across four dimensions. You assess data completeness — not 
medical accuracy. You always respond with valid JSON only, no preamble or explanation.
"""

SUFFICIENCY_EVAL_PROMPT = """
Condition: {condition}
Research iteration: {iteration} of 8

Data retrieved so far:
{data_summary}

Evaluate the data sufficiency for each of the four report dimensions.
A score of 1.0 means completely sufficient; 0.0 means no relevant data.

Consider "sufficient" (overall_score >= 0.75) when:
- Standard of Care: At least 3 peer-reviewed review articles or guidelines found
- Emerging Treatments: At least 2 active/recruiting clinical trials (Phase II or higher)
- Key Players: At least 3 distinct organizations (pharma, academic, govt) identified
- Recent Developments: At least 2 sources from within the past 24 months

If not sufficient, provide specific refined_queries for each source that would 
address the missing areas. Adjust search strategy — do not just repeat the same query.

Respond with JSON conforming to SufficiencyResult schema.
"""
```

### 7.2 Final Synthesis Prompt

```python
SYNTHESIS_SYSTEM_PROMPT = """
You are a senior medical intelligence analyst producing structured briefings for 
health strategy teams (non-clinical audience). You write in clear, professional 
language appropriate for executives and strategists.

ABSOLUTE RULES — violations will invalidate the report:
1. Every factual claim requires a [SOURCE_N] citation marker referencing the provided data bundle.
2. Do not fabricate, hallucinate, or infer drug names, trial IDs, dates, or statistics 
   not explicitly present in the provided data.
3. Mark all investigational/pipeline therapies explicitly as "investigational" or 
   "in clinical development" — never as "approved" or "available."
4. Scope ALL approval claims to specific regulatory authority: write "FDA-approved" 
   not "approved."
5. Flag preprint sources with [PREPRINT] after the citation.
6. Do not include any dosing information, dose ranges, or administration schedules.
7. If data on a dimension is genuinely sparse, state this explicitly rather than 
   extrapolating or generalizing.
8. Respond ONLY with valid JSON conforming to the ReportJSON schema. No prose outside JSON.
"""
```

---

## 8. Data Integrity Constraints

These are hard architectural rules derived from known failure modes of LLM-based medical research tools.

| Failure Mode | Mitigation |
|---|---|
| **Guideline staleness** | PubMed queries always include `mindate=2020` on standard-of-care searches. Web search results must include publication date metadata. Synthesis prompt flags any result without a date. |
| **Drug approval status errors** | All approval claims sourced exclusively from OpenFDA `drugsfda` endpoint with `approval_date` field. Web search approval mentions are labeled `[WEB_ONLY, UNVERIFIED]`. |
| **Trial status lag** | ClinicalTrials.gov API queried directly every time — never trust secondary sources for trial status. Status from API response used verbatim. |
| **Hallucinated citations** | All citation identifiers (PMID, NCT, DOI) in Claude's synthesis output are cross-validated against the `citation_registry` before rendering. Any identifier not in the registry is stripped and replaced with `[CITATION REMOVED — UNVERIFIED]`. |
| **Preprint infiltration** | Europe PMC queries include `NOT (SRC:PPR)` by default. PubMed `publication_types` checked for "Preprint". Preprint flag propagated through to citation display. |
| **Jurisdiction confusion** | OpenFDA explicitly labeled as FDA/US. EMA data not included in MVP — explicitly noted as out of scope in report footer. |
| **Off-label conflation** | Synthesis prompt instructs Claude to distinguish "approved indication" vs "studied in" vs "used off-label" when source data makes the distinction available. |
| **SEO-optimized medical content** | Web search results scored by domain authority tier (gov/edu/journal = tier 1; hospital/institution = tier 2; portal/aggregator = tier 3). Tier 3 sources can inform context but not factual claims. |
| **Dosing information** | Synthesis system prompt explicitly prohibits dosing. `DosageAndAdministration` field from OpenFDA labels is excluded from data bundle passed to Claude. |

---

## 9. Output Schema & Report Structure

```json
{
  "report_id": "uuid",
  "condition": "Type 2 Diabetes Mellitus",
  "generated_at": "2026-02-26T10:45:00Z",
  "loop_iterations_used": 3,
  "final_sufficiency_score": 0.87,
  "data_sources_queried": ["clinicaltrials", "pubmed", "openfda", "europepmc", "web_search"],

  "sections": {

    "overview": {
      "title": "Condition Overview",
      "content": "Type 2 diabetes mellitus (T2DM) is a chronic metabolic disorder...",
      "claims": [
        {
          "text": "T2DM affects approximately 537 million adults globally as of 2021",
          "citation_indices": [1, 4],
          "confidence": "high"
        }
      ]
    },

    "standard_of_care": {
      "title": "Current Standard of Care",
      "content": "...",
      "approved_therapies": [
        {
          "name": "Metformin",
          "drug_class": "Biguanide",
          "regulatory_status": "FDA-approved",
          "approval_year": 1994,
          "nda_number": "NDA020357",
          "citation_index": 2
        }
      ],
      "claims": []
    },

    "emerging_treatments": {
      "title": "Emerging Treatments in Development",
      "pipeline_items": [
        {
          "name": "Investigational compound or trial name",
          "nct_id": "NCT0XXXXXXXX",
          "phase": "Phase III",
          "sponsor": "...",
          "status": "RECRUITING",
          "primary_outcome": "...",
          "estimated_completion": "2026-Q4",
          "citation_index": 7
        }
      ],
      "claims": []
    },

    "key_players": {
      "title": "Key Companies & Institutions",
      "entities": [
        {
          "name": "Novo Nordisk A/S",
          "type": "pharmaceutical",
          "role": "Lead sponsor — 4 active Phase III trials",
          "edgar_filing_context": "...",
          "citation_indices": [7, 11, 15]
        }
      ],
      "claims": []
    },

    "recent_developments": {
      "title": "Recent Developments",
      "content": "...",
      "claims": []
    }
  },

  "references": [
    {
      "index": 1,
      "source_type": "pubmed",
      "identifier": "PMID:35870000",
      "title": "IDF Diabetes Atlas 10th edition",
      "url": "https://pubmed.ncbi.nlm.nih.gov/35870000/",
      "pub_date": "2022-01-01",
      "is_preprint": false,
      "jurisdiction": null,
      "retrieved_at": "2026-02-26T10:00:00Z"
    }
  ],

  "report_caveats": [
    "Approval status reflects FDA (US) only — EMA/international status not covered.",
    "OpenFDA data is informational; verify critical approval details at DailyMed.",
    "Dosing information is excluded from this report.",
    "This report is for strategic intelligence purposes only — not clinical guidance."
  ],

  "data_quality_flags": []
}
```

---

## 10. Phase 2 Add-ons Design

### 10.1 Chat Agent on Report

**New Route:** `POST /chat/<session_id>`

**Architecture:** Stateless from the server's perspective — the full report JSON is stored in the session and prepended to each chat turn as context.

```python
@app.route("/chat/<session_id>", methods=["POST"])
def chat(session_id):
    session = get_session(session_id)
    if not session or not session["report"]:
        return jsonify({"error": "report not found"}), 404

    user_message = request.json.get("message", "").strip()
    history = request.json.get("history", [])  # client maintains history

    messages = [
        {"role": "user", "content": CHAT_CONTEXT_PROMPT.format(
            condition=session["report"]["condition"],
            report_json=json.dumps(session["report"], indent=2)
        )},
        {"role": "assistant", "content": "I have reviewed the briefing. How can I help?"},
        *history,
        {"role": "user", "content": user_message}
    ]

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=CHAT_SYSTEM_PROMPT,
        messages=messages
    )
    return jsonify({"response": response.content[0].text})
```

**Chat System Prompt:**
```
You are a medical intelligence analyst assistant. The user is reviewing a briefing 
you generated. Answer questions strictly based on the briefing content and your 
knowledge. When answering, cite the relevant sections. 
Do not speculate beyond what the briefing contains. 
Do not provide clinical advice or dosing information.
```

**Frontend:** A fixed bottom panel in `report.html` with message input and response area. Chat history stored in-browser (JS array). No backend storage needed.

---

### 10.2 PDF/DOCX Export

**New Route:** `POST /export/<session_id>?format=pdf|docx`

**Architecture:** Report JSON → HTML → PDF (via WeasyPrint) or DOCX (via python-docx). The report JSON already has all structure needed — this is purely a rendering concern.

```python
@app.route("/export/<session_id>", methods=["POST"])
def export_report(session_id):
    session = get_session(session_id)
    fmt = request.args.get("format", "pdf")
    
    report = session["report"]
    if fmt == "pdf":
        html = render_template("export_report.html", report=report)
        pdf_bytes = weasyprint.HTML(string=html).write_pdf()
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            download_name=f"medbrief_{report['condition'].replace(' ', '_')}.pdf"
        )
    elif fmt == "docx":
        docx_bytes = build_docx(report)  # uses python-docx
        return send_file(
            io.BytesIO(docx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            download_name=f"medbrief_{report['condition'].replace(' ', '_')}.docx"
        )
```

**PDF styling:** A dedicated `export_report.html` template with print-optimized CSS — no navigation, full-width layout, citation footnotes at page bottom, cover page with condition name and generation timestamp.

---

## 11. Non-Goals & Deferred Scope

These were considered and explicitly excluded:

- **Highlight & Comment System:** Deferred. Requires stable span IDs on rendered HTML, localStorage schema, and annotation UI. Medium-complexity frontend work that doesn't affect report quality.
- **Patient History Pairing:** Deferred. Requires a second LLM call flow, patient biodata schema, and a different synthesis prompt. The orchestrator architecture supports this as an add-on synthesis step but it is out of MVP scope.
- **UMLS / MeSH / RxNorm terminology normalization:** The query builder handles basic synonym expansion via Claude. Production-grade UMLS lookup requires an NLM license and local installation — too heavy for MVP.
- **EMA / international approval data:** OpenFDA is US/FDA only. EMA API is available but adds jurisdictional complexity. Explicitly flagged in report caveats.
- **DailyMed SPL integration:** Production-grade dosing source. Excluded because dosing is explicitly out of scope.
- **WHO ICTRP / Open Targets / ChEMBL:** Valuable but out of MVP scope — extend in Phase 3.
- **Persistent storage / report history:** In-memory only for MVP. SQLite migration would be a single-day add-on.

---

## 12. Implementation Plan

### Environment & Prerequisites

- [ ] Python 3.11+ installed
- [ ] `pip install flask anthropic requests python-dotenv` (Phase 1 core deps)
- [ ] `pip install weasyprint python-docx` (Phase 2 export deps)
- [ ] Obtain NCBI API key at https://www.ncbi.nlm.nih.gov/account/
- [ ] Obtain OpenFDA API key at https://open.fda.gov/apis/authentication/
- [ ] Obtain Anthropic API key and set up billing
- [ ] Create `.env` file with: `ANTHROPIC_API_KEY`, `NCBI_API_KEY`, `OPENFDA_API_KEY`
- [ ] Verify ClinicalTrials.gov v2 API is accessible (no key needed — sanity test)
- [ ] Verify Europe PMC API is accessible (no key needed — sanity test)

---

### Phase 1 — Core MVP

#### P1.1 Project Structure & Base
- [ ] Scaffold directory structure (`app.py`, `orchestrator.py`, `session.py`, `sources/`, `templates/`, `static/`)
- [ ] Implement `BaseSourceClient` with `_execute_with_retry` and `SourceResult` dataclass
- [ ] Implement `session.py` with thread-safe in-memory store
- [ ] Implement `app.py` with `POST /generate` and `GET /stream/<session_id>` SSE route
- [ ] Create basic `index.html` with condition input form and SSE consumer JS
- [ ] End-to-end smoke test: hit `/generate`, see SSE stream in browser dev tools

#### P1.2 Data Source Clients
- [ ] Implement `sources/clinicaltrials.py` — query, normalize, citation extraction
- [ ] Implement `sources/pubmed.py` — ESearch + EFetch two-step, preprint detection
- [ ] Implement `sources/openfda.py` — labels endpoint + drugsfda endpoint
- [ ] Implement `sources/europepmc.py` — core search, preprint filter
- [ ] Implement `sources/sec_edgar.py` — full-text search only (company name lookup deferred)
- [ ] Implement `sources/web_search.py` — Anthropic web search tool wrapper
- [ ] Unit test each client with a known condition (e.g., "Type 2 Diabetes") — verify normalized output schema
- [ ] Verify rate limiting and retry logic under simulated 429 responses

#### P1.3 Query Builder
- [ ] Implement `query_builder.py` — per-source query construction from condition string
- [ ] Implement MeSH synonym expansion via PubMed ESearch on iteration 2+
- [ ] Define query mutation strategy table (iteration 1–8 behavior per source)
- [ ] Test query builder output for 3 different conditions

#### P1.4 Research Loop
- [ ] Implement `research_loop.py` with `RawDataBundle` accumulator and parallel fetch via `ThreadPoolExecutor`
- [ ] Implement `_evaluate_sufficiency()` — Claude API call + `SufficiencyResult` parser
- [ ] Wire loop iteration counter and max_iterations guard
- [ ] Implement query refinement ingestion from `SufficiencyResult.refined_queries`
- [ ] Implement SSE event push for `loop_info` on each iteration
- [ ] Test loop with a poorly-named/obscure condition to verify retry behavior
- [ ] Test loop with a common condition to verify early termination at sufficient data

#### P1.5 Citation Registry
- [ ] Implement `citation_registry.py` — accumulate citations, deduplicate by identifier, assign sequential indices
- [ ] Implement citation validation: strip any identifier from Claude output not present in registry
- [ ] Test deduplication (same PMID from PubMed and Europe PMC should yield one citation entry)

#### P1.6 Report Builder & Synthesis
- [ ] Implement `report_builder.py` — partition raw data by report dimension, call Claude synthesis
- [ ] Write and test `SYNTHESIS_SYSTEM_PROMPT` and `SYNTHESIS_USER_PROMPT`
- [ ] Implement `ReportJSON` dataclass and parser for Claude's structured output
- [ ] Implement fallback if Claude response is malformed JSON — retry synthesis call once with stricter format instructions
- [ ] Test full synthesis with sample raw data from Phase 1.4 run

#### P1.7 HTML Renderer & Frontend
- [ ] Implement `html_renderer.py` — `ReportJSON` → HTML with superscript citation spans
- [ ] Create `report.html` template — four-section layout, references section, data quality flags panel
- [ ] Implement `GET /report/<session_id>` route — render full report HTML from session
- [ ] Wire SSE consumer in `report.js` to show section-by-section stream as report builds
- [ ] Add data integrity caveats footer to rendered report

#### P1.8 Data Integrity Layer
- [ ] Implement domain-tier scoring for web search sources
- [ ] Add `[WEB_ONLY, UNVERIFIED]` labeling pipeline
- [ ] Add `[PREPRINT]` flag propagation from source clients through to citation display
- [ ] Add jurisdiction label enforcement check — verify all approval claims include regulatory body name
- [ ] Manual test: run report for "semaglutide" — verify no dosing info appears, all approvals say "FDA-approved" not just "approved"

#### P1.9 Error Handling & Logging
- [ ] Add structured logging (Python `logging` module) to all source clients — log source, query, result count, latency
- [ ] Add global exception handler in orchestrator — catch per-source failures, push `error` SSE event, continue loop
- [ ] Add request timeout handling (15s per source call)
- [ ] Add graceful degradation: if 3+ sources fail in same iteration, push warning event but continue to synthesis with available data
- [ ] Test full pipeline with NCBI_API_KEY unset — verify graceful fallback to 3 req/sec mode
- [ ] Test with OpenFDA returning 500 — verify report still generates from other sources

---

### Phase 1 Integration & Manual QA

- [ ] Run full pipeline on 5 test conditions: "Type 2 Diabetes", "Non-Small Cell Lung Cancer", "Multiple Sclerosis", "Alzheimer's Disease", "Rare condition (Gaucher Disease)"
- [ ] Verify citation count > 10 per report
- [ ] Verify no hallucinated PMIDs or NCT numbers (cross-check 5 random citations per report against actual sources)
- [ ] Verify all reports include data quality flags section
- [ ] Verify loop terminates in ≤8 iterations for all test conditions
- [ ] Time report generation — target < 90 seconds end-to-end for common conditions

---

### Phase 2 — Chat Agent

- [ ] Write `CHAT_SYSTEM_PROMPT` and `CHAT_CONTEXT_PROMPT`
- [ ] Implement `POST /chat/<session_id>` route with full report JSON as context
- [ ] Build chat UI panel in `report.html` — fixed bottom bar, message history display
- [ ] Implement client-side chat history management in JS (no server storage)
- [ ] Test: ask 10 clarification questions about a generated report — verify answers are grounded in report content
- [ ] Test: ask question not in report — verify model acknowledges limitation vs hallucinating
- [ ] Test: inject a clinical advice request — verify refusal behavior

---

### Phase 2 — PDF/DOCX Export

- [ ] Install and verify WeasyPrint (known system dependency issues on some OS — test early)
- [ ] Create `templates/export_report.html` — print-optimized CSS, cover page, footnote citations
- [ ] Implement `build_docx()` in `report_builder.py` using python-docx
- [ ] Implement `POST /export/<session_id>` with `format=pdf|docx` param
- [ ] Add export buttons to `report.html` frontend
- [ ] Test PDF output — verify citations render, no truncated text, references section complete
- [ ] Test DOCX output — verify opens in Word/Google Docs without corruption
- [ ] Test export on a report with 30+ citations — verify pagination and footnote numbering

---

### Phase 2 Integration QA

- [ ] Full end-to-end test: generate report → chat 3 turns → export as PDF → export as DOCX
- [ ] Verify session remains valid for chat and export after report completion
- [ ] Test session expiry: verify 404 response for expired/unknown session_id on all routes
- [ ] Load test: 3 concurrent report generation requests — verify thread safety of session store

---

### Final Pre-Demo Checklist

- [ ] `.env.example` file created with all required keys and documentation
- [ ] All source client `MAX_RETRIES` and `RETRY_BACKOFF_BASE` values tuned based on QA results
- [ ] Report footer shows generation timestamp, loop iteration count, and data sources queried
- [ ] All 5 test condition reports reviewed for hallucinations and accuracy
- [ ] README with setup and run instructions in place
- [ ] Confirm no `print()` statements — all logging via `logging` module

---

*End of Document — MedBrief v1.0.0*