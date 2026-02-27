/**
 * report.js — SSE consumer for live section streaming into the report page.
 *
 * Current flow: index.html redirects to /report/<sid> after generation is
 * complete, so the Jinja2-rendered report is the canonical path. This file
 * is wired up for a future live-streaming variant where sections are appended
 * as they arrive rather than waiting for full synthesis.
 *
 * Usage: include this script on a page that has a `data-session-id` attribute
 * on its <body> tag and placeholder divs with id="section-<key>".
 */

(function () {
  "use strict";

  const body = document.body;
  const sessionId = body.dataset.sessionId;
  if (!sessionId) return; // static report — nothing to stream

  const sectionOrder = [
    "overview",
    "standard_of_care",
    "emerging_treatments",
    "key_players",
    "recent_developments",
  ];

  const statusEl = document.getElementById("stream-status");

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  function insertSection(key, html) {
    const container = document.getElementById("section-" + key);
    if (container) {
      container.innerHTML = html;
      container.classList.add("loaded");
    }
  }

  const evtSource = new EventSource("/stream/" + sessionId);

  evtSource.onmessage = function (e) {
    let event;
    try {
      event = JSON.parse(e.data);
    } catch (_) {
      return;
    }

    switch (event.type) {
      case "status":
        setStatus(event.data);
        break;

      case "loop_info": {
        const d = event.data;
        setStatus(
          "Iteration " +
          d.iteration +
          "/3 — Trials: " +
          d.trials +
          ", PubMed: " +
          d.pubmed +
          ", FDA: " +
          d.fda
        );
        break;
      }

      case "section":
        insertSection(event.data.key, event.data.html);
        break;

      case "complete":
        evtSource.close();
        setStatus("Report complete — " + event.data.references_count + " references.");
        // If running in live mode, unhide the references section
        const refsEl = document.getElementById("references");
        if (refsEl) refsEl.classList.add("loaded");
        break;

      case "error":
        setStatus("Error (" + event.data.source + "): " + event.data.message);
        evtSource.close();
        break;

      case "heartbeat":
        break;

      default:
        break;
    }
  };

  evtSource.onerror = function () {
    evtSource.close();
    setStatus("Connection lost.");
  };
})();
