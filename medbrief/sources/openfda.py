import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

OPENFDA_BASE = "https://api.fda.gov/"


def _get(endpoint: str, params: dict) -> dict:
    api_key = os.getenv("OPENFDA_API_KEY", "")
    if api_key:
        params["api_key"] = api_key
    try:
        r = requests.get(f"{OPENFDA_BASE}{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning("OpenFDA no results for %s", endpoint)
            return {"results": []}
        logger.error("OpenFDA HTTP error %s: %s", e.response.status_code, e)
        return {"results": []}
    except Exception as e:
        logger.error("OpenFDA request failed: %s", e)
        return {"results": []}


def fetch(condition: str, extra_params: dict | None = None) -> list[dict]:
    results = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Call 1: drug labels — indications
    label_data = _get("drug/label.json", {
        "search": f'indications_and_usage:"{condition}"',
        "limit": 8,
    })
    for item in label_data.get("results", []):
        openfda = item.get("openfda", {})
        brand = openfda.get("brand_name", [""])[0]
        generic = openfda.get("generic_name", [""])[0]
        manufacturer = openfda.get("manufacturer_name", [""])[0]
        pharm_class = openfda.get("pharm_class_epc", [""])[0]
        indications = item.get("indications_and_usage", [""])[0][:500]

        if not (brand or generic):
            continue

        results.append({
            "source": "openfda",
            "endpoint": "label",
            "id": f"LABEL:{brand or generic}",
            "brand_name": brand,
            "generic_name": generic,
            "drug_class": pharm_class,
            "manufacturer": manufacturer,
            "indications_summary": indications,
            "jurisdiction": "FDA",
            "url": "",
            "retrieved_at": ts,
        })

    # Call 2: drugsfda — approval history
    drugsfda_data = _get("drug/drugsfda.json", {
        "search": f'openfda.generic_name:"{condition}"+AND+submissions.submission_status:"AP"',
        "limit": 5,
    })
    for item in drugsfda_data.get("results", []):
        openfda = item.get("openfda", {})
        brand = openfda.get("brand_name", [""])[0]
        generic = openfda.get("generic_name", [""])[0]
        manufacturer = openfda.get("manufacturer_name", [""])[0]
        submissions = item.get("submissions", [])
        approved = [s for s in submissions if s.get("submission_status") == "AP"]
        approval_date = approved[0].get("submission_status_date", "") if approved else ""
        sub_type = approved[0].get("submission_type", "") if approved else ""
        app_number = item.get("application_number", "")

        if not (brand or generic):
            continue

        results.append({
            "source": "openfda",
            "endpoint": "drugsfda",
            "id": app_number or f"NDA:{brand or generic}",
            "brand_name": brand,
            "generic_name": generic,
            "manufacturer": manufacturer,
            "approval_date": approval_date,
            "submission_type": sub_type,
            "jurisdiction": "FDA",
            "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={app_number.replace('NDA', '').replace('BLA', '')}",
            "retrieved_at": ts,
        })

    return results
