from config import CONTRACT_VERSION

CHANNELS = {"linkedin", "email", "phone", "sms", "internal_task"}
TONES = {"direct", "conversational", "light", "clear", "practical", "brief"}
QUEUES = {
    "sales_hot",
    "sales_nurture",
    "sales_cold",
    "care_urgent",
    "care_followup",
    "care_routine",
    "care_hold",
}
LEAD_LABELS = {"hot", "warm", "cold"}
PATIENT_LABELS = {"urgent", "soon", "routine", "hold"}


def validate_payload(payload):
    errors = []
    if not isinstance(payload, dict):
        return ["body has to be a json object"]

    required = [
        "contract_version",
        "idempotency_key",
        "run_id",
        "record_id",
        "source",
        "occurred_at",
        "person",
        "qualification",
        "action",
        "message",
        "rationale",
        "agent_trace",
    ]
    for key in required:
        if payload.get(key) in (None, "", [], {}):
            errors.append(f"missing {key}")

    if payload.get("contract_version") not in (None, CONTRACT_VERSION):
        errors.append(f"contract_version must be {CONTRACT_VERSION}")

    if payload.get("source") not in (None, "linkedin", "fhir"):
        errors.append("source must be linkedin or fhir")

    person = payload.get("person")
    if isinstance(person, dict):
        if not str(person.get("name") or "").strip():
            errors.append("person.name is required")
    elif "person" in payload:
        errors.append("person must be an object")

    qual = payload.get("qualification")
    if isinstance(qual, dict):
        kind = qual.get("kind")
        label = qual.get("label")
        if kind not in ("lead_temperature", "outreach_urgency"):
            errors.append("qualification.kind is not one we accept")
        allowed = LEAD_LABELS if kind == "lead_temperature" else PATIENT_LABELS
        if label not in allowed:
            errors.append(f"qualification.label {label} is not valid for {kind}")
        score = qual.get("score")
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            errors.append("qualification.score must be an integer 0-100")
        if not str(qual.get("intent") or "").strip():
            errors.append("qualification.intent is required")
    elif "qualification" in payload:
        errors.append("qualification must be an object")

    action = payload.get("action")
    if isinstance(action, dict):
        if action.get("channel") not in CHANNELS:
            errors.append("action.channel is not allowed")
        if action.get("tone") not in TONES:
            errors.append("action.tone is not allowed")
        if action.get("queue") not in QUEUES:
            errors.append("action.queue is not allowed")
        if not isinstance(action.get("escalate"), bool):
            errors.append("action.escalate must be true or false")
    elif "action" in payload:
        errors.append("action must be an object")

    message = payload.get("message")
    if isinstance(message, dict):
        if not str(message.get("subject") or "").strip():
            errors.append("message.subject is required")
        body = str(message.get("body") or "").strip()
        if len(body) < 40:
            errors.append("message.body is too short to be a real note")
        if message.get("audience") not in ("prospect", "staff"):
            errors.append("message.audience must be prospect or staff")
    elif "message" in payload:
        errors.append("message must be an object")

    rationale = payload.get("rationale")
    if isinstance(rationale, str) and len(rationale.strip()) < 20:
        errors.append("rationale is too thin")

    trace = payload.get("agent_trace")
    if isinstance(trace, list):
        if len(trace) < 3:
            errors.append("agent_trace needs at least 3 steps")
        for step in trace:
            if not isinstance(step, dict) or not step.get("step") or not step.get("detail"):
                errors.append("each agent_trace item needs step and detail")
                break
    elif "agent_trace" in payload:
        errors.append("agent_trace must be a list")

    return errors


def build_payload(run_id, record, result, occurred_at):
    contact = record.get("contact") or {}
    qual = result["qualification"]
    action = result["action"]
    message = result["message"]
    kind = "lead_temperature" if record["source"] == "linkedin" else "outreach_urgency"
    audience = "prospect" if record["source"] == "linkedin" else "staff"
    title = ""
    details = (record.get("context") or {}).get("details") or {}
    if record["source"] == "linkedin":
        title = details.get("job_title") or ""
    else:
        title = details.get("role_line") or "patient"
    return {
        "contract_version": CONTRACT_VERSION,
        "idempotency_key": f"{run_id}:{record['id']}",
        "run_id": run_id,
        "record_id": record["id"],
        "source": record["source"],
        "occurred_at": occurred_at,
        "person": {
            "name": record.get("name"),
            "title": title,
            "location": contact.get("location") or "",
            "contact": {
                "email": contact.get("email"),
                "phone": contact.get("phone"),
            },
        },
        "qualification": {
            "kind": kind,
            "label": qual["label"],
            "score": int(qual["score"]),
            "intent": qual.get("intent") or "unspecified",
        },
        "action": {
            "channel": action["channel"],
            "tone": action["tone"],
            "escalate": bool(action["escalate"]),
            "queue": action["queue"],
        },
        "message": {
            "subject": message["subject"],
            "body": message["body"],
            "audience": audience,
        },
        "rationale": result["rationale"],
        "agent_trace": result["trace"],
    }
