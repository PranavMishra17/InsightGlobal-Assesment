# MedBrief — Data Sources Reference
**Verified:** 2026-02-26 | All endpoints live-checked via curl

---

## Live Test Results (2026-02-26)

| Source | Status | Response | Gotchas |
|---|---|---|---|
| ClinicalTrials.gov v2 | **200** | 874 recruiting T2D trials | Must add `countTotal=true` to get `totalCount` field |
| PubMed ESearch | **200** | 197,145 results for T2D MeSH | URL brackets must be percent-encoded in shell: `[` → `%5B`, `]` → `%5D` |
| OpenFDA drug/label | **200** | 3,704 matching labels | First result: Glimepiride. 404 returns empty results not an error — handle gracefully |
| Europe PMC | **200** | 1,496,880 hits | No auth needed. `synonym=false` recommended for precision |
| SEC EDGAR | **200** (with header) | 219 10-K filings | Returns **403 without `User-Agent` header**. Response fields differ from docs — see section 5 |

---

## 1. ClinicalTrials.gov API v2

**Official docs:** https://clinicaltrials.gov/data-api/api
**OpenAPI spec:** https://clinicaltrials.gov/api/v2/swagger-docs
**Auth:** None required
**Rate limit:** ~50 req/min (unofficial — be conservative)
**Note:** Classic API retired June 2024. v2 only.
**Live verified:** 200 OK — 874 recruiting T2D trials as of 2026-02-26
**Gotcha:** `totalCount` is only present in the response when `countTotal=true` is passed.

### Endpoints

| Method | URL | Purpose |
|---|---|---|
| GET | `https://clinicaltrials.gov/api/v2/studies` | Search studies |
| GET | `https://clinicaltrials.gov/api/v2/studies/{nctId}` | Single study by NCT ID |
| GET | `https://clinicaltrials.gov/api/v2/studies/{nctId}/history` | Version history |
| GET | `https://clinicaltrials.gov/api/v2/stats/size` | DB stats |
| GET | `https://clinicaltrials.gov/api/v2/version` | API version info |

### Query Parameters (GET /studies)

| Param | Type | Values / Notes |
|---|---|---|
| `query.cond` | string | Condition/disease — primary filter |
| `query.term` | string | General keyword fallback |
| `query.intr` | string | Intervention/drug name |
| `query.spons` | string | Sponsor organization |
| `query.locn` | string | Location |
| `filter.overallStatus` | enum (CSV) | `RECRUITING`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `NOT_YET_RECRUITING` |
| `filter.phase` | enum (CSV) | `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4`, `EARLY_PHASE1`, `NA` |
| `filter.ids` | string (CSV) | NCT ID list |
| `sort` | string | `LastUpdatePostDate:desc`, `EnrollmentCount:desc` |
| `pageSize` | int | 1–1000, default 10 |
| `pageToken` | string | Pagination cursor from previous response |
| `format` | enum | `json` (default), `csv` |
| `countTotal` | bool | Include `totalCount` in response |
| `fields` | CSV | Specific fields to return — see below |

### Response Structure

```json
{
  "totalCount": 12483,
  "nextPageToken": "NF0g...",
  "studies": [
    {
      "protocolSection": {
        "identificationModule": {
          "nctId": "NCT05XXXXXXX",
          "briefTitle": "...",
          "officialTitle": "..."
        },
        "statusModule": {
          "overallStatus": "RECRUITING",
          "startDateStruct": { "date": "2023-06" },
          "completionDateStruct": { "date": "2025-12" }
        },
        "sponsorCollaboratorsModule": {
          "leadSponsor": { "name": "Novo Nordisk A/S", "class": "INDUSTRY" },
          "collaborators": []
        },
        "descriptionModule": { "briefSummary": "..." },
        "conditionsModule": { "conditions": ["Type 2 Diabetes Mellitus"] },
        "designModule": {
          "phases": ["PHASE3"],
          "enrollmentInfo": { "count": 450 }
        },
        "armsInterventionsModule": {
          "interventions": [
            { "type": "DRUG", "name": "Semaglutide", "description": "..." }
          ]
        }
      }
    }
  ]
}
```

