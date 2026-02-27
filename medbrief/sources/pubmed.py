import logging
import os
import time
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _api_key() -> str:
    return os.getenv("PUBMED_API_KEY", "")


def _rate_delay() -> float:
    return 0.11 if _api_key() else 0.35


def fetch(condition: str, extra_params: dict | None = None) -> list[dict]:
    """Two calls: reviews/guidelines + recent clinical trials."""
    results = []

    # Call 1: systematic reviews / guidelines
    pmids_soc = _esearch(
        f'({condition}[MeSH Terms] OR {condition}[Title]) AND '
        '(systematic review[pt] OR review[pt] OR guideline[pt])',
        retmax=10,
        extra=extra_params,
    )
    if pmids_soc:
        results.extend(_efetch(pmids_soc))

    # Call 2: recent clinical trials
    pmids_trials = _esearch(
        f'({condition}[MeSH Terms] OR {condition}[Title]) AND '
        '(clinical trial[pt] OR randomized controlled trial[pt])',
        retmax=10,
        min_year=2021,
        extra=extra_params,
    )
    if pmids_trials:
        results.extend(_efetch(pmids_trials))

    # Deduplicate by pmid
    seen = set()
    unique = []
    for r in results:
        if r["pmid"] not in seen:
            seen.add(r["pmid"])
            unique.append(r)
    return unique


def _esearch(term: str, retmax: int = 10, min_year: int = 2019,
             extra: dict | None = None) -> list[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
        "datetype": "pdat",
        "mindate": str(min_year),
        "maxdate": "3000",
    }
    if _api_key():
        params["api_key"] = _api_key()
    if extra:
        params.update(extra)

    try:
        time.sleep(_rate_delay())
        r = requests.get(f"{NCBI_BASE}esearch.fcgi", params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        logger.error("PubMed ESearch failed: %s", e)
        return []


def _efetch(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    }
    if _api_key():
        params["api_key"] = _api_key()

    try:
        time.sleep(_rate_delay())
        r = requests.get(f"{NCBI_BASE}efetch.fcgi", params=params, timeout=30)
        r.raise_for_status()
        return _parse_xml(r.text)
    except Exception as e:
        logger.error("PubMed EFetch failed: %s", e)
        return []


def _parse_xml(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("PubMed XML parse error: %s", e)
        return []

    results = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        title_el = article.find(".//ArticleTitle")
        abstract_el = article.find(".//AbstractText")
        journal_el = article.find(".//Title")
        year_el = article.find(".//PubDate/Year")
        doi_el = article.find(".//ArticleId[@IdType='doi']")

        pub_types = [
            pt.text for pt in article.findall(".//PublicationType") if pt.text
        ]
        is_preprint = "Preprint" in pub_types

        pmid = pmid_el.text if pmid_el is not None else None
        if not pmid:
            continue

        results.append({
            "source": "pubmed",
            "id": f"PMID:{pmid}",
            "pmid": pmid,
            "title": title_el.text if title_el is not None else "",
            "abstract": abstract_el.text if abstract_el is not None else "",
            "journal": journal_el.text if journal_el is not None else "",
            "year": year_el.text if year_el is not None else "",
            "doi": doi_el.text if doi_el is not None else "",
            "publication_types": pub_types,
            "is_preprint": is_preprint,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "retrieved_at": ts,
        })
    return results
