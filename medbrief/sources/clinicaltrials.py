import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

FIELDS = ",".join([
    "NCTId", "BriefTitle", "OverallStatus", "Phase",
    "Condition", "InterventionName", "InterventionType",
    "LeadSponsorName", "BriefSummary", "StartDate",
    "CompletionDate", "EnrollmentCount", "StudyType",
])


def fetch(condition: str, extra_params: dict | None = None) -> list[dict]:
    params = {
        "query.cond": condition,
        "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
        "query.term": "AREA[Phase](PHASE2 OR PHASE3 OR PHASE4)",
        "pageSize": 15,
        "format": "json",
        "fields": FIELDS,
    }
    if extra_params:
        params.update(extra_params)

    try:
        r = requests.get(BASE_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        studies = data.get("studies", [])
        return [_normalize(s) for s in studies]
    except requests.exceptions.HTTPError as e:
        logger.error("ClinicalTrials HTTP error: %s", e.response.status_code)
        return []
    except Exception as e:
        logger.error("ClinicalTrials fetch failed: %s", e)
        return []


def _normalize(study: dict) -> dict:
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status = proto.get("statusModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {})
    desc = proto.get("descriptionModule", {})
    design = proto.get("designModule", {})
    arms = proto.get("armsInterventionsModule", {})

    nct_id = ident.get("nctId", "")
    interventions = arms.get("interventions", [])
    intervention_name = interventions[0].get("name", "") if interventions else ""
    phases = design.get("phases", [])

    return {
        "source": "clinicaltrials",
        "id": nct_id,
        "title": ident.get("briefTitle", ""),
        "status": status.get("overallStatus", ""),
        "phase": phases[0] if phases else "",
        "intervention": intervention_name,
        "sponsor": sponsor.get("leadSponsor", {}).get("name", ""),
        "summary": desc.get("briefSummary", ""),
        "start_date": status.get("startDateStruct", {}).get("date", ""),
        "completion_date": status.get("completionDateStruct", {}).get("date", ""),
        "enrollment": design.get("enrollmentInfo", {}).get("count", 0),
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
