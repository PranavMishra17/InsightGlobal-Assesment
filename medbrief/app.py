import glob
import json
import logging
import os
import queue
import re
import threading
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # suppress Flask request spam

logger = logging.getLogger(__name__)
app = Flask(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def _linkify_citations(text: str) -> str:
    """Convert [SOURCE_N] markers to superscript anchor links."""
    if not text:
        return ""
    return re.sub(
        r'\[SOURCE_(\d+)\]',
        lambda m: f'<sup><a href="#ref-{m.group(1)}">[{m.group(1)}]</a></sup>',
        text,
    )


app.jinja_env.filters["linkify"] = _linkify_citations


def save_report_html(report: dict, json_path: str) -> None:
    """Render the report template to a standalone HTML file alongside its JSON."""
    html_path = json_path.replace(".json", ".html")
    try:
        with app.app_context():
            html = render_template("report.html", report=report)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Report HTML saved: %s", html_path)
    except Exception as e:
        logger.error("Failed to save report HTML for %s: %s", json_path, e)

# --- In-memory session store ---
_store: dict = {}
_lock = threading.Lock()


def create_session(session_id: str, condition: str) -> None:
    with _lock:
        _store[session_id] = {
            "condition": condition,
            "queue": queue.Queue(),
            "report": None,
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


def get_session(session_id: str) -> dict | None:
    with _lock:
        return _store.get(session_id)


def update_report(session_id: str, report: dict) -> None:
    with _lock:
        if session_id in _store:
            _store[session_id]["report"] = report
            _store[session_id]["status"] = "complete"


def push_event(session_id: str, event_type: str, data) -> None:
    session = get_session(session_id)
    if session:
        session["queue"].put({"type": event_type, "data": data})


# --- Routes ---

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    body = request.get_json(silent=True) or {}
    condition = (body.get("condition") or "").strip()
    if not condition:
        return jsonify({"error": "condition required"}), 400

    session_id = str(uuid.uuid4())
    create_session(session_id, condition)

    from orchestrator import run_orchestrator
    thread = threading.Thread(
        target=run_orchestrator,
        args=(session_id, condition),
        daemon=True,
    )
    thread.start()

    return jsonify({"session_id": session_id})


@app.route("/stream/<session_id>")
def stream(session_id):
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    def event_generator():
        # If session already completed (e.g. browser reconnected after SSE drop),
        # immediately replay a complete event so the browser can redirect.
        if session.get("status") == "complete":
            logger.debug("SSE reconnect — session %s already complete, replaying complete event", session_id)
            refs = session.get("report", {}).get("references", []) if session.get("report") else []
            yield f"data: {json.dumps({'type': 'complete', 'data': {'session_id': session_id, 'references_count': len(refs), 'sources': []}})}\n\n"
            return

        q = session["queue"]
        while True:
            try:
                event = q.get(timeout=15)
                logger.debug("SSE sending event type=%s for session %s", event["type"], session_id)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "complete":
                    logger.debug("SSE stream closing after 'complete' for session %s", session_id)
                    break
            except queue.Empty:
                # Check if completed while we were waiting (race between queue drain + status update)
                if session.get("status") == "complete":
                    logger.debug("SSE detected completion via status check for session %s", session_id)
                    refs = session.get("report", {}).get("references", []) if session.get("report") else []
                    yield f"data: {json.dumps({'type': 'complete', 'data': {'session_id': session_id, 'references_count': len(refs), 'sources': []}})}\n\n"
                    break
                logger.debug("SSE heartbeat for session %s", session_id)
                yield "data: {\"type\": \"heartbeat\"}\n\n"

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/status/<session_id>")
def status(session_id):
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    return jsonify({
        "status": session["status"],
        "ready": session["status"] == "complete",
    })


@app.route("/report/<session_id>")
def report(session_id):
    session = get_session(session_id)
    if not session or not session.get("report"):
        return jsonify({"error": "report not ready"}), 404
    return render_template("report.html", report=session["report"])


@app.route("/api/reports")
def api_reports():
    """List all saved report JSON files, newest first. Auto-generates missing HTML files."""
    if not os.path.isdir(REPORTS_DIR):
        return jsonify([])
    files = sorted(
        glob.glob(os.path.join(REPORTS_DIR, "*.json")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    reports = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            html_path = path.replace(".json", ".html")
            if not os.path.isfile(html_path):
                save_report_html(data, path)
            filename = os.path.basename(path)
            reports.append({
                "filename": filename,
                "html_filename": filename.replace(".json", ".html"),
                "condition": data.get("condition", "Unknown"),
                "generated_at": data.get("generated_at", ""),
                "references_count": len(data.get("references", [])),
                "sources": data.get("data_sources_queried", []),
            })
        except Exception as e:
            logger.warning("Skipping unreadable report %s: %s", path, e)
    return jsonify(reports)


@app.route("/reports-file/<filename>")
def reports_file(filename):
    """Serve a pre-rendered HTML report file directly."""
    if not re.match(r'^[a-z0-9_\-]+\.html$', filename):
        return jsonify({"error": "invalid filename"}), 400
    if not os.path.isfile(os.path.join(REPORTS_DIR, filename)):
        return jsonify({"error": "report not found"}), 404
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/saved-report/<filename>")
def saved_report(filename):
    """Load and render a report from disk by filename."""
    # Prevent path traversal — only allow bare filenames ending in .json
    safe = re.sub(r"[^a-z0-9_\-]", "", filename.lower().removesuffix(".json"))
    if not safe or filename != safe + ".json":
        return jsonify({"error": "invalid filename"}), 400
    path = os.path.join(REPORTS_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "report not found"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            report_data = json.load(f)
    except Exception as e:
        logger.error("Failed to load saved report %s: %s", filename, e)
        return jsonify({"error": "failed to load report"}), 500
    return render_template("report.html", report=report_data)


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
