"""
    Background worker: polls watched job sources on a schedule.

        PYTHONPATH=src python -m backend.worker

    Phase C adds matching and tailoring of the new postings each poll returns.
    Several workers may run at once; sources are claimed with row locks and leases (discovery.py).
"""

import logging
import signal
import threading

from . import discovery
from .config import Settings
from .db import Database
from .sources.http import HttpClient

log = logging.getLogger("autoapplier.worker")


def run(settings=None, database=None, http=None, stop=None):
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    http = http or HttpClient(requests_per_minute=settings.host_requests_per_minute)
    stop = stop or threading.Event()
    log.info("Worker started; polling every %ss (tick %ss).", settings.poll_interval_seconds,
             settings.worker_tick_seconds)
    while not stop.is_set():
        try:
            discovery.run_due_polls(database, http, settings.poll_interval_seconds)
        except Exception:
            log.exception("Worker tick failed")
        stop.wait(settings.worker_tick_seconds)
    http.close()
    database.dispose()
    log.info("Worker stopped.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("AUTOAPPLIER_DATABASE_URL is not set.")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(settings, stop=stop)


if __name__ == "__main__":
    main()
