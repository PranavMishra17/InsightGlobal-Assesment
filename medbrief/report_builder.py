import json
import logging
import os
import re
import time
import uuid
from datetime import datetime

import anthropic

from citation_registry import CitationRegistry
from research_loop import RawDataBundle

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM = """You are a senior medical intelligence analyst producing structured briefings for health strategy teams (non-clinical audience). You write in clear, professional language appropriate for executives and strategists.

ABSOLUTE RULES — violations will invalidate the report:
1. Every factual claim requires a [SOURCE_N] citation marker referencing the provided data bundle.
2. Do not fabricate, hallucinate, or infer drug names, trial IDs, dates, or statistics not explicitly present in the provided data.
3. Mark all investigational/pipeline therapies explicitly as "investigational" or "in clinical development" — never as "approved" or "available."
4. Scope ALL approval claims to specific regulatory authority: write "FDA-approved" not "approved."
5. Flag preprint sources with [PREPRINT] after the citation.
6. Do not include any dosing information, dose ranges, or administration schedules.
7. If data on a dimension is genuinely sparse, state this explicitly rather than extrapolating or generalizing.
8. Respond ONLY with valid JSON conforming to the ReportJSON schema. No prose outside JSON."""

SYNTHESIS_USER = """Condition: {condition}

Data bundle (use ONLY sources below — do not add information not present here):

=== CLINICAL TRIALS ({trial_count} records) ===
{trials_text}

=== PUBMED ARTICLES ({pubmed_count} records) ===
{pubmed_text}

=== FDA DRUG DATA ({fda_count} records) ===
{fda_text}

=== WEB SEARCH RESULTS ===
{web_text}

=== CITATION INDEX (use these SOURCE_N numbers) ===
{citation_index}

Produce a JSON report with this exact schema:
{{
  "sections": {{
    "overview": {{
      "title": "Condition Overview",
      "content": "2-3 paragraph overview with [SOURCE_N] citations"
    }},
    "standard_of_care": {{
      "title": "Current Standard of Care",
      "content": "Narrative with citations",
      "approved_therapies": [
        {{
          "name": "Drug name",
          "drug_class": "Class",
          "regulatory_status": "FDA-approved",
          "approval_year": 2000,
          "citation_index": 1
        }}
      ]
    }},
    "emerging_treatments": {{
      "title": "Emerging Treatments in Development",
      "content": "Narrative with citations",
      "pipeline_items": [
        {{
          "name": "Trial or compound name",
          "nct_id": "NCT0XXXXXXXX",
          "phase": "Phase II",
          "sponsor": "...",
          "status": "RECRUITING",
          "citation_index": 2
        }}
      ]
    }},
    "key_players": {{
      "title": "Key Companies and Institutions",
      "content": "Narrative with citations",
      "entities": [
        {{
          "name": "Organization name",
          "type": "pharmaceutical",
          "role": "...",
          "citation_indices": [1, 2]
        }}
      ]
    }},
    "recent_developments": {{
      "title": "Recent Developments",
      "content": "Narrative covering latest findings and news with [SOURCE_N] citations"
    }}
  }}
}}"""


def build(bundle: RawDataBundle, registry: CitationRegistry, condition: str) -> dict:
    """
    Call Claude for synthesis, validate citations, return complete report dict.
    """
    client = anthropic.Anthropic(api_key=os.getenv("CALUDE_API_KEY"))

    # Build text representations for the prompt
    trials_text = _format_trials(bundle.trials)
    pubmed_text = _format_pubmed(bundle.pubmed)
    fda_text = _format_fda(bundle.fda)
    web_text = bundle.web[0]["content"] if bundle.web else "No web search results."
    citation_index = _format_citation_index(registry)

    user_msg = SYNTHESIS_USER.format(
        condition=condition,
        trial_count=len(bundle.trials),
        trials_text=trials_text,
        pubmed_count=len(bundle.pubmed),
        pubmed_text=pubmed_text,
        fda_count=len(bundle.fda),
        fda_text=fda_text,
        web_text=web_text,
        citation_index=citation_index,
    )

    synthesis_json = None
    for attempt in range(2):
        logger.debug("build: synthesis attempt %d — sending %d chars to Claude", attempt + 1, len(user_msg))
        t0 = time.time()
        try:
            with client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=16000,
                system=SYNTHESIS_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            ) as stream:
                text = stream.get_final_message().content[0].text.strip()

            logger.debug("build: Claude synthesis responded in %.1fs — %d chars", time.time() - t0, len(text))

            # Strip markdown fences
            if text.startswith("```"):
                parts = text.split("```")
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]

            synthesis_json = json.loads(text)
            logger.debug("build: JSON parsed OK, sections=%s", list(synthesis_json.get("sections", {}).keys()))
            break
        except json.JSONDecodeError as e:
            logger.error("Synthesis JSON parse failed after %.1fs (attempt %d): %s", time.time() - t0, attempt + 1, e)
            logger.debug("Synthesis raw text (first 500): %s", text[:500] if "text" in dir() else "N/A")
            if attempt == 0:
                user_msg = user_msg + "\n\nCRITICAL: Your previous response was not valid JSON. Respond ONLY with the JSON object, nothing else."
        except anthropic.APIError as e:
            logger.error("Anthropic API error during synthesis after %.1fs: %s", time.time() - t0, e)
            break
        except Exception as e:
            logger.error("Synthesis failed after %.1fs: %s", time.time() - t0, e)
            break

    if not synthesis_json:
        synthesis_json = _fallback_report(bundle, condition)

    # Validate + strip hallucinated citation indices
    valid_indices = {c["index"] for c in registry.all_citations()}
    synthesis_json = _validate_citations(synthesis_json, valid_indices)

    report = {
        "report_id": str(uuid.uuid4()),
        "condition": condition,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_sources_queried": _sources_used(bundle),
        "sections": synthesis_json.get("sections", {}),
        "references": registry.all_citations(),
        "report_caveats": [
            "Approval status reflects FDA (US) only — EMA/international status not covered.",
            "OpenFDA data is informational; verify critical approval details at DailyMed.",
            "Dosing information is excluded from this report.",
            "This report is for strategic intelligence purposes only — not clinical guidance.",
        ],
    }
    return report