### Python Snippet

```python
import requests
import time
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

FIELDS = ",".join([
    "NCTId", "BriefTitle", "OverallStatus", "Phase",
    "Condition", "InterventionName", "InterventionType",
    "LeadSponsorName", "CollaboratorName", "BriefSummary",
    "StartDate", "CompletionDate", "EnrollmentCount",
    "PrimaryOutcomeMeasure", "StudyType"
])

def fetch_trials(condition: str, status: str = "RECRUITING,ACTIVE_NOT_RECRUITING",
                 phase: str = "PHASE2,PHASE3,PHASE4", page_size: int = 20) -> dict:
    params = {
        "query.cond": condition,
        "filter.overallStatus": status,
        "filter.phase": phase,
        "pageSize": page_size,
        "format": "json",
        "countTotal": "true",
        "fields": FIELDS
    }
    try:
        response = requests.get(BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"ClinicalTrials HTTP error: {e.response.status_code}")
        raise
    except requests.exceptions.Timeout:
        logger.error("ClinicalTrials request timed out")
        raise

def fetch_trial_by_nct(nct_id: str) -> dict:
    try:
        response = requests.get(f"{BASE_URL}/{nct_id}", timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"ClinicalTrials single study fetch failed: {e}")
        raise

# Access nested fields:
# study["protocolSection"]["identificationModule"]["nctId"]
# study["protocolSection"]["statusModule"]["overallStatus"]
# study["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]["name"]
```

---

## 2. PubMed / NCBI E-Utilities

**Official docs:** https://www.ncbi.nlm.nih.gov/books/NBK25497/
**Quick start:** https://www.ncbi.nlm.nih.gov/books/NBK25500/
**In-depth params:** https://www.ncbi.nlm.nih.gov/books/NBK25499/
**API key signup:** https://www.ncbi.nlm.nih.gov/account/
**Auth:** Optional key in `.env` as `NCBI_API_KEY`
**Rate limit:** 3 req/sec without key, 10 req/sec with key
**Live verified:** 200 OK — 197,145 T2D articles as of 2026-02-26
**Gotcha:** Square brackets in `[MeSH Terms]` and `[pt]` must be percent-encoded (`%5B`, `%5D`) when constructing URLs in shell/curl. The `requests` library handles this automatically via `params=`.

### Endpoints (Base: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`)

| Utility | URL suffix | Purpose |
|---|---|---|
| ESearch | `esearch.fcgi` | Search — returns PMIDs |
| EFetch | `efetch.fcgi` | Fetch full records by ID |
| ESummary | `esummary.fcgi` | Fetch summaries by ID |
| EInfo | `einfo.fcgi` | DB metadata, field list |
| ELink | `elink.fcgi` | Related records across databases |
| ESpell | `espell.fcgi` | Spelling suggestions |

### ESearch Parameters

| Param | Notes |
|---|---|
| `db` | Database — use `pubmed` |
| `term` | Query string — supports field tags like `[MeSH Terms]`, `[Title]`, `[pt]` |
| `retmax` | Max results returned (default 20, max 10000) |
| `retstart` | Offset for pagination |
| `retmode` | `json` or `xml` |
| `sort` | `relevance` (default), `pub+date`, `JournalName` |
| `datetype` | `pdat` (publication date), `edat` (Entrez date) |
| `mindate` / `maxdate` | Date range in `YYYY/MM/DD` format |
| `usehistory` | `y` — stores results on server for chained EFetch |
| `api_key` | NCBI API key |

### Publication Type Field Tags (for `[pt]` filter)

```
systematic review[pt]
review[pt]
guideline[pt]
clinical trial[pt]
randomized controlled trial[pt]
meta-analysis[pt]
```

### EFetch Parameters

| Param | Notes |
|---|---|
| `db` | `pubmed` |
| `id` | Comma-separated PMIDs |
| `rettype` | `abstract`, `medline`, `xml`, `uilist` |
| `retmode` | `json`, `xml`, `text` |
| `api_key` | NCBI API key |

### Python Snippet

