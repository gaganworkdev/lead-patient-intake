# Intake

Take-home pipeline for lead and patient intake. It reads the LinkedIn-style CSV, pulls synthetic patients from the public SMART FHIR sandbox, runs each record through a short agent loop, and posts a structured payload to a local CRM. The page is there so you can read the rationale, the draft, and the exact JSON the CRM stored.

Patients are synthetic. Nothing here is real PHI.

## Setup

Python 3.11 or newer.

Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Mac or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

You need one free model. No paid account.

- Groq: create a key at https://console.groq.com and set `GROQ_API_KEY`. Default model is `llama-3.3-70b-versatile`.
- Gemini: create a key in Google AI Studio and set `GEMINI_API_KEY`. Default model is `gemini-2.5-flash`.
- Ollama: install it, run `ollama pull llama3.2`, leave the keys blank. If nothing else is set and Ollama is up on port 11434, that is what gets used.

If more than one is available, Groq wins, then Gemini, then Ollama. Set `LLM_PROVIDER` to `groq`, `gemini`, or `ollama` if you want to force one.

Other env vars, all optional:

- `GROQ_MODEL`, `GEMINI_MODEL`, `OLLAMA_MODEL`, `OLLAMA_HOST` if you want a different model or host
- `PORT` defaults to `5050`
- `CRM_URL` defaults to `http://127.0.0.1:5050/api/crm/intake`

## Run

Windows:

```powershell
.venv\Scripts\python app.py
```

Mac or Linux, with the venv activated:

```bash
python app.py
```

Open http://127.0.0.1:5050 and click **Run pipeline**. The first run takes a while because each record is two model calls, plus the FHIR fetches.

The page has four views:

- Overview shows counts for the latest run and the live log
- Records is the qualification, the note, and the agent trace
- CRM inbox is what `POST /api/crm/intake` actually accepted
- Checks is where the deliberate failures show up

Same pipeline without clicking, which also writes `output/sample_run.json`:

```powershell
.venv\Scripts\python run_pipeline.py
```

```bash
python run_pipeline.py
```

`output/sample_run.json` already has a finished run: 18 leads, 16 sandbox patients, 34 accepted by the CRM. That run used local Ollama `llama3.2`.

`python test_basics.py` checks the bad-lead drop, the lead scoring split, the malformed patient, and the CRM contract. It does not call a model.

## What a run does on purpose

- Drops CSV row `L000` because the name is empty
- Times out the first FHIR call once, then retries and continues
- Rejects Patient `bad-entry-demo` because the name is not a FHIR name
- Posts one incomplete body to the CRM and expects `422`

Those three lines are under Checks. The 422 is the contract check, not a crashed record.

`WRITEUP.md` is the short note on the shape of the pipeline, what I would change at 10k records a day, and what would have to change for real PHI.
