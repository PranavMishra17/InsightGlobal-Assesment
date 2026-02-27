import json
import logging
import queue
import threading
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # suppress Flask request spam

logger = logging.getLogger(__name__)
app = Flask(__name__)

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


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