```python
import requests
import time
import logging
import os
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
API_KEY = os.getenv("NCBI_API_KEY", "")
RATE_LIMIT_DELAY = 0.11 if API_KEY else 0.34  # 9/sec with key, 2.9/sec without

def esearch(query: str, db: str = "pubmed", retmax: int = 20,
            min_year: int = 2020, pub_type_filter: str = "") -> list[str]:
    term = query
    if pub_type_filter:
        term = f"({query}) AND {pub_type_filter}"
    
    params = {
        "db": db,
        "term": term,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
        "datetype": "pdat",
        "mindate": str(min_year),
        "maxdate": "3000",
    }
    if API_KEY:
        params["api_key"] = API_KEY

    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(f"{NCBI_BASE}esearch.fcgi", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        logger.error(f"PubMed ESearch failed: {e}")
        raise

def efetch_abstracts(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    }
    if API_KEY:
        params["api_key"] = API_KEY

    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(f"{NCBI_BASE}efetch.fcgi", params=params, timeout=30)
        r.raise_for_status()
        return _parse_pubmed_xml(r.text)
    except Exception as e:
        logger.error(f"PubMed EFetch failed: {e}")
        raise

def _parse_pubmed_xml(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    results = []
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        title_el = article.find(".//ArticleTitle")
        abstract_el = article.find(".//AbstractText")
        journal_el = article.find(".//Title")  # journal title
        year_el = article.find(".//PubDate/Year")
        doi_el = article.find(".//ArticleId[@IdType='doi']")
        
        pub_types = [
            pt.text for pt in article.findall(".//PublicationType")
            if pt.text
        ]
        is_preprint = "Preprint" in pub_types

        results.append({
            "pmid": pmid_el.text if pmid_el is not None else None,
            "title": title_el.text if title_el is not None else None,
            "abstract": abstract_el.text if abstract_el is not None else None,
            "journal": journal_el.text if journal_el is not None else None,
            "year": year_el.text if year_el is not None else None,
            "doi": doi_el.text if doi_el is not None else None,
            "publication_types": pub_types,
            "is_preprint": is_preprint,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid_el.text}/" if pmid_el is not None else None,
        })
    return results

# Standard of Care query:
# esearch("type 2 diabetes[MeSH Terms]", pub_type_filter="systematic review[pt] OR review[pt]")
# Emerging treatments query:
# esearch("type 2 diabetes treatment", pub_type_filter="clinical trial[pt] OR randomized controlled trial[pt]")
```

---

## 3. OpenFDA

**Official docs:** https://open.fda.gov/apis/
**Query syntax:** https://open.fda.gov/apis/query-syntax/
**Auth:** Free API key required — https://open.fda.gov/apis/authentication/
**Store as:** `OPENFDA_API_KEY` in `.env`
**Rate limit:** 240 req/min, 120,000/day with key | 240 req/min, 1,000/day without
**Live verified:** 200 OK — 3,704 label records matching "type 2 diabetes" as of 2026-02-26
**Gotcha:** A query with no matches returns HTTP 404 (not an empty 200). The Python snippet already handles this — returns `{"results": [], "meta": {"results": {"total": 0}}}` on 404.

### Endpoints Used (Base: `https://api.fda.gov/`)

| Endpoint | URL | What it returns |
|---|---|---|
| Drug Labels (SPL) | `drug/label.json` | Prescribing info, indications, contraindications |
| Drugs@FDA | `drug/drugsfda.json` | Approval history, NDA/BLA numbers, sponsors |
| Adverse Events (FAERS) | `drug/event.json` | Adverse event reports |
| Drug Enforcement | `drug/enforcement.json` | Recall data |

### Query Syntax

```
search=field:value                         # exact field match
search=field:"multi word phrase"           # phrase match (quotes)
search=field1:val+AND+field2:val           # AND
search=field1:val+field2:val               # OR (implicit)
count=field.exact                          # aggregate counts
limit=N                                    # max 1000 per call
skip=N                                     # pagination offset
```

### Key Searchable Fields

**drug/label.json:**
- `indications_and_usage` — free text indications
- `openfda.brand_name` — brand name
- `openfda.generic_name` — generic name
- `openfda.pharm_class_epc` — pharmacologic class
- `openfda.manufacturer_name`

