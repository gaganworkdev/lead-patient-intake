import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

from agent import run_agent
from config import CRM_URL, SAMPLE_PATH
from contract import build_payload, validate_payload
from ingest import load_leads, load_patients
from llm_client import LLMClient, LLMError
import store

log = logging.getLogger("pipeline")
_lock = threading.Lock()
status = {
    "running": False,
    "phase": "idle",
    "done": 0,
    "total": 0,
    "note": "No run yet",
    "log": [],
    "error": None,
    "provider": None,
}


def snapshot():
    with _lock:
        data = dict(status)
        data["log"] = list(status["log"])
        return data


def remember_last_run():
    if snapshot()["running"]:
        return
    run = store.latest_run()
    if not run:
        return
    summary = run.get("summary") or {}
    if run.get("status") == "finished":
        note = f"Last run delivered {summary.get('delivered', 0)} records."
        _update(
            phase="done",
            running=False,
            error=None,
            note=note,
            provider=summary.get("provider"),
            log=[note],
        )
    elif run.get("status") == "failed":
        note = summary.get("error") or "Last run stopped."
        _update(phase="error", running=False, error=note, note=note, log=[note])


def _note(message):
    log.info(message)
    with _lock:
        status["note"] = message
        status["log"] = (status["log"] + [message])[-40:]


def _update(**fields):
    with _lock:
        status.update(fields)


def _post(payload, timeout=15):
    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(CRM_URL, json=payload, timeout=timeout)
            if resp.status_code == 422:
                return resp
            if resp.status_code >= 500:
                raise requests.HTTPError(f"crm responded {resp.status_code}")
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            _note(f"CRM post attempt {attempt}/3 failed ({exc})")
            if attempt == 3:
                break
            time.sleep(0.7 * attempt)
    raise RuntimeError(f"CRM unreachable: {last_error}")


def probe_contract(run_id):
    bad = {"contract_version": "1.0", "note": "this payload is incomplete on purpose"}
    local_errors = validate_payload(bad)
    try:
        resp = _post(bad)
    except RuntimeError as exc:
        store.save_failure(run_id, "crm_contract", "probe", str(exc))
        return str(exc)
    if resp.status_code == 422:
        message = (
            f"Bad payload was rejected with 422. Local check saw {len(local_errors)} problems. "
            "That rejection is the contract test, not a crashed record."
        )
        log.info(message)
        store.save_failure(run_id, "crm_contract", "probe", message)
        return message
    message = f"Contract probe got an unexpected {resp.status_code}"
    store.save_failure(run_id, "crm_contract", "probe", message)
    return message


