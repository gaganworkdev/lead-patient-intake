import csv
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from config import FHIR_BASE, LEADS_CSV

log = logging.getLogger("ingest")

STRONG_WORDS = (
    "missed call",
    "no-show",
    "no show",
    "follow-up",
    "follow up",
    "after-hours",
    "after hours",
    "booking",
    "receptionist",
    "front desk",
    "front-desk",
    "call volume",
    "multilingual",
    "scheduling",
)
WEAK_WORDS = (
    "ai ",
    "ai receptionist",
    "ghl",
    "chatbot",
    "voice agent",
    "webinar",
    "retention",
    "wait time",
)
ACUITY_WORDS = (
    "heart failure",
    "myocardial",
    "stroke",
    "cancer",
    "malignant",
    "copd",
    "sepsis",
    "suicid",
    "kidney",
    "dialysis",
    "diabet",
    "pregnan",
    "fracture",
    "chest pain",
    "pneumonia",
    "transplant",
    "failure",
    "overdose",
    "coronary",
    "heart disease",
)

_inject_timeout = {"armed": False}


def _clamp(score):
    return max(0, min(96, int(score)))


def _activity_is_dead(activity):
    text = (activity or "").strip().lower()
    if not text:
        return True
    return "no recent" in text or "dormant" in text


def lead_warmth(activity, note):
    blob_note = (note or "").lower()
    meaningful = not _activity_is_dead(activity)
    if "demo" in blob_note or "requested a call" in blob_note:
        return "demo"
    if "pricing" in blob_note or "pricing" in (activity or "").lower():
        return "pricing"
    if "warm intro" in blob_note:
        return "warm_intro"
    if "mutual" in blob_note:
        return "mutual"
    if meaningful:
        return "engaged"
    if "cold" in blob_note or _activity_is_dead(activity):
        return "cold"
    return "unknown"


def _title_weight(title):
    text = (title or "").lower()
    if any(part in text for part in ("founder", "owner", "ceo", "chief")):
        return 18
    if any(part in text for part in ("vp", "vice president", "director")):
        return 14
    if "manager" in text or "administrator" in text:
        return 10
    return 5


def _keyword_weight(blob):
    text = f" {blob.lower()} "
    strong = any(word in text for word in STRONG_WORDS)
    weak = any(word in text for word in WEAK_WORDS) or " ai" in text or text.strip().startswith("ai")
    if strong:
        return 14, "strong"
    if weak:
        return 6, "weak"
    return 0, "none"


def _size_weight(size):
    text = (size or "").strip()
    if text in ("1-10", "11-50"):
        return 6
    if text == "51-200":
        return 3
    return 0


def score_lead(row):
    activity = row.get("recent_activity") or ""
    note = row.get("connection_note") or ""
    blob = f"{activity} {note} {row.get('linkedin_headline') or ''}"
    warmth = lead_warmth(activity, note)
    warmth_points = {
        "demo": 24,
        "pricing": 24,
        "warm_intro": 18,
        "mutual": 12,
        "engaged": 6,
        "unknown": 0,
        "cold": -16,
    }[warmth]
    keyword_points, keyword_band = _keyword_weight(blob)
    industry = (row.get("industry") or "").lower()
    icp = industry in ("healthcare", "dental", "medspa", "chiropractic", "wellness")
    score = 28
    score += 12 if icp else 0
    score += _title_weight(row.get("job_title"))
    score += _size_weight(row.get("company_size"))
    score += keyword_points
    score += warmth_points
    hints = []
    if icp:
        hints.append(f"industry {row.get('industry')} fits clinic work")
    hints.append(f"title weight from {row.get('job_title') or 'unknown role'}")
    hints.append(f"warmth {warmth}")
    if keyword_band != "none":
        hints.append(f"{keyword_band} buying language in the activity")
    return {
        "warmth": warmth,
        "heuristic_score": _clamp(score),
        "hints": hints,
        "keyword_band": keyword_band,
        "do_not_contact": False,
    }