**drug/drugsfda.json:**
- `openfda.generic_name`
- `openfda.brand_name`
- `openfda.manufacturer_name`
- `products.marketing_status` — `Prescription`, `OTC`, `Discontinued`
- `submissions.submission_type` — `NDA`, `BLA`, `ANDA`
- `submissions.submission_status` — `AP` (approved)

**drug/event.json:**
- `patient.drug.openfda.generic_name`
- `patient.reaction.reactionmeddrapt` — adverse reaction term
- `receivedate` — date received by FDA

### Python Snippet

```python
import requests
import logging
import os

logger = logging.getLogger(__name__)

OPENFDA_BASE = "https://api.fda.gov/"
API_KEY = os.getenv("OPENFDA_API_KEY", "")

def _get(endpoint: str, params: dict) -> dict:
    if API_KEY:
        params["api_key"] = API_KEY
    try:
        r = requests.get(f"{OPENFDA_BASE}{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"OpenFDA no results for {endpoint}: {params.get('search','')}")
            return {"results": [], "meta": {"results": {"total": 0}}}
        logger.error(f"OpenFDA HTTP error {e.response.status_code}: {e}")
        raise
    except Exception as e:
        logger.error(f"OpenFDA request failed: {e}")
        raise

def fetch_drug_labels(condition: str, limit: int = 10) -> dict:
    # Search indications text for condition mentions
    return _get("drug/label.json", {
        "search": f'indications_and_usage:"{condition}"',
        "limit": limit
    })

def fetch_approved_drugs(generic_name: str, limit: int = 10) -> dict:
    # Look up approved drugs by generic name
    return _get("drug/drugsfda.json", {
        "search": f'openfda.generic_name:"{generic_name}"'
                  f'+AND+submissions.submission_status:"AP"',
        "limit": limit
    })

def fetch_adverse_events_count(generic_name: str, limit: int = 10) -> dict:
    # Top adverse reactions for a drug — returns count aggregate
    return _get("drug/event.json", {
        "search": f'patient.drug.openfda.generic_name:"{generic_name}"',
        "count": "patient.reaction.reactionmeddrapt.exact",
        "limit": limit
    })

def fetch_drug_class(pharm_class: str, limit: int = 10) -> dict:
    # All approved drugs in a pharmacologic class
    return _get("drug/drugsfda.json", {
        "search": f'openfda.pharm_class_epc:"{pharm_class}"',
        "limit": limit
    })

# Response structure for drugsfda:
# data["results"][0]["openfda"]["brand_name"][0]
# data["results"][0]["openfda"]["generic_name"][0]
# data["results"][0]["openfda"]["manufacturer_name"][0]
# data["results"][0]["submissions"][0]["submission_type"]       # NDA, BLA
# data["results"][0]["submissions"][0]["submission_status"]     # AP = approved
# data["results"][0]["submissions"][0]["submission_status_date"]

# IMPORTANT: Always label jurisdiction as FDA (US) only
# Do NOT include dosing info from label endpoint
```

---

## 4. Europe PMC

**Official docs:** https://europepmc.org/RestfulWebService
**Developers page:** https://europepmc.org/developers
**Auth:** None required
**Rate limit:** Not officially published — use 3 req/sec conservatively
**Live verified:** 200 OK — 1,496,880 T2D hits as of 2026-02-26. First result: "Oral small-molecule GLP-1 receptor agonist for type 2 diabetes and obesity"

### Endpoints (Base: `https://www.ebi.ac.uk/europepmc/webservices/rest/`)

| Method | URL | Purpose |
|---|---|---|
| GET | `/search` | Full-text + metadata search |
| GET | `/article/{source}/{id}` | Single article by ID |
| GET | `/article/{source}/{id}/references` | References list |
| GET | `/article/{source}/{id}/citations` | Citing articles |
| GET | `/article/{source}/{id}/fullTextXML` | Full XML (OA only) |
| GET | `/fields` | List all searchable fields |
| GET | (annotations) | See annotations sub-API below |

### Search Parameters

