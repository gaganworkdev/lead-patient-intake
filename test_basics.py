from contract import build_payload, validate_payload
from ingest import load_leads, normalize_patient, score_lead


def test_bad_lead_is_dropped():
    records, failures = load_leads()
    ids = [item["source_id"] for item in records]
    assert "L000" not in ids
    assert "L001" in ids
    assert any(item["ref"] == "L000" for item in failures)
    assert len(records) >= 18


def test_warmth_splits_the_list():
    hot = score_lead(
        {
            "job_title": "VP Marketing",
            "industry": "Healthcare",
            "company_size": "201-500",
            "recent_activity": "Attended a HubSpot webinar last week",
            "connection_note": "Requested a demo via website form",
            "linkedin_headline": "",
        }
    )
    cold = score_lead(
        {
            "job_title": "Owner",
            "industry": "Dental",
            "company_size": "1-10",
            "recent_activity": "No recent activity",
            "connection_note": "No note, imported from trade show list",
            "linkedin_headline": "Family dentist",
        }
    )
    assert hot["warmth"] == "demo"
    assert hot["heuristic_score"] >= 78
    assert cold["warmth"] == "cold"
    assert cold["heuristic_score"] < 50


def test_malformed_patient_raises():
    try:
        normalize_patient({"resourceType": "Patient", "id": "bad-entry-demo", "name": "nope"}, [], [])
    except ValueError as exc:
        assert "bad-entry-demo" in str(exc)
    else:
        raise AssertionError("malformed patient should fail")


def test_contract_rejects_a_partial_body():
    errors = validate_payload({"contract_version": "1.0", "note": "incomplete"})
    assert errors
    assert any("missing" in item for item in errors)


def test_contract_accepts_a_full_payload():
    record = {
        "id": "lead:L001",
        "source": "linkedin",
        "name": "Rebecca Hart",
        "contact": {"email": None, "phone": None, "location": "Austin TX"},
        "context": {"details": {"job_title": "Practice Manager"}},
    }
    result = {
        "qualification": {"label": "warm", "score": 64, "intent": "curious about front desk load"},
        "action": {"channel": "linkedin", "tone": "conversational", "escalate": False, "queue": "sales_nurture"},
        "message": {
            "subject": "Front desk after hours",
            "body": "Rebecca, practices your size usually lose the calls that come in after five. Happy to show how a clinic in a similar spot caught them without hiring.",
        },
        "rationale": "She runs operations at a small clinic and recently engaged with receptionist content, so this is a real fit.",
        "trace": [
            {"step": "signals", "detail": "manager title and a live activity"},
            {"step": "qualify", "detail": "warm, score 64"},
            {"step": "action", "detail": "nurture on LinkedIn"},
        ],
    }
    payload = build_payload("run-test", record, result, "2026-10-07T12:00:00+00:00")
    assert validate_payload(payload) == []


if __name__ == "__main__":
    test_bad_lead_is_dropped()
    test_warmth_splits_the_list()
    test_malformed_patient_raises()
    test_contract_rejects_a_partial_body()
    test_contract_accepts_a_full_payload()
    print("ok")
