"""
    A small durable task queue on the `tasks` table.

    - enqueue() inside the caller's transaction, so work is never lost between "a posting was
      saved" and "matching it was scheduled". An optional key makes enqueueing idempotent.
    - claim() leases due tasks with SELECT ... FOR UPDATE SKIP LOCKED, so several worker threads or
      processes never run the same task, and a crashed worker's task is retried after its lease.
    - Handlers return normally to finish a task. Raising Retry reschedules it; any other exception
      is retried with exponential backoff until max_attempts, after which the task is marked dead
      and the handler's on_give_up hook runs.
"""

import json
import logging
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from .models import Task, utcnow

log = logging.getLogger("autoapplier.tasks")

LEASE = timedelta(minutes=15)       # longer than one tailoring run (LLM + compile)
MAX_BACKOFF = timedelta(hours=6)


class Retry(Exception):
    """Raised by a handler to run the task again after `seconds`, without counting a failure."""

    def __init__(self, seconds, reason=""):
        super().__init__(reason or f"retry in {seconds}s")
        self.seconds = seconds


@dataclass(frozen=True)
class ClaimedTask:
    id: int
    kind: str
    payload: dict
    attempts: int
    max_attempts: int


def enqueue(session, kind, payload=None, key=None, run_after=None, max_attempts=5):
    """
        Desc:
            Adds a task in the caller's transaction. With a key, does nothing if a task with that
            key already exists (queued, running, or dead).
        Returns:
            bool: True if a task was added.
    """
    if key is not None and session.scalar(select(Task.id).where(Task.key == key)) is not None:
        return False
    task = Task(kind=kind, payload_json=json.dumps(payload or {}), key=key,
                run_after=run_after or utcnow(), max_attempts=max_attempts)
    try:
        with session.begin_nested():
            session.add(task)
    except IntegrityError:   # another transaction added the same key concurrently
        return False
    return True


def claim(db, kinds=None, limit=1, now=None):
    """Leases up to `limit` due tasks (optionally of the given kinds)."""
    now = now or utcnow()
    with db.session() as session:
        query = (select(Task)
                 .where(Task.dead.is_(False), Task.run_after <= now,
                        or_(Task.locked_until.is_(None), Task.locked_until < now))
                 .order_by(Task.run_after, Task.id)
                 .limit(limit)
                 .with_for_update(skip_locked=True))
        if kinds:
            query = query.where(Task.kind.in_(kinds))
        claimed = []
        for task in session.scalars(query):
            task.locked_until = now + LEASE
            task.attempts += 1
            claimed.append(ClaimedTask(task.id, task.kind, json.loads(task.payload_json),
                                       task.attempts, task.max_attempts))
        return claimed


def complete(db, task_id):
    with db.session() as session:
        session.execute(delete(Task).where(Task.id == task_id))


def reschedule(db, task_id, seconds, reason="", count_attempt=False):
    with db.session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return
        if not count_attempt:
            task.attempts = max(0, task.attempts - 1)
        task.run_after = utcnow() + timedelta(seconds=seconds)
        task.locked_until = None
        task.last_error = reason[:1000] or None


def fail(db, claimed, error):
    """Records a failed attempt: backs off, or marks the task dead. Returns True if dead."""
    with db.session() as session:
        task = session.get(Task, claimed.id)
        if task is None:
            return True
        task.last_error = error[:1000]
        task.locked_until = None
        if task.attempts >= task.max_attempts:
            task.dead = True
            return True
        backoff = timedelta(seconds=30) * (2 ** (task.attempts - 1)) * random.uniform(0.9, 1.1)
        task.run_after = utcnow() + min(backoff, MAX_BACKOFF)
        return False


def run(db, handlers, limit=1, kinds=None):
    """
        Desc:
            Claims and runs up to `limit` tasks. `handlers` maps kind -> handler object with
            run(payload) and optional on_give_up(payload, error).
        Returns:
            int: number of tasks claimed.
    """
    claimed = claim(db, kinds=kinds or list(handlers), limit=limit)
    for task in claimed:
        handler = handlers[task.kind]
        try:
            handler.run(task.payload)
        except Retry as e:
            reschedule(db, task.id, e.seconds, str(e))
            continue
        except Exception as e:
            log.exception("Task %s (%s) failed on attempt %d", task.id, task.kind, task.attempts)
            message = f"{type(e).__name__}: {e}"
            if fail(db, task, message):
                log.error("Task %s (%s) gave up after %d attempts", task.id, task.kind, task.attempts)
                give_up = getattr(handler, "on_give_up", None)
                if give_up:
                    try:
                        give_up(task.payload, message)
                    except Exception:
                        log.exception("on_give_up for task %s failed", task.id)
            continue
        complete(db, task.id)
    return len(claimed)