| Param | Values | Notes |
|---|---|---|
| `query` | string | Lucene query syntax (see operators below) |
| `format` | `json`, `xml`, `dc` | Always use `json` |
| `resultType` | `lite`, `core`, `idlist` | `core` returns full metadata incl. abstract |
| `pageSize` | int | Max 1000 |
| `cursorMark` | string | Pagination — start with `*`, use `nextCursorMark` from response |
| `sort` | string | `CITED desc`, `P_PDATE_D desc` (pub date), `RELEVANCE` |
| `synonym` | `true`/`false` | MeSH synonym expansion (default true — set false for precision) |

### Query Operators

```
"type 2 diabetes"              # exact phrase
TITLE:"diabetes"               # field-specific
ABSTRACT:"SGLT2 inhibitor"    # abstract search
AUTH:"Zimmet P"                # author
JOURNAL:"New England Journal"  # journal
PUB_YEAR:2023                  # exact year
PUB_YEAR:[2020 TO 2024]        # year range
SRC:MED                        # MEDLINE/PubMed only
SRC:PMC                        # PubMed Central only
SRC:PPR                        # preprints only
NOT SRC:PPR                    # exclude preprints
OPEN_ACCESS:Y                  # open access only
HAS_FULLTEXT:Y                 # has full text in Europe PMC
HAS_ABSTRACT:Y                 # has abstract
CITATION_COUNT:[50 TO *]       # min 50 citations
```

### Response Structure (resultType=core)

```json
{
  "hitCount": 4821,
  "nextCursorMark": "AoE=...",
  "resultList": {
    "result": [
      {
        "id": "36754560",
        "source": "MED",
        "pmid": "36754560",
        "pmcid": "PMC9876543",
        "doi": "10.1016/S0140-6736(22)02429-X",
        "title": "...",
        "authorString": "Zimmet P, Alberti KG, ...",
        "journalTitle": "Lancet",
        "pubYear": "2023",
        "abstractText": "...",
        "citedByCount": 147,
        "isOpenAccess": "Y",
        "inEPMC": "Y",
        "inPMC": "N",
        "pubTypeList": {
          "pubType": ["review-article", "journal-article"]
        },
        "fullTextUrlList": {
          "fullTextUrl": [
            {
              "url": "https://europepmc.org/article/MED/36754560",
              "documentStyle": "html",
              "availabilityCode": "OA"
            }
          ]
        }
      }
    ]
  }
}
```

### Python Snippet

```python
import requests
import logging
import time

logger = logging.getLogger(__name__)

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
RATE_LIMIT_DELAY = 0.35  # conservative ~3 req/sec

def search_europepmc(query: str, result_type: str = "core",
                     page_size: int = 20, sort: str = "CITED desc",
                     exclude_preprints: bool = True) -> dict:
    if exclude_preprints:
        query = f"({query}) NOT SRC:PPR"

    params = {
        "query": query,
        "format": "json",
        "resultType": result_type,
        "pageSize": page_size,
        "cursorMark": "*",
        "sort": sort,
        "synonym": "false",  # disable MeSH expansion for precision
    }

    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(f"{EPMC_BASE}/search", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("resultList", {}).get("result", [])
        return {
            "total": data.get("hitCount", 0),
            "results": results,
            "next_cursor": data.get("nextCursorMark")
        }
    except requests.exceptions.HTTPError as e:
        logger.error(f"Europe PMC HTTP error {e.response.status_code}: {e}")
        raise
    except Exception as e:
        logger.error(f"Europe PMC request failed: {e}")
        raise

def is_preprint(result: dict) -> bool:
    pub_types = result.get("pubTypeList", {}).get("pubType", [])
    if isinstance(pub_types, str):
        pub_types = [pub_types]
    return result.get("source") == "PPR" or "preprint" in [p.lower() for p in pub_types]

# Standard queries:
# search_europepmc('"type 2 diabetes" AND (treatment OR therapy) AND PUB_YEAR:[2020 TO 2026]')
# search_europepmc('"type 2 diabetes" OPEN_ACCESS:Y', sort="CITED desc")
# search_europepmc('"type 2 diabetes" SRC:MED', result_type="lite")  # fast metadata only
```

