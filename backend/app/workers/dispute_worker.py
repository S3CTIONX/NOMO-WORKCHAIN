"""
workers/dispute_worker.py
==========================
Celery worker that handles dispute lifecycle events.

Triggered by: listener.py when WorkChain.DisputeRaised is detected.
Responsibilities:
  - Create the dispute record in the database
  - Notify both parties via Telegram
  - Escalate to arbitrator if evidence window closes with no resolution
"""

import asyncio
import logging
from datetime import datetime, timedelta

from celery import Celery

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Reuse the same Celery app instance if available, else create
try:
    from app.workers.verification_worker import celery_app
except ImportError:
    from celery import Celery
    celery_app = Celery(
        "workchain_dispute",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="workchain.handle_dispute_opened",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def handle_dispute_opened(self, job_id: int, raised_by: str):
    """
    Called immediately when DisputeRaised event fires.
    Creates DB record and notifies both parties.
    Also schedules the evidence_window_check task.
    """
    logger.info(f"Handling dispute opened: job={job_id} raised_by={raised_by}")

    try:
        _run_async(_open_dispute(job_id=job_id, raised_by=raised_by))

        # Schedule evidence window check after the window closes
        window_hours = settings.dispute_evidence_window_hours
        check_evidence_window.apply_async(
            kwargs={"job_id": job_id},
            countdown=window_hours * 3600,
        )

        logger.info(
            f"Evidence window check scheduled in {window_hours}h for job={job_id}"
        )

    except Exception as exc:
        logger.error(f"handle_dispute_opened failed: job={job_id} error={exc}", exc_info=True)
        raise self.retry(exc=exc)


async def _open_dispute(job_id: int, raised_by: str):
    """Create dispute record in DB."""
    from app.db.session import AsyncSessionLocal
    from app.services.dispute.service import DisputeService

    async with AsyncSessionLocal() as db:
        service = DisputeService(db)
        await service.open_dispute(job_id=job_id, raised_by=raised_by)
        await db.commit()


@celery_app.task(
    name="workchain.check_evidence_window",
    bind=True,
    max_retries=1,
)
def check_evidence_window(self, job_id: int):
    """
    Runs after the evidence window closes.
    If dispute is still open → alert arbitrator to resolve.
    If already resolved → no-op.
    """
    logger.info(f"Evidence window check: job={job_id}")
    _run_async(_check_and_escalate(job_id=job_id))


async def _check_and_escalate(job_id: int):
    """
    If dispute is still Open after the evidence window,
    send the arbitrator a Telegram alert to review and resolve.
    """
    from app.db.session import AsyncSessionLocal
    from app.db.schemas import DisputeStatus
    from app.services.dispute.service import DisputeService
    from app.services.notifications.notifier import _send_telegram

    async with AsyncSessionLocal() as db:
        service = DisputeService(db)
        record = await service.get_dispute(job_id)

        if not record:
            logger.warning(f"No dispute record found for job {job_id}")
            return

        if record.status != DisputeStatus.OPEN:
            logger.info(f"Dispute for job {job_id} already resolved — no escalation needed")
            return

        # Evidence window has closed — escalate to arbitrator
        logger.info(f"Escalating dispute to arbitrator: job={job_id}")

        from app.services.blockchain.client import get_blockchain_client
        client = get_blockchain_client()
        dispute = await client.get_dispute_by_job(job_id)
        evidence_count = dispute["evidence_count"] if dispute else 0

        message = (
            f"🔴 *Arbitration Required*\n"
            f"Job `#{job_id}` dispute evidence window has closed.\n"
            f"Evidence items submitted: {evidence_count}\n"
            f"Arbitrator action needed — call `resolve()` on DisputeResolver.\n"
            f"Remaining escrow: {record.remaining_escrow_wei} wei"
        )
        await _send_telegram(message)
        logger.info(f"Arbitrator alerted for job={job_id}")


@celery_app.task(name="workchain.handle_dispute_resolved")
def handle_dispute_resolved(
    job_id: int,
    employer_award_wei: int,
    worker_award_wei: int,
    resolution_note: str,
):
    """
    Called when DisputeResolved event is detected on-chain.
    Updates DB record to Resolved status.
    """
    logger.info(f"Dispute resolved on-chain: job={job_id}")
    _run_async(_mark_resolved(
        job_id=job_id,
        employer_award_wei=employer_award_wei,
        worker_award_wei=worker_award_wei,
        resolution_note=resolution_note,
    ))


async def _mark_resolved(
    job_id: int,
    employer_award_wei: int,
    worker_award_wei: int,
    resolution_note: str,
):
    from app.db.session import AsyncSessionLocal
    from app.services.dispute.service import DisputeService

    async with AsyncSessionLocal() as db:
        service = DisputeService(db)
        await service.resolve(
            job_id=job_id,
            employer_amount_wei=employer_award_wei,
            worker_amount_wei=worker_award_wei,
            resolution_note=resolution_note,
            resolved_by="on-chain",
        )
        await db.commit()
