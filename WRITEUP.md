# Notes

## How it's put together

The two sources do not look alike, so I didn't try to make the agent understand CSV rows and FHIR bundles. Ingest normalizes both into one record: id, source, name, contact, a short context blurb, a priority signal, and a trimmed raw payload. The agent only sees that.

The loop is deliberately a few small steps instead of one prompt that returns the whole decision. Code pulls signals first (title, warmth of the LinkedIn note, active conditions, whether a phone exists). The model then qualifies the record and writes a rationale. A policy function picks channel, tone, queue, and whether a person should take it. The model writes the note with that decision in front of it. A local review checks length, placeholders, SSN-shaped numbers, and that a patient note is not addressed to the patient. If review fails, it gets one rewrite. If that also fails, the record is logged and not replaced with a canned message.

Leads and patients share the loop and then branch. Leads become a draft a rep could send on LinkedIn, because the export has no email. Patients become an internal handoff. I don't want a sandbox chart turned into an SMS, even though the people are synthetic.

The CRM is a Flask route with an explicit contract. Idempotency key is the run id plus the record id, so a retry does not double-insert. The inbox in the UI is whatever that route stored, not a copy the pipeline kept for itself.

## If this were 10,000 a day

I would not fan out one Condition and one Appointment search per patient on a public server. Bulk export, or a nightly pull into our own store, and then the agent reads from there. Each record becomes a queue message. Workers call the model with a concurrency cap and a token budget, and anything that fails review or times out goes to a dead-letter queue with the trace attached. The CRM post we already make is idempotent, so workers can retry safely. Qualification can be skipped when the source hash has not changed. At this volume I would also batch the CRM writes, but I would keep the same payload so the contract does not change under the workers.

The slow part will be the model, not Flask. Two short calls per record is the cost I was willing to pay here so the rationale and the note can be judged separately. At 10k/day that is worth caching and a smaller model for the first pass, with the stronger model only on hot and urgent records.

## If the chart was real PHI

This sandbox run still drops SSN, driver's license, passport, MRN, and street address before anything is stored or sent to a model. The prompt gets city, age, condition names, and whether a phone exists. The digits stay in the CRM row.

That is not enough for real patients. A public Groq or Gemini key is not a BAA. Production would call a model under a BAA, inside our network, with the minimum fields the decision needs. Often that is an acuity band plus "has a future appointment," and the condition names stay in the EHR. The desk would need auth, an audit log of who opened a chart, encryption for the queue and the database, and a retention window. Patient outreach would not leave as an automated SMS from this pipeline. A coordinator sends it, from a system that is allowed to have the number.

The failure cases in this repo (bad row, injected timeout with retry, malformed Patient, rejected CRM payload) are the same places a PHI pipeline has to stay loud instead of dropping a record on the floor.
