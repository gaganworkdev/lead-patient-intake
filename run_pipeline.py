import time

import requests

from app import app
from config import PORT
from pipeline import run


def _wait_for_crm():
    url = f"http://127.0.0.1:{PORT}/api/health"
    for _ in range(40):
        try:
            resp = requests.get(url, timeout=1)
            if resp.ok:
                return
        except requests.RequestException:
            time.sleep(0.25)
    raise SystemExit("The local CRM did not come up. Check the port in .env.")


if __name__ == "__main__":
    import threading

    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
        daemon=True,
    ).start()
    _wait_for_crm()
    result = run()
    if not result.get("ok"):
        raise SystemExit(result.get("error") or "run failed")
