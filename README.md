# MedBrief

Enter a medical condition, get a 4-section research report with citations — streamed to the browser in real time.

**Sources:** ClinicalTrials.gov, PubMed, OpenFDA, Anthropic web search

---

## Setup

```bash
cd medbrief
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python app.py
```

Open `http://localhost:5000`.

## Environment

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude synthesis + web search |
| `NCBI_API_KEY` | No | PubMed: 10 req/s vs 3 |
| `OPENFDA_API_KEY` | No | OpenFDA: 120k/day vs 1k |

## API

| Route | Method | Description |
|---|---|---|
| `/` | GET | Input form |
| `/generate` | POST | Start report; body: `{"condition": "type 2 diabetes"}` |
| `/stream/<sid>` | GET | SSE stream: `status`, `section`, `complete`, `error` |
| `/report/<sid>` | GET | Rendered HTML report |

## Structure

```
medbrief/
  app.py              # Flask routes + SSE
  orchestrator.py     # pipeline controller
  research_loop.py    # fetch → evaluate → repeat (max 3 iterations)
  report_builder.py   # Claude synthesis → HTML report
  citation_registry.py
  sources/
    clinicaltrials.py
    pubmed.py
    openfda.py
    web_search.py     # Anthropic tool use
  templates/
    index.html
    report.html
  static/report.js    # SSE consumer
```

## Disclaimer

Not for clinical use. Report content is AI-generated and may be incomplete or inaccurate. Always consult a qualified healthcare professional.