---

## 5. SEC EDGAR

**Official API docs:** https://www.sec.gov/search-filings/edgar-application-programming-interfaces
**Full-text search UI:** https://efts.sec.gov/LATEST/search-index
**EDGAR search:** https://www.sec.gov/edgar/search/
**Auth:** None required for data.sec.gov and efts.sec.gov
**Rate limit:** 10 req/sec max (SEC policy — use User-Agent header)
**Live verified:** 200 OK (with User-Agent) — 219 10-K filings mentioning "type 2 diabetes" (2024–2026) as of 2026-02-26
**Gotcha:** Returns **403 Forbidden** if `User-Agent` header is absent. Always include it.
**Gotcha:** Response field names differ from SEC documentation. Verified live field names below.

### Endpoints

| Method | URL | Purpose |
|---|---|---|
| GET | `https://efts.sec.gov/LATEST/search-index?q=...` | Full-text search across all filings since 2001 |
| GET | `https://data.sec.gov/submissions/CIK{cik}.json` | All filings for a company by CIK |
| GET | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` | XBRL financial data |
| GET | `https://efts.sec.gov/LATEST/search-index?q=...&entity=...` | Search with company filter |
| GET | `https://www.sec.gov/cgi-bin/browse-edgar?company=...&action=getcompany&output=atom` | Company name → CIK lookup |

### Full-Text Search Parameters (`efts.sec.gov/LATEST/search-index`)

| Param | Notes |
|---|---|
| `q` | Query string — Boolean operators supported (AND implicit, OR, NOT, `"exact phrase"`, wildcards `*`) |
| `dateRange` | `custom` |
| `startdt` | `YYYY-MM-DD` |
| `enddt` | `YYYY-MM-DD` |
| `forms` | Comma-separated form types: `10-K`, `10-Q`, `8-K`, `20-F` |
| `entity` | Company name filter |
| `_source` | Fields to return (live-verified names): `period_ending,display_names,file_date,form,adsh,ciks` |

### Python Snippet

```python
import requests
import logging
import time
import re

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{}.json"
EDGAR_COMPANY_SEARCH = "https://www.sec.gov/cgi-bin/browse-edgar"

# SEC requires a User-Agent header identifying your app
HEADERS = {
    "User-Agent": "MedBrief/1.0 contact@yourorg.com",
    "Accept-Encoding": "gzip, deflate",
}

def search_filings(query: str, start_date: str = "2023-01-01",
                   end_date: str = "2026-12-31",
                   forms: str = "10-K,10-Q") -> dict:
    params = {
        "q": f'"{query}"',
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
        "forms": forms,
    }
    try:
        time.sleep(0.11)  # ~9 req/sec — well under 10 limit
        r = requests.get(EDGAR_SEARCH_URL, params=params,
                         headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"EDGAR full-text search failed: {e}")
        raise

def get_company_submissions(cik: str) -> dict:
    # CIK must be zero-padded to 10 digits
    cik_padded = cik.zfill(10)
    url = EDGAR_SUBMISSIONS_URL.format(cik_padded)
    try:
        time.sleep(0.11)
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"EDGAR submissions fetch failed for CIK {cik}: {e}")
        raise

def company_name_to_cik(company_name: str) -> str | None:
    params = {
        "company": company_name,
        "action": "getcompany",
        "type": "10-K",
        "dateb": "",
        "owner": "include",
        "count": "5",
        "search_text": "",
        "output": "atom"
    }
    try:
        time.sleep(0.11)
        r = requests.get(EDGAR_COMPANY_SEARCH, params=params,
                         headers=HEADERS, timeout=15)
        r.raise_for_status()
        # Extract CIK from Atom XML response
        match = re.search(r'CIK=(\d+)', r.text)
        return match.group(1) if match else None
    except Exception as e:
        logger.warning(f"EDGAR CIK lookup failed for '{company_name}': {e}")
        return None

# EDGAR search response structure:
# data["hits"]["hits"][0]["_source"]["display_names"]  # list: ["Novo Nordisk A/S (NVO) (CIK 0001341439)"]
# data["hits"]["hits"][0]["_source"]["file_date"]
# data["hits"]["hits"][0]["_source"]["form"]           # form type (not "form_type")
# data["hits"]["hits"][0]["_source"]["period_ending"]  # period end date (not "period_of_report")
# data["hits"]["hits"][0]["_source"]["adsh"]           # accession number → filing URL
# NOTE: field is "display_names" (list), NOT "entity_name" — live-verified 2026-02-26
#
# Filing URL pattern:
# https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_nodashes}/{filename}

# NOTE: EDGAR is only queried AFTER company names are extracted
# from ClinicalTrials sponsors and OpenFDA drug sponsors.
# Do NOT query EDGAR cold with only a condition name.
```

