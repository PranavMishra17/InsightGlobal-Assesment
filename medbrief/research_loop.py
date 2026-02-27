import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from citation_registry import CitationRegistry
from sources import clinicaltrials, openfda, pubmed, web_search

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3
SUFFICIENCY_THRESHOLD = 0.65

SUFFICIENCY_SYSTEM = (
    "You are a data sufficiency evaluator for a medical intelligence briefing. "
    "You assess whether enough data has been gathered across four report dimensions. "
    "Respond ONLY with valid JSON — no preamble or explanation."
)

SUFFICIENCY_PROMPT = """\
Condition: {condition}
Iteration: {iteration} of {max_iter}

Data retrieved so far:
- ClinicalTrials records: {trial_count} (phases: {phases})
- PubMed articles: {pubmed_count} (types: {pub_types})
- OpenFDA drug records: {fda_count}
- Web search: {web_ok}

Evaluate sufficiency. Consider sufficient (score >= 0.75) when:
- standard_of_care: >= 2 review/guideline articles OR >= 2 FDA drug records
- emerging_treatments: >= 2 active Phase II/III trials
- key_players: >= 2 distinct sponsors identified
- recent_developments: web search returned content

If not sufficient, provide refined queries to address gaps.

Respond with this exact JSON schema:
{{
  "sufficient": false,
  "overall_score": 0.0,
  "missing_areas": ["..."],
  "refined_queries": {{
    "clinicaltrials": {{"query.cond": "...", "query.term": "..."}},
    "pubmed": {{"term": "..."}},
    "openfda": {{"condition": "..."}},
    "web_search": {{"queries": ["...", "..."]}}
  }}
}}
"""


class RawDataBundle:
    def __init__(self):
        self.trials: list[dict] = []
        self.pubmed: list[dict] = []
        self.fda: list[dict] = []
        self.web: list[dict] = []

    def extend(self, other: "RawDataBundle") -> None:
        # Deduplicate by id when merging
        existing_ids = {i["id"] for i in self.trials}
        self.trials.extend(i for i in other.trials if i["id"] not in existing_ids)

        existing_ids = {i["id"] for i in self.pubmed}
        self.pubmed.extend(i for i in other.pubmed if i["id"] not in existing_ids)

        existing_ids = {i["id"] for i in self.fda}
        self.fda.extend(i for i in other.fda if i["id"] not in existing_ids)

        # Web search: just keep latest
        if other.web:
            self.web = other.web

    def all_items(self) -> list[dict]:
        return self.trials + self.pubmed + self.fda + self.web

    def summary(self) -> dict:
        phases = list({t.get("phase", "") for t in self.trials if t.get("phase")})
        pub_types = list({
            pt for p in self.pubmed for pt in p.get("publication_types", [])
        })
        sponsors = list({t.get("sponsor", "") for t in self.trials if t.get("sponsor")})
        return {
            "trial_count": len(self.trials),
            "phases": ", ".join(phases) or "none",
            "pubmed_count": len(self.pubmed),
            "pub_types": ", ".join(pub_types[:5]) or "none",
            "fda_count": len(self.fda),
            "web_ok": "yes" if self.web else "no",
            "sponsors": sponsors[:5],
        }


class ResearchLoop:
    def __init__(self, condition: str, citation_registry: CitationRegistry,
                 push_event, session_id: str):
        self.condition = condition
        self.registry = citation_registry
        self.push_event = push_event
        self.session_id = session_id
        self._client = anthropic.Anthropic(api_key=os.getenv("CALUDE_API_KEY"))

    def run(self) -> RawDataBundle:
        bundle = RawDataBundle()
        refined: dict = {}

        for iteration in range(1, MAX_ITERATIONS + 1):
            self.push_event(self.session_id, "status",
                            f"Research iteration {iteration}/{MAX_ITERATIONS} — querying all sources...")

            iter_bundle = self._fetch_all(refined)
            bundle.extend(iter_bundle)

            # Register everything found so far
            self.registry.register_all(bundle.all_items())

            summary = bundle.summary()
            self.push_event(self.session_id, "loop_info", {
                "iteration": iteration,
                "trials": summary["trial_count"],
                "pubmed": summary["pubmed_count"],
                "fda": summary["fda_count"],
                "web": summary["web_ok"],
            })

            # Last iteration — always proceed to synthesis
            if iteration == MAX_ITERATIONS:
                break

            # Evaluate sufficiency
            result = self._evaluate(bundle, iteration)
            if result.get("sufficient") or result.get("overall_score", 0) >= SUFFICIENCY_THRESHOLD:
                logger.info("Sufficient data at iteration %d", iteration)
                break

            # Carry refined queries into next iteration
            refined = result.get("refined_queries", {})
            missing = result.get("missing_areas", [])
            if missing:
                self.push_event(self.session_id, "status",
                                f"Refining queries — gaps: {'; '.join(missing[:2])}")

        return bundle

    def _fetch_all(self, refined: dict) -> RawDataBundle:
        b = RawDataBundle()

        ct_params = refined.get("clinicaltrials", {})
        pm_params = refined.get("pubmed", {})
        fda_params = refined.get("openfda", {})
        ws_params = refined.get("web_search", {})

        fda_condition = fda_params.pop("condition", self.condition) if fda_params else self.condition

        tasks = {
            "trials": (clinicaltrials.fetch, self.condition, ct_params or None),
            "pubmed": (pubmed.fetch, self.condition, pm_params or None),
            "fda": (openfda.fetch, fda_condition, fda_params or None),
            "web": (web_search.fetch, self.condition, ws_params or None),
        }

        logger.debug("_fetch_all: launching %d source tasks", len(tasks))
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(fn, cond, params): key
                for key, (fn, cond, params) in tasks.items()
            }
            for future in as_completed(futures):
                key = futures[future]
                elapsed = time.time() - t0
                try:
                    result = future.result()
                    logger.debug("Source '%s' completed in %.1fs — %d items", key, elapsed, len(result))
                    if key == "trials":
                        b.trials = result
                    elif key == "pubmed":
                        b.pubmed = result
                    elif key == "fda":
                        b.fda = result
                    elif key == "web":
                        b.web = result
                except Exception as e:
                    logger.error("Source '%s' raised after %.1fs: %s", key, elapsed, e)
                    self.push_event(self.session_id, "warning",
                                    {"message": f"{key} unavailable — continuing without it.", "source": key})

        logger.debug("_fetch_all: all sources done in %.1fs", time.time() - t0)
        return b

    def _evaluate(self, bundle: RawDataBundle, iteration: int) -> dict:
        summary = bundle.summary()
        prompt = SUFFICIENCY_PROMPT.format(
            condition=self.condition,
            iteration=iteration,
            max_iter=MAX_ITERATIONS,
            **summary,
        )

        logger.debug("_evaluate: calling Claude sufficiency eval (iteration %d)", iteration)
        t0 = time.time()
        try:
            response = self._client.messages.create(
                model="claude-haiku-4-5-20251001",  # fast + cheap for eval
                max_tokens=512,
                system=SUFFICIENCY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                timeout=30,
            )
            logger.debug("_evaluate: Claude responded in %.1fs", time.time() - t0)
            text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Sufficiency eval JSON parse failed after %.1fs: %s", time.time() - t0, e)
            return {"sufficient": False, "overall_score": 0.5, "refined_queries": {}}
        except Exception as e:
            logger.error("Sufficiency eval failed after %.1fs: %s", time.time() - t0, e)
            return {"sufficient": False, "overall_score": 0.5, "refined_queries": {}}
