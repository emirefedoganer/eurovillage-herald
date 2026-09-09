"""Durable email outbox. Every outgoing email (transactional or bulletin)
is enqueued here first; nothing is ever sent synchronously and directly
from inside a request handler. This is what makes delivery restart-safe,
redeploy-safe, and retry-safe WITHOUT introducing Redis/Celery or a
separate worker process:

  - process_outbox() is called opportunistically from routes that
    already touch relevant data (mirroring the exact pattern already
    used for scheduled issue publishing -- see app.py's
    _process_due_issues()): a handful of page loads is enough to drain
    the queue promptly on a site with any real traffic.
  - it is ALSO exposed at an authenticated admin action ("gönderim
    kuyruğunu şimdi işle") and at a bearer-token-protected endpoint
    (/internal/e-posta/isle) suitable for an optional Railway Cron job,
    for the rare case where natural traffic isn't enough (see app.py and
    the deployment docs this feature adds).

Idempotency: enqueue() takes an idempotency_key; a second enqueue() call
with a key that already has ANY job (queued/processing/sent) returns the
existing job instead of creating a new one. This is what prevents
duplicate confirmation/acknowledgement/campaign emails when a request
retries after a partial failure, or a redeploy interrupts a batch
mid-flight.

Crash recovery: a job stuck in "processing" (the process died between
sending and recording the result) is treated as retryable on the next
process_outbox() call. This trades a rare possible duplicate send (if
the email actually went out just before the crash) for never silently
losing a queued message -- the same at-least-once trade-off every
outbox/queue system without a two-phase commit with the provider makes.
"""
import uuid
from datetime import datetime, timezone

import mailer
import store

MAX_ATTEMPTS = 5


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enqueue(kind, to_address, subject, text_body, html_body=None, reply_to=None,
            campaign_id=None, idempotency_key=None):
    """kind: "transactional" | "bulletin"."""
    jobs = store.load_email_outbox()
    if idempotency_key:
        existing = next((j for j in jobs if j.get("idempotency_key") == idempotency_key), None)
        if existing:
            return existing
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "to": to_address,
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "reply_to": reply_to,
        "campaign_id": campaign_id,
        "idempotency_key": idempotency_key,
        "status": "queued",
        "retry_count": 0,
        "last_error": None,
        "created_at": _now_iso(),
        "sent_at": None,
    }
    jobs.append(job)
    store.save_email_outbox(jobs)
    return job


def _send_one(job):
    if job["kind"] == "transactional":
        return mailer.send_transactional_email(
            job["to"], job["subject"], job["text_body"], job.get("html_body"), reply_to=job.get("reply_to"),
        )
    return mailer.send_bulletin_email(job["to"], job["subject"], job["text_body"], job.get("html_body"))


def process_outbox(limit=50):
    """Sends every currently-queued (or stuck-processing) job, up to
    `limit`. Returns how many jobs were attempted."""
    jobs = store.load_email_outbox()
    for job in jobs:
        if job["status"] == "processing":
            job["status"] = "queued"  # recover from a crash mid-send; see module docstring

    attempted = 0
    for job in jobs:
        if attempted >= limit:
            break
        if job["status"] != "queued":
            continue
        attempted += 1
        job["status"] = "processing"
        ok, error = _send_one(job)
        if ok:
            job["status"] = "sent"
            job["sent_at"] = _now_iso()
            job["last_error"] = None
        else:
            job["retry_count"] += 1
            job["last_error"] = (error or "")[:500]
            job["status"] = "failed" if job["retry_count"] >= MAX_ATTEMPTS else "queued"
    if attempted or any(j["status"] == "queued" for j in jobs):
        store.save_email_outbox(jobs)
    return attempted


def retry_job(job_id):
    """Only a genuinely FAILED job can be manually retried -- a queued or
    already-sent job is left alone (retrying "everything" indiscriminately
    is exactly the "dangerous resend-everything control" this is meant to
    avoid)."""
    jobs = store.load_email_outbox()
    idx = next((i for i, j in enumerate(jobs) if j["id"] == job_id), None)
    if idx is None or jobs[idx]["status"] != "failed":
        return False
    jobs[idx]["status"] = "queued"
    jobs[idx]["retry_count"] = 0
    jobs[idx]["last_error"] = None
    store.save_email_outbox(jobs)
    return True


def stats():
    jobs = store.load_email_outbox()
    counts = {"queued": 0, "processing": 0, "sent": 0, "failed": 0}
    last_sent = None
    last_failed = None
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
        if j["status"] == "sent" and j.get("sent_at") and (not last_sent or j["sent_at"] > last_sent["sent_at"]):
            last_sent = j
        if j["status"] == "failed" and (not last_failed or j["created_at"] > last_failed["created_at"]):
            last_failed = j
    return {"counts": counts, "last_sent": last_sent, "last_failed": last_failed, "total": len(jobs)}


def failed_jobs():
    return [j for j in store.load_email_outbox() if j["status"] == "failed"]


def jobs_for_campaign(campaign_id):
    return [j for j in store.load_email_outbox() if j.get("campaign_id") == campaign_id]
