import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


LEADS_CSV = ROOT / "data" / "mock_leads.csv"
DB_PATH = ROOT / "data" / "intake.db"
LOG_PATH = ROOT / "logs" / "pipeline.log"
SAMPLE_PATH = ROOT / "output" / "sample_run.json"
FHIR_BASE = "https://r4.smarthealthit.org"
PORT = int(env("PORT", "5050") or "5050")
CRM_URL = env("CRM_URL", f"http://127.0.0.1:{PORT}/api/crm/intake")
CONTRACT_VERSION = "1.0"

PRODUCT = (
    "Deskline, an AI receptionist for clinics. It picks up missed calls, "
    "books the visit, and follows up with people who went quiet after hours."
)