def run():
    if snapshot()["running"]:
        return {"ok": False, "error": "a run is already going"}

    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    _update(
        running=True,
        phase="starting",
        done=0,
        total=0,
        error=None,
        log=[],
        note="Starting",
        provider=None,
    )
    store.start_run(run_id)
    summary = {
        "run_id": run_id,
        "leads_kept": 0,
        "patients_kept": 0,
        "delivered": 0,
        "failed": 0,
        "provider": None,
        "contract_probe": None,
    }
    sample_records = []

    try:
        _note("Checking the LLM")
        client = LLMClient()
        summary["provider"] = client.describe()
        _update(provider=client.describe(), phase="ingest")
        _note(f"Using {client.describe()}")

        _note("Reading the lead CSV")
        leads, lead_failures = load_leads()
        for failure in lead_failures:
            store.save_failure(run_id, failure["stage"], failure["ref"], failure["message"])
            summary["failed"] += 1
        summary["leads_kept"] = len(leads)
        _note(f"Leads kept {len(leads)}, dropped {len(lead_failures)}")

        _update(phase="fhir")
        _note("Pulling synthetic patients from the SMART sandbox")
        patients, patient_failures = load_patients(limit=16)
        for failure in patient_failures:
            store.save_failure(run_id, failure["stage"], failure["ref"], failure["message"])
            summary["failed"] += 1
        summary["patients_kept"] = len(patients)
        _note(f"Patients kept {len(patients)}, dropped {len(patient_failures)}")

        records = leads + patients
        _update(phase="agent", total=len(records), done=0)
        _note(f"Agent loop starting for {len(records)} records")

        for index, record in enumerate(records, start=1):
            _update(done=index - 1)
            _note(f"Working {record['id']} ({index}/{len(records)})")
            try:
                result = run_agent(record, client)
            except (LLMError, Exception) as exc:
                log.exception("agent failed on %s", record["id"])
                store.save_failure(run_id, "agent", record["id"], str(exc))
                summary["failed"] += 1
                _update(done=index)
                continue

            occurred = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            payload = build_payload(run_id, record, result, occurred)
            problems = validate_payload(payload)
            if problems:
                message = "payload failed our own contract: " + "; ".join(problems)
                store.save_failure(run_id, "contract", record["id"], message)
                store.save_record(run_id, record, result, "contract_failed")
                summary["failed"] += 1
                _update(done=index)
                continue

            try:
                resp = _post(payload)
                body = resp.json()
            except (RuntimeError, ValueError, requests.RequestException) as exc:
                result["crm"] = {"accepted": False, "error": str(exc)}
                store.save_record(run_id, record, result, "delivery_failed")
                store.save_failure(run_id, "deliver", record["id"], str(exc))
                summary["failed"] += 1
                sample_records.append(_sample_row(record, result))
                _update(done=index)
                continue

            if resp.status_code == 422 or not body.get("accepted"):
                result["crm"] = {"accepted": False, "status_code": resp.status_code, "body": body}
                store.save_record(run_id, record, result, "delivery_failed")
                store.save_failure(run_id, "deliver", record["id"], json.dumps(body)[:500])
                summary["failed"] += 1
            else:
                result["trace"].append(
                    {
                        "step": "deliver",
                        "detail": (
                            f"CRM accepted it as id {body.get('crm_id')}"
                            + (" (duplicate)" if body.get("duplicate") else "")
                        ),
                    }
                )
                result["crm"] = {
                    "accepted": True,
                    "status_code": resp.status_code,
                    "crm_id": body.get("crm_id"),
                    "duplicate": bool(body.get("duplicate")),
                }
                store.save_record(run_id, record, result, "delivered")
                summary["delivered"] += 1
            sample_records.append(_sample_row(record, result))
            _update(done=index)

        _update(phase="probe")
        _note("Sending one bad payload to confirm the CRM rejects it")
        summary["contract_probe"] = probe_contract(run_id)

        summary["status"] = "finished"
        store.finish_run(run_id, "finished", summary)
        _write_sample(run_id, summary, sample_records)
        _update(phase="done", running=False, error=None)
        _note(
            f"Finished. Delivered {summary['delivered']} of {len(records)}. "
            f"Sample written to {SAMPLE_PATH.name}."
        )
        return {"ok": True, "run_id": run_id, "summary": summary}
    except Exception as exc:
        log.exception("run failed")
        summary["status"] = "failed"
        summary["error"] = str(exc)
        store.finish_run(run_id, "failed", summary)
        _update(phase="error", running=False, error=str(exc))
        _note(f"Run stopped: {exc}")
        return {"ok": False, "error": str(exc), "run_id": run_id}


def _sample_row(record, result):
    return {
        "input": {
            "id": record["id"],
            "source": record["source"],
            "name": record["name"],
            "contact": {
                "location": (record.get("contact") or {}).get("location"),
                "has_email": bool((record.get("contact") or {}).get("email")),
                "has_phone": bool((record.get("contact") or {}).get("phone")),
            },
            "context": record.get("context"),
            "priority_signal": record.get("priority_signal"),
        },
        "reasoning": result.get("trace"),
        "qualification": result.get("qualification"),
        "action": result.get("action"),
        "message": result.get("message"),
        "rationale": result.get("rationale"),
        "crm": result.get("crm"),
    }


def _write_sample(run_id, summary, rows):
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summary,
        "records": rows,
    }
    SAMPLE_PATH.write_text(json.dumps(document, indent=2), encoding="utf-8")
    log.info("wrote %s records to %s", len(rows), SAMPLE_PATH)
