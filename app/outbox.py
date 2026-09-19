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
from datetime import datetime, timedelta, timezone

import mailer
import store

MAX_ATTEMPTS = 5

# Minutes to wait before retrying, indexed by retry_count (1st retry waits
# 1 minute, 2nd waits 2, ... capped at the last entry). Short enough that a
# real transient failure (a 5xx, a rate limit) recovers within one normal
# traffic-driven drain cycle, long enough that a burst of page loads
# doesn't hammer the provider with the same doomed request every few
# seconds -- see app.py's admin email-system view, which surfaces this as
# "next retry time" per failed job.
_BACKOFF_MINUTES = [1, 2, 5, 15, 30]


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _next_attempt_at(retry_count):
    minutes = _BACKOFF_MINUTES[min(retry_count, len(_BACKOFF_MINUTES) - 1)]
    return (_now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def enqueue(kind, to_address, subject, text_body, html_body=None, reply_to=None,
            campaign_id=None, idempotency_key=None, subscriber_id=None, target_preference=None,
            headers=None):
    """kind: "transactional" | "bulletin". subscriber_id/target_preference
    are set only for a real bulletin campaign send (never a test send --
    see bulletins.send_test()) and let process_outbox() re-check the
    recipient is STILL eligible at the moment the job actually sends, not
    just when it was enqueued -- see store.is_subscriber_eligible()."""
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
        "headers": headers,
        "campaign_id": campaign_id,
        "idempotency_key": idempotency_key,
        "subscriber_id": subscriber_id,
        "target_preference": target_preference,
        "status": "queued",
        "retry_count": 0,
        "last_error": None,
        "created_at": _now_iso(),
        "sent_at": None,
        # The provider's own message id (e.g. Resend's) once actually
        # sent -- None for the fake backend, for SMTP (smtplib doesn't
        # hand one back), and for any job not yet sent.
        "provider_message_id": None,
        # Set once a Resend delivery-status webhook event ties back to
        # this job via provider_message_id -- see app.py's
        # /internal/webhooks/resend and subscriptions.record_bounce()/
        # record_complaint(). Purely informational for the admin UI;
        # never read to decide whether/what to send.
        "provider_event": None,
        # Exponential-ish backoff so a retry doesn't hammer the provider
        # on the very next outbox drain -- see _next_attempt_delay().
        "next_attempt_at": None,
    }
    jobs.append(job)
    store.save_email_outbox(jobs)
    return job


def _send_one(job):
    """Returns (ok, error, message_id, permanent) -- see mailer._send()."""
    if job["kind"] == "transactional":
        return mailer.send_transactional_email(
            job["to"], job["subject"], job["text_body"], job.get("html_body"), reply_to=job.get("reply_to"),
        )
    return mailer.send_bulletin_email(
        job["to"], job["subject"], job["text_body"], job.get("html_body"),
        headers=job.get("headers"), reply_to=job.get("reply_to"),
    )


def process_outbox(limit=50):
    """Sends every currently-queued (or stuck-processing) job whose
    next_attempt_at has arrived, up to `limit`. Returns how many jobs were
    attempted (a bulletin job SKIPPED for eligibility counts as attempted;
    it consumed a slot and is resolved, just never handed to the mailer).

    A job whose failure mailer.py classified as `permanent` (invalid
    credentials, an unverified sending domain, a malformed payload -- see
    mailer._is_permanent_resend_error()) is marked "failed" immediately,
    on its very first such attempt, rather than being requeued to burn
    through MAX_ATTEMPTS retries on a condition no retry can fix. Every
    other failure is requeued with a backoff delay (next_attempt_at) until
    MAX_ATTEMPTS is reached."""
    jobs = store.load_email_outbox()
    for job in jobs:
        if job["status"] == "processing":
            job["status"] = "queued"  # recover from a crash mid-send; see module docstring
        job.setdefault("subscriber_id", None)
        job.setdefault("target_preference", None)
        job.setdefault("provider_event", None)
        job.setdefault("next_attempt_at", None)

    now_iso = _now_iso()
    attempted = 0
    for job in jobs:
        if attempted >= limit:
            break
        if job["status"] != "queued":
            continue
        if job.get("next_attempt_at") and job["next_attempt_at"] > now_iso:
            continue  # backoff window hasn't elapsed yet

        # A real bulletin campaign job (subscriber_id set -- never a test
        # send, see bulletins.send_test()) is re-checked against the
        # SUBSCRIBER'S CURRENT status/preference right here, not just
        # whatever it was when the campaign was queued -- see
        # store.is_subscriber_eligible().
        if job["kind"] == "bulletin" and job.get("subscriber_id"):
            if not store.is_subscriber_eligible(job["subscriber_id"], job.get("target_preference")):
                attempted += 1
                job["status"] = "skipped"
                job["last_error"] = "Alıcı artık uygun değil (abonelikten çıkmış/onaysız/tercih kapalı) -- gönderim atlandı."
                job["next_attempt_at"] = None
                continue

        attempted += 1
        job["status"] = "processing"
        ok, error, message_id, permanent = _send_one(job)
        if ok:
            job["status"] = "sent"
            job["sent_at"] = _now_iso()
            job["last_error"] = None
            job["provider_message_id"] = message_id
            job["next_attempt_at"] = None
        else:
            job["retry_count"] += 1
            job["last_error"] = (error or "")[:500]
            if permanent or job["retry_count"] >= MAX_ATTEMPTS:
                job["status"] = "failed"
                job["next_attempt_at"] = None
            else:
                job["status"] = "queued"
                job["next_attempt_at"] = _next_attempt_at(job["retry_count"])
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
    jobs[idx]["next_attempt_at"] = None
    store.save_email_outbox(jobs)
    return True


def mark_provider_event(provider_message_id, event_type):
    """Called by app.py's /internal/webhooks/resend handler to annotate
    the outbox job a delivery-status event refers to (matched via the
    Resend message id recorded when the job was sent). Purely
    informational for the admin UI -- never read to decide whether or
    what to send; eligibility is decided solely by the subscriber record
    (see subscriptions.record_bounce()/record_complaint() and
    store.is_subscriber_eligible()). A no-op if no job matches (an event
    for a message this outbox never sent, or sent before this field
    existed) -- never an error, webhook delivery must always 200."""
    if not provider_message_id:
        return False
    jobs = store.load_email_outbox()
    idx = next((i for i, j in enumerate(jobs) if j.get("provider_message_id") == provider_message_id), None)
    if idx is None:
        return False
    jobs[idx]["provider_event"] = event_type
    store.save_email_outbox(jobs)
    return True


def stats():
    jobs = store.load_email_outbox()
    counts = {"queued": 0, "processing": 0, "sent": 0, "failed": 0, "skipped": 0}
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


def retrying_jobs():
    """Queued jobs that have already failed at least once and are waiting
    for their next_attempt_at -- shown in the admin email view."""
    return [j for j in store.load_email_outbox() if j["status"] == "queued" and j.get("retry_count", 0) > 0]


def jobs_for_campaign(campaign_id):
    return [j for j in store.load_email_outbox() if j.get("campaign_id") == campaign_id]