# --- HTML rendering ---

def render_html(report: dict) -> str:
    sections = report.get("sections", {})
    refs = report.get("references", [])
    caveats = report.get("report_caveats", [])

    html_parts = []

    section_order = [
        "overview", "standard_of_care", "emerging_treatments",
        "key_players", "recent_developments",
    ]

    for key in section_order:
        sec = sections.get(key)
        if not sec:
            continue
        title = sec.get("title", key.replace("_", " ").title())
        content = _linkify_citations(sec.get("content", ""), refs)

        html_parts.append(f'<section id="{key}" class="report-section">')
        html_parts.append(f'<h2>{title}</h2>')
        html_parts.append(f'<p>{content}</p>')

        # Approved therapies table
        therapies = sec.get("approved_therapies", [])
        if therapies:
            html_parts.append('<h3>Approved Therapies</h3>')
            html_parts.append('<table class="data-table"><thead><tr>'
                              '<th>Drug</th><th>Class</th><th>Status</th><th>Year</th>'
                              '</tr></thead><tbody>')
            for t in therapies:
                idx = t.get("citation_index", "")
                sup = f'<sup><a href="#ref-{idx}">[{idx}]</a></sup>' if idx else ""
                html_parts.append(
                    f'<tr><td>{t.get("name","")}{sup}</td>'
                    f'<td>{t.get("drug_class","")}</td>'
                    f'<td>{t.get("regulatory_status","")}</td>'
                    f'<td>{t.get("approval_year","")}</td></tr>'
                )
            html_parts.append('</tbody></table>')

        # Pipeline items table
        pipeline = sec.get("pipeline_items", [])
        if pipeline:
            html_parts.append('<h3>Pipeline</h3>')
            html_parts.append('<table class="data-table"><thead><tr>'
                              '<th>Trial/Compound</th><th>Phase</th><th>Status</th><th>Sponsor</th>'
                              '</tr></thead><tbody>')
            for p in pipeline:
                idx = p.get("citation_index", "")
                sup = f'<sup><a href="#ref-{idx}">[{idx}]</a></sup>' if idx else ""
                nct = p.get("nct_id", "")
                nct_link = (f'<a href="https://clinicaltrials.gov/study/{nct}" target="_blank">{nct}</a>'
                            if nct else "")
                html_parts.append(
                    f'<tr><td>{p.get("name","")}{sup} {nct_link}</td>'
                    f'<td>{p.get("phase","")}</td>'
                    f'<td>{p.get("status","")}</td>'
                    f'<td>{p.get("sponsor","")}</td></tr>'
                )
            html_parts.append('</tbody></table>')

        # Key entities list
        entities = sec.get("entities", [])
        if entities:
            html_parts.append('<ul class="entity-list">')
            for e in entities:
                indices = e.get("citation_indices", [])
                sups = "".join(
                    f'<sup><a href="#ref-{i}">[{i}]</a></sup>' for i in indices
                )
                html_parts.append(
                    f'<li><strong>{e.get("name","")}</strong> '
                    f'({e.get("type","")}) — {e.get("role","")}{sups}</li>'
                )
            html_parts.append('</ul>')

        html_parts.append('</section>')

    # References
    if refs:
        html_parts.append('<section id="references" class="report-section">')
        html_parts.append('<h2>References</h2><ol class="references-list">')
        for ref in refs:
            idx = ref["index"]
            title = ref.get("title", "") or ref.get("identifier", "")
            url = ref.get("url", "")
            src = ref.get("source_type", "")
            pub_date = ref.get("pub_date", "")
            preprint = " [PREPRINT]" if ref.get("is_preprint") else ""
            jurisdiction = f" ({ref['jurisdiction']})" if ref.get("jurisdiction") else ""
            link = f'<a href="{url}" target="_blank">{title}</a>' if url else title
            html_parts.append(
                f'<li id="ref-{idx}">[{idx}] {link} — {src}{jurisdiction} {pub_date}{preprint}</li>'
            )
        html_parts.append('</ol></section>')

    # Caveats
    if caveats:
        html_parts.append('<section id="caveats" class="caveats">')
        html_parts.append('<h3>Data Caveats</h3><ul>')
        for c in caveats:
            html_parts.append(f'<li>{c}</li>')
        html_parts.append('</ul></section>')

    return "\n".join(html_parts)