def load_leads(path=None):
    path = path or LEADS_CSV
    records = []
    failures = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    rows.append(
        {
            "lead_id": "L000",
            "full_name": "",
            "job_title": "",
            "company": "",
            "industry": "",
            "company_size": "",
            "location": "",
            "linkedin_headline": "",
            "recent_activity": "",
            "connection_note": "injected bad row for the failure path",
        }
    )
    for row in rows:
        try:
            records.append(normalize_lead(row))
        except ValueError as exc:
            ref = row.get("lead_id") or "unknown-lead"
            log.warning("dropped lead %s: %s", ref, exc)
            failures.append({"stage": "ingest", "ref": ref, "message": str(exc)})
    return records, failures


def normalize_lead(row):
    lead_id = (row.get("lead_id") or "").strip()
    name = (row.get("full_name") or "").strip()
    if not lead_id or not name:
        raise ValueError(f"{lead_id or 'row'} is missing lead_id or full_name")
    signal = score_lead(row)
    headline = (row.get("linkedin_headline") or "").strip()
    activity = (row.get("recent_activity") or "").strip()
    note = (row.get("connection_note") or "").strip()
    summary = (
        f"{name} is {row.get('job_title') or 'unknown role'} at {row.get('company') or 'unknown company'} "
        f"({row.get('industry') or 'unknown industry'}, size {row.get('company_size') or 'n/a'}, {row.get('location') or 'unknown location'}). "
        f"Headline: {headline or 'none'}. Recent activity: {activity or 'none'}. How they got on the list: {note or 'none'}."
    )
    return {
        "id": f"lead:{lead_id}",
        "source": "linkedin",
        "source_id": lead_id,
        "name": name,
        "contact": {
            "email": None,
            "phone": None,
            "location": (row.get("location") or "").strip(),
        },
        "context": {
            "summary": summary,
            "details": {
                "job_title": (row.get("job_title") or "").strip(),
                "company": (row.get("company") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
                "company_size": (row.get("company_size") or "").strip(),
                "headline": headline,
                "recent_activity": activity,
                "connection_note": note,
            },
        },
        "priority_signal": signal,
        "raw_payload": {key: (row.get(key) or "").strip() for key in row.keys()},
    }


def _code_text(node):
    if not isinstance(node, dict):
        return ""
    if node.get("text"):
        return str(node["text"])
    coding = node.get("coding") or []
    if coding and isinstance(coding[0], dict):
        return coding[0].get("display") or coding[0].get("code") or ""
    return ""


def _human_name(resource):
    names = resource.get("name")
    if isinstance(names, str) or not isinstance(names, list) or not names:
        return ""
    chosen = names[0] if isinstance(names[0], dict) else {}
    for item in names:
        if isinstance(item, dict) and item.get("use") == "official":
            chosen = item
            break
    given = " ".join(chosen.get("given") or [])
    family = chosen.get("family") or ""
    return f"{given} {family}".strip()


def _age(birth_date):
    if not birth_date:
        return None
    try:
        born = datetime.strptime(birth_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years


def score_patient(active_conditions, acuity, age, upcoming, no_show):
    score = 30 + min(len(active_conditions) * 6, 30)
    if acuity:
        score += 22
    if age is not None and age >= 75:
        score += 8
    if upcoming:
        score += 18
    if no_show:
        score += 12
    hints = [f"{len(active_conditions)} active conditions"]
    if acuity:
        hints.append("at least one higher-acuity condition")
    if upcoming:
        hints.append("an appointment is on the books soon")
    if no_show:
        hints.append("a missed or cancelled visit")
    if age is not None and age >= 75:
        hints.append("older patient")
    return {"heuristic_score": _clamp(score), "hints": hints, "acuity": "high" if acuity else "standard"}


def normalize_patient(resource, conditions, appointments):
    if not isinstance(resource, dict) or resource.get("resourceType") != "Patient":
        raise ValueError("entry is not a Patient resource")
    patient_id = str(resource.get("id") or "").strip()
    name = _human_name(resource)
    if not patient_id or not name:
        raise ValueError(f"{patient_id or 'patient'} has no usable id or name")
    if resource.get("deceasedBoolean") or resource.get("deceasedDateTime"):
        deceased = True
    else:
        deceased = False

    phone = None
    email = None
    for telecom in resource.get("telecom") or []:
        if not isinstance(telecom, dict):
            continue
        if telecom.get("system") == "phone" and not phone:
            phone = telecom.get("value")
        if telecom.get("system") == "email" and not email:
            email = telecom.get("value")

    address = {}
    addresses = resource.get("address") or []
    if addresses and isinstance(addresses[0], dict):
        address = addresses[0]
    city = address.get("city") or ""
    state = address.get("state") or ""
    location = ", ".join(part for part in (city, state) if part)

    language = ""
    for comm in resource.get("communication") or []:
        if isinstance(comm, dict):
            language = _code_text(comm.get("language") or {})
            if language:
                break

    cleaned_conditions = []
    for condition in conditions:
        text = _code_text(condition.get("code") or {})
        status = _code_text(condition.get("clinicalStatus") or {}) or "unknown"
        if not text:
            continue
        cleaned_conditions.append(
            {
                "text": text,
                "status": status,
                "onset": condition.get("onsetDateTime") or condition.get("recordedDate") or "",
            }
        )
    active = [item for item in cleaned_conditions if item["status"].lower().startswith("active")]
    acuity = any(any(word in item["text"].lower() for word in ACUITY_WORDS) for item in active)

    cleaned_appts = []
    upcoming = False
    no_show = False
    now = datetime.now(timezone.utc)
    for appt in appointments:
        status = (appt.get("status") or "").lower()
        start = appt.get("start") or ""
        description = appt.get("description") or _code_text(appt.get("serviceType") or {}) or _code_text(
            (appt.get("appointmentType") or {}) if isinstance(appt.get("appointmentType"), dict) else {}
        )
        cleaned_appts.append({"status": status, "start": start, "description": description})
        if status in ("noshow", "cancelled"):
            no_show = True
        if start:
            try:
                when = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delta = (when - now).total_seconds()
                if 0 <= delta <= 14 * 86400:
                    upcoming = True
            except ValueError:
                pass

    age = _age(resource.get("birthDate"))
    signal = score_patient(active, acuity, age, upcoming, no_show)
    signal["do_not_contact"] = deceased
    signal["active_conditions"] = len(active)
    if deceased:
        signal["hints"].append("chart is marked deceased, do not outreach")

    condition_line = ", ".join(item["text"] for item in (active or cleaned_conditions)[:5]) or "none listed"
    appt_line = f"{len(cleaned_appts)} appointment records" if cleaned_appts else "no appointments returned"
    born = (resource.get("birthDate") or "")[:4]
    summary = (
        f"{name}, {resource.get('gender') or 'unknown gender'}"
        f"{f', born {born}' if born else ''}, {location or 'location unknown'}. "
        f"Language on file: {language or 'not set'}. Phone on file: {'yes' if phone else 'no'}. "
        f"Active conditions: {condition_line}. Appointments: {appt_line}."
    )
    if deceased:
        summary += " Record is flagged deceased."

    return {
        "id": f"patient:{patient_id}",
        "source": "fhir",
        "source_id": patient_id,
        "name": name,
        "contact": {"email": email, "phone": phone, "location": location},
        "context": {
            "summary": summary,
            "details": {
                "role_line": "patient",
                "gender": resource.get("gender") or "",
                "age": age,
                "language": language,
                "active_conditions": [item["text"] for item in active[:5]],
                "condition_count": len(active),
                "appointment_count": len(cleaned_appts),
                "has_phone": bool(phone),
                "deceased": deceased,
            },
        },
        "priority_signal": signal,
        "raw_payload": {
            "resourceType": "Patient",
            "id": patient_id,
            "gender": resource.get("gender"),
            "birthDate": resource.get("birthDate"),
            "city": city,
            "state": state,
            "language": language,
            "conditions": cleaned_conditions[:8],
            "appointments": cleaned_appts[:5],
        },
    }


def fetch_json(session, url, params=None):
    delay = 0.6
    last_error = None
    for attempt in range(1, 4):
        try:
            if attempt == 1 and _inject_timeout["armed"]:
                _inject_timeout["armed"] = False
                log.warning("deliberate timeout on first FHIR call, retrying")
                raise requests.Timeout("injected timeout before the sandbox call")
            resp = session.get(url, params=params, timeout=20)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} from sandbox")
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status and status != 429 and 400 <= status < 500:
                raise
            last_error = exc
            log.warning("fhir fetch attempt %s/3 failed for %s (%s)", attempt, url, exc)
            if attempt == 3:
                break
            time.sleep(delay)
            delay *= 2
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            log.warning("fhir fetch attempt %s/3 failed for %s (%s)", attempt, url, exc)
            if attempt == 3:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"fhir fetch gave up for {url}: {last_error}")


