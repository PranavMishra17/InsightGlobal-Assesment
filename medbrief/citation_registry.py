import logging

logger = logging.getLogger(__name__)


class CitationRegistry:
    """
    Accumulates citations from all sources, deduplicates by identifier,
    assigns sequential 1-based indices.
    """

    def __init__(self):
        self._by_id: dict[str, dict] = {}   # id -> citation dict
        self._index_map: dict[str, int] = {}  # id -> index
        self._counter = 0

    def register(self, item: dict) -> int:
        """
        Register a source item and return its citation index.
        If already registered (same id), returns existing index.
        """
        cid = item.get("id", "")
        if not cid:
            return 0

        if cid in self._index_map:
            return self._index_map[cid]

        self._counter += 1
        self._index_map[cid] = self._counter
        self._by_id[cid] = self._build_citation(item, self._counter)
        return self._counter

    def register_all(self, items: list[dict]) -> None:
        for item in items:
            self.register(item)

    def validate_index(self, index: int) -> bool:
        return index in self._index_map.values()

    def validate_id(self, cid: str) -> bool:
        return cid in self._index_map

    def get_index(self, cid: str) -> int | None:
        return self._index_map.get(cid)

    def all_citations(self) -> list[dict]:
        return sorted(self._by_id.values(), key=lambda c: c["index"])

    def _build_citation(self, item: dict, index: int) -> dict:
        source = item.get("source", "unknown")
        cid = item.get("id", "")

        if source == "pubmed":
            return {
                "index": index,
                "source_type": "pubmed",
                "identifier": cid,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "pub_date": item.get("year", ""),
                "journal": item.get("journal", ""),
                "is_preprint": item.get("is_preprint", False),
                "jurisdiction": None,
                "retrieved_at": item.get("retrieved_at", ""),
            }
        elif source == "clinicaltrials":
            return {
                "index": index,
                "source_type": "clinicaltrials",
                "identifier": cid,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "pub_date": item.get("start_date", ""),
                "journal": None,
                "is_preprint": False,
                "jurisdiction": None,
                "retrieved_at": item.get("retrieved_at", ""),
            }
        elif source == "openfda":
            return {
                "index": index,
                "source_type": "openfda",
                "identifier": cid,
                "title": f"{item.get('brand_name', '')} ({item.get('generic_name', '')})",
                "url": item.get("url", ""),
                "pub_date": item.get("approval_date", ""),
                "journal": None,
                "is_preprint": False,
                "jurisdiction": "FDA",
                "retrieved_at": item.get("retrieved_at", ""),
            }
        elif source == "web_search":
            return {
                "index": index,
                "source_type": "web_search",
                "identifier": cid,
                "title": "Web Search Results",
                "url": "",
                "pub_date": "",
                "journal": None,
                "is_preprint": False,
                "jurisdiction": None,
                "retrieved_at": item.get("retrieved_at", ""),
            }
        else:
            return {
                "index": index,
                "source_type": source,
                "identifier": cid,
                "title": item.get("title", cid),
                "url": item.get("url", ""),
                "pub_date": "",
                "journal": None,
                "is_preprint": False,
                "jurisdiction": None,
                "retrieved_at": item.get("retrieved_at", ""),
            }
