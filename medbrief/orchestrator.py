import json
import logging
import os
import re
import time

from citation_registry import CitationRegistry
from report_builder import build, render_html
from research_loop import ResearchLoop

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def _save_report_to_disk(report: dict, condition: str) -> str:
    """Save report JSON to reports/ directory. Returns the file path."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", condition.lower()).strip("_")[:40]
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{ts}.json"
    path = os.path.join(REPORTS_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info("Report saved to disk: %s", path)
    except Exception as e:
        logger.error("Failed to save report to disk: %s", e)
    return path


def run_orchestrator(session_id: str, condition: str) -> None:
    """
    Entry point called in a background thread by app.py.
    Runs the full pipeline: research loop → synthesis → SSE complete event.
    Always sends a 'complete' event — never leaves the client hanging.
    """
    # Late import to avoid circular dependency with app.py
    from app import push_event, update_report

    registry = CitationRegistry()
    report = None

    try:
        push_event(session_id, "status", f"Starting research for: {condition}")

        loop = ResearchLoop(
            condition=condition,
            citation_registry=registry,
            push_event=push_event,
            session_id=session_id,
        )

        push_event(session_id, "status", "Gathering data from all sources...")
        bundle = loop.run()

        push_event(session_id, "status", "Synthesizing report...")
        report = build(bundle, registry, condition)

    except Exception as e:
        logger.exception("Pipeline error for session %s: %s", session_id, e)
        push_event(session_id, "warning", {
            "message": f"Some data sources failed — generating report from available data. ({e})",
            "source": "orchestrator",
        })
        # build() has its own fallback, but if it never ran, create a minimal report
        if report is None:
            from report_builder import _fallback_report
            from research_loop import RawDataBundle
            report = _fallback_report(RawDataBundle(), condition)
            report.update({
                "report_id": __import__("uuid").uuid4().__str__(),
                "condition": condition,
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
                "data_sources_queried": [],
                "references": [],
                "report_caveats": [
                    "Report generated with limited data due to source errors.",
                    "This report is for strategic intelligence purposes only — not clinical guidance.",
                ],
            })

    # Always attempt to store and complete — even with a degraded report
    try:
        sections = report.get("sections", {})
        section_order = [
            "overview", "standard_of_care", "emerging_treatments",
            "key_players", "recent_developments",
        ]
        refs = report.get("references", [])

        for key in section_order:
            sec = sections.get(key)
            if not sec:
                continue
            section_html = render_html({
                "sections": {key: sec},
                "references": refs,
                "report_caveats": [],
            })
            push_event(session_id, "section", {
                "key": key,
                "title": sec.get("title", key),
                "html": section_html,
            })

        _save_report_to_disk(report, report.get("condition", condition))

        update_report(session_id, report)
        logger.debug("update_report called for session %s — status now 'complete'", session_id)

        complete_payload = {
            "session_id": session_id,
            "references_count": len(refs),
            "sources": report.get("data_sources_queried", []),
        }
        logger.debug("Pushing 'complete' SSE event for session %s: %s", session_id, complete_payload)
        push_event(session_id, "complete", complete_payload)
        logger.info("Report complete for session %s — %d references", session_id, len(refs))

    except Exception as e:
        logger.exception("Report render failed for session %s: %s", session_id, e)
        # Last resort: send complete so browser redirects rather than hanging
        push_event(session_id, "complete", {
            "session_id": session_id,
            "references_count": 0,
            "sources": [],
        })
