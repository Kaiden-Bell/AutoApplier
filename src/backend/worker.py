"""
    Background worker: polls watched job sources, then matches and tailors new postings.

        PYTHONPATH=src python -m backend.worker

    One thread polls sources (cheap, frequent). AUTOAPPLIER_TASK_THREADS threads run queued tasks
    (matching and tailoring; tailoring waits on the LLM for up to a minute), so a slow tailoring
    never delays polling. An hourly cleanup removes rows nobody needs (matching.cleanup).
    Several worker processes may run at once: sources and tasks are claimed with row locks and
    leases (discovery.py, tasks.py).
"""

import logging
import signal
import threading
import time

from . import discovery, matching, tasks
from .config import Settings
from .db import Database
from .sources.http import HttpClient

log = logging.getLogger("autoapplier.worker")

CLEANUP_EVERY = 60 * 60


def handlers(database, http, settings):
    return {
        "match_posting": matching.MatchPosting(database),
        "tailor_match": matching.TailorMatch(database, http, settings),
    }


def _loop(name, stop, tick, step):
    while not stop.is_set():
        busy = False
        try:
            busy = step()
        except Exception:
            log.exception("%s tick failed", name)
        if not busy:
            stop.wait(tick)


def run(settings=None, database=None, http=None, stop=None):
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    http = http or HttpClient(requests_per_minute=settings.host_requests_per_minute)
    stop = stop or threading.Event()
    task_handlers = handlers(database, http, settings)
    last_cleanup = [0.0]

    def poll_step():
        discovery.run_due_polls(database, http, settings.poll_interval_seconds)
        if time.monotonic() - last_cleanup[0] >= CLEANUP_EVERY:
            last_cleanup[0] = time.monotonic()
            removed = matching.cleanup(database)
            if any(removed.values()):
                log.info("Cleanup removed %s", removed)
        return False

    def task_step():
        # Busy while there was work, so a backlog drains without waiting a tick per task.
        return tasks.run(database, task_handlers, limit=1) > 0

    log.info("Worker started; polling every %ss, %d task thread(s).",
             settings.poll_interval_seconds, settings.task_threads)
    threads = [threading.Thread(target=_loop, name="poller", daemon=True,
                                args=("poller", stop, settings.worker_tick_seconds, poll_step))]
    threads += [threading.Thread(target=_loop, name=f"tasks-{i}", daemon=True,
                                 args=("tasks", stop, settings.task_tick_seconds, task_step))
                for i in range(settings.task_threads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
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
