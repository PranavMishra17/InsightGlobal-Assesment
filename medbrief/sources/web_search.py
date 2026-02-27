import logging
import os
import time

import anthropic

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("CALUDE_API_KEY"))
    return _client


WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}

SYSTEM = (
    "You are a medical research assistant. "
    "Search for information using the provided queries. "
    "Return only factual information with source URLs. "
    "Prefer .gov, .edu, journal domains over aggregator sites. "
    "Ignore WebMD, Healthline, and similar patient portals. "
    "Always include publication dates when available."
)


def fetch(condition: str, extra_params: dict | None = None) -> list[dict]:
    """
    Run two web searches: recent developments + key players pipeline.
    Returns a list with a single dict containing combined text results.
    """
    queries = [
        f'"{condition}" treatment guidelines 2024 2025 site:nih.gov OR site:who.int OR site:nejm.org',
        f'"{condition}" new treatment clinical trial results 2025',
        f'"{condition}" pharmaceutical company pipeline 2025',
    ]

    if extra_params and extra_params.get("queries"):
        queries = extra_params["queries"]

    query_list = "\n".join(f"- {q}" for q in queries)

    logger.debug("web_search.fetch: starting Claude call for condition=%r", condition)
    t0 = time.time()
    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            tools=[WEB_SEARCH_TOOL],
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Context: Medical condition = {condition}\n\n"
                    f"Please search for the following:\n{query_list}\n\n"
                    "For each result provide: source URL, publication date, key finding."
                ),
            }],
            timeout=90,
        )
        logger.debug("web_search.fetch: Claude responded in %.1fs", time.time() - t0)

        text_blocks = [
            block.text for block in response.content if hasattr(block, "text")
        ]
        combined = "\n".join(text_blocks).strip()
        logger.debug("web_search.fetch: got %d chars of text", len(combined))

        if not combined:
            logger.warning("web_search.fetch: Claude returned no text blocks")
            return []

        return [{
            "source": "web_search",
            "id": "WEB:web_search",
            "content": combined,
            "condition": condition,
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }]

    except anthropic.APIError as e:
        logger.error("web_search API error after %.1fs: status=%s msg=%s", time.time() - t0, e.status_code, e.message)
        return []
    except Exception as e:
        logger.error("web_search failed after %.1fs: %s", time.time() - t0, e)
        return []