# --- Helpers ---

def _linkify_citations(text: str, refs: list[dict]) -> str:
    """Replace [SOURCE_N] markers with superscript links."""
    def replace(m):
        n = m.group(1)
        return f'<sup><a href="#ref-{n}">[{n}]</a></sup>'
    return re.sub(r'\[SOURCE_(\d+)\]', replace, text)


def _format_trials(trials: list[dict]) -> str:
    if not trials:
        return "No clinical trial data retrieved."
    lines = []
    for t in trials:
        lines.append(
            f"[{t['id']}] {t['title']} | Phase: {t['phase']} | "
            f"Status: {t['status']} | Sponsor: {t['sponsor']} | "
            f"Intervention: {t['intervention']}\n"
            f"  Summary: {t['summary'][:300]}"
        )
    return "\n".join(lines)


def _format_pubmed(articles: list[dict]) -> str:
    if not articles:
        return "No PubMed data retrieved."
    lines = []
    for a in articles:
        preprint = " [PREPRINT]" if a.get("is_preprint") else ""
        lines.append(
            f"[{a['id']}] {a['title']} ({a['year']}) {a['journal']}{preprint}\n"
            f"  Abstract: {(a.get('abstract') or '')[:400]}"
        )
    return "\n".join(lines)


def _format_fda(items: list[dict]) -> str:
    if not items:
        return "No FDA data retrieved."
    lines = []
    for item in items:
        lines.append(
            f"[{item['id']}] {item.get('brand_name','')} ({item.get('generic_name','')}) "
            f"— {item.get('drug_class','')} | FDA | "
            f"Approval: {item.get('approval_date','unknown')} | "
            f"Manufacturer: {item.get('manufacturer','')}\n"
            f"  Indications: {item.get('indications_summary','')[:300]}"
        )
    return "\n".join(lines)


def _format_citation_index(registry: CitationRegistry) -> str:
    lines = []
    for c in registry.all_citations():
        lines.append(
            f"SOURCE_{c['index']}: [{c['identifier']}] {c['title']} "
            f"({c['source_type']})"
        )
    return "\n".join(lines) or "No citations registered."


def _validate_citations(report: dict, valid_indices: set) -> dict:
    """Walk report JSON and strip any SOURCE_N references not in registry."""
    text = json.dumps(report)

    def replace(m):
        n = int(m.group(1))
        if n in valid_indices:
            return m.group(0)
        logger.warning("Removing hallucinated citation SOURCE_%d", n)
        return "[CITATION REMOVED]"

    cleaned = re.sub(r'\[SOURCE_(\d+)\]', replace, text)

    # Also strip numeric citation_index fields that are invalid
    def fix_index(m):
        n = int(m.group(1))
        return m.group(0) if n in valid_indices else '"citation_index": null'

    cleaned = re.sub(r'"citation_index":\s*(\d+)', fix_index, cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return report


def _sources_used(bundle: RawDataBundle) -> list[str]:
    sources = []
    if bundle.trials:
        sources.append("clinicaltrials")
    if bundle.pubmed:
        sources.append("pubmed")
    if bundle.fda:
        sources.append("openfda")
    if bundle.web:
        sources.append("web_search")
    return sources


def _fallback_report(bundle: RawDataBundle, condition: str) -> dict:
    """Minimal fallback if Claude synthesis fails."""
    return {
        "sections": {
            "overview": {
                "title": "Condition Overview",
                "content": f"Report generation encountered an error for condition: {condition}. Raw data was retrieved but could not be synthesized.",
            },
            "standard_of_care": {"title": "Current Standard of Care", "content": "See references.", "approved_therapies": []},
            "emerging_treatments": {"title": "Emerging Treatments", "content": "See references.", "pipeline_items": []},
            "key_players": {"title": "Key Players", "content": "See references.", "entities": []},
            "recent_developments": {"title": "Recent Developments", "content": "See references."},
        }
    }