---

## 6. Anthropic Web Search (Claude Tool)

**Docs:** https://docs.anthropic.com/en/docs/build-with-claude/tool-use  
**Auth:** Anthropic API key (`ANTHROPIC_API_KEY`)  
**Model:** `claude-opus-4-6`  
**No additional key needed** — web search is a built-in tool

### Tool Definition

```python
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search"
}
```

### Python Snippet

```python
import anthropic
import logging
import os

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def web_search_via_claude(queries: list[str], context: str = "") -> dict:
    query_list = "\n".join(f"- {q}" for q in queries)
    
    try:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=(
                "You are a medical research assistant. "
                "Search for information using the provided queries. "
                "Return only factual information with source URLs. "
                "Prefer .gov, .edu, journal domains over aggregator sites. "
                "Ignore WebMD, Healthline, and similar patient portals. "
                "Always include publication dates when available."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Context: {context}\n\n"
                    f"Please search for the following:\n{query_list}\n\n"
                    "For each result, provide: source URL, publication date, "
                    "key finding, and domain tier (gov/edu/journal = tier1, "
                    "hospital/institution = tier2, other = tier3)."
                )
            }]
        )

        # Extract text content from response — may include tool_use blocks
        text_blocks = [
            block.text for block in response.content
            if hasattr(block, "text")
        ]
        return {
            "content": "\n".join(text_blocks),
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            }
        }
    except anthropic.APIError as e:
        logger.error(f"Anthropic web search failed: {e.status_code} {e.message}")
        raise
    except Exception as e:
        logger.error(f"Web search request failed: {e}")
        raise

# Domain tier priority for citation trust:
# Tier 1: nih.gov, cdc.gov, who.int, nejm.org, thelancet.com, .edu journals
# Tier 2: hospital systems, academic medical centers, pharma press offices
# Tier 3: WebMD, Healthline, Mayo patient pages, SEO health portals
#
# Tier 3 results can inform context but never back factual claims.
```

---

## Environment Setup

```bash
# .env.example
ANTHROPIC_API_KEY=sk-ant-...
NCBI_API_KEY=...          # https://www.ncbi.nlm.nih.gov/account/
OPENFDA_API_KEY=...       # https://open.fda.gov/apis/authentication/

# No key needed for:
# ClinicalTrials.gov v2
# Europe PMC
# SEC EDGAR (data.sec.gov + efts.sec.gov)
```

```bash
pip install requests anthropic python-dotenv
```

---

## Quick Sanity Test URLs (open in browser to verify live)

```
# ClinicalTrials.gov v2
https://clinicaltrials.gov/api/v2/studies?query.cond=type+2+diabetes&filter.overallStatus=RECRUITING&pageSize=2&format=json&countTotal=true

# PubMed ESearch
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=type+2+diabetes[MeSH+Terms]&retmax=5&retmode=json

# OpenFDA labels
https://api.fda.gov/drug/label.json?search=indications_and_usage:%22type+2+diabetes%22&limit=2

# Europe PMC
https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=type+2+diabetes&format=json&resultType=lite&pageSize=2

# SEC EDGAR full-text (403 in browser without User-Agent — use curl or requests with header)
# curl -H "User-Agent: MedBrief/1.0 contact@yourorg.com" "https://efts.sec.gov/LATEST/search-index?q=%22type+2+diabetes%22&forms=10-K&dateRange=custom&startdt=2024-01-01&enddt=2026-01-01"
```