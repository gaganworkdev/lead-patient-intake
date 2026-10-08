import logging
import threading

from flask import Flask, jsonify, render_template, request

from config import PORT
from contract import validate_payload
from logging_setup import setup_logging
import pipeline
import store

setup_logging()
store.init()
pipeline.remember_last_run()
log = logging.getLogger("crm")
app = Flask(__name__)


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/dashboard")
def dashboard():
    run = store.latest_run()
    payload = {
        "status": pipeline.snapshot(),
        "run": run,
        "records": [],
        "failures": [],
        "crm": [],
    }
    if run:
        payload["records"] = store.records_for(run["id"])
        payload["failures"] = store.failures_for(run["id"])
        payload["crm"] = store.crm_for(run["id"])
    return jsonify(payload)


@app.post("/api/pipeline/run")
def start_run():
    if pipeline.snapshot()["running"]:
        return jsonify({"ok": False, "error": "a run is already going"}), 409

    def _go():
        pipeline.run()

    threading.Thread(target=_go, daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/crm/intake")
def crm_intake():
    payload = request.get_json(silent=True)
    errors = validate_payload(payload)
    if errors:
        log.warning("crm rejected a payload: %s", "; ".join(errors))
        return jsonify({"accepted": False, "errors": errors}), 422
    saved = store.save_crm(payload)
    log.info(
        "crm stored %s as %s duplicate=%s",
        payload.get("record_id"),
        saved["crm_id"],
        saved["duplicate"],
    )
    return jsonify({"accepted": True, "crm_id": saved["crm_id"], "duplicate": saved["duplicate"]})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