def _related(session, resource_type, patient_id):
    try:
        bundle = fetch_json(
            session,
            f"{FHIR_BASE}/{resource_type}",
            {"patient": patient_id, "_count": 5},
        )
    except (RuntimeError, requests.HTTPError) as exc:
        log.warning("could not load %s for %s: %s", resource_type, patient_id, exc)
        return []
    if bundle.get("resourceType") == "OperationOutcome":
        return []
    found = []
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get("resourceType") == resource_type:
            found.append(resource)
    return found


def _hydrate(session, resource):
    if not isinstance(resource, dict) or resource.get("resourceType") != "Patient":
        raise ValueError("entry is not a Patient resource")
    patient_id = str(resource.get("id") or "").strip()
    if not patient_id or not _human_name(resource):
        raise ValueError(f"{patient_id or 'patient'} has no usable id or name")
    conditions = _related(session, "Condition", patient_id)
    appointments = _related(session, "Appointment", patient_id)
    return normalize_patient(resource, conditions, appointments)


def _hydrate_worker(resource):
    session = requests.Session()
    session.headers["Accept"] = "application/fhir+json"
    return _hydrate(session, resource)


def load_patients(limit=16):
    _inject_timeout["armed"] = True
    session = requests.Session()
    session.headers["Accept"] = "application/fhir+json"
    bundle = fetch_json(session, f"{FHIR_BASE}/Patient", {"_count": limit + 4})
    entries = list(bundle.get("entry") or [])
    entries.append(
        {
            "resource": {
                "resourceType": "Patient",
                "id": "bad-entry-demo",
                "name": "not-a-human-name",
            }
        }
    )
    resources = []
    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict):
            resources.append(resource)

    records = []
    failures = []
    workers = min(4, max(len(resources), 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_hydrate_worker, resource): resource for resource in resources}
        for future in as_completed(futures):
            resource = futures[future]
            ref = resource.get("id") or "unknown-patient"
            try:
                records.append(future.result())
            except ValueError as exc:
                log.warning("dropped patient %s: %s", ref, exc)
                failures.append({"stage": "ingest", "ref": ref, "message": str(exc)})
            except Exception as exc:
                log.exception("patient %s failed", ref)
                failures.append({"stage": "ingest", "ref": ref, "message": str(exc)})

    records.sort(key=lambda item: item["source_id"])
    kept = [item for item in records if item["source_id"] != "bad-entry-demo"]
    if len(kept) > limit:
        kept = kept[:limit]
    log.info("fhir ingest kept %s patients, %s failures", len(kept), len(failures))
    return kept, failures
