"""
workers/timeout_worker.py
==========================
Celery beat scheduler for time-based background jobs.

Handles two timeout scenarios:
  1. Milestone proposal expiry (MilestoneManager — 72h window)
     → Calls expireProposal() on-chain if worker hasn't responded
  2. Verification timeout
     → Rejects a proof that the backend took too long to verify

Run alongside the main worker:
    celery -A app.workers.timeout_worker beat --loglevel=info
    celery -A app.workers.timeout_worker worker --loglevel=info
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from app.workers.verification_worker import celery_app
except ImportError:
    from celery import Celery
    celery_app = Celery(
        "workchain_timeout",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )

# ── Periodic schedule (Celery Beat) ──────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Check for expired milestone proposals every 30 minutes
    "expire-stale-proposals": {
        "task":     "workchain.expire_stale_proposals",
        "schedule": crontab(minute="*/30"),
    },
    # Check for stuck pending verifications every 10 minutes
    "timeout-stuck-verifications": {
        "task":     "workchain.timeout_stuck_verifications",
        "schedule": crontab(minute="*/10"),
    },
}


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Task: expire stale milestone proposals ────────────────────────────────────

@celery_app.task(name="workchain.expire_stale_proposals")
def expire_stale_proposals():
    """
    Runs every 30 minutes.
    Fetches all pending proposals from the DB,
    checks if their on-chain window has passed,
    and calls expireProposal() for each one.
    """
    logger.info("Running expire_stale_proposals sweep")
    _run_async(_expire_proposals())


async def _expire_proposals():
    from app.db.session import AsyncSessionLocal
    from app.db.models import MilestoneProposalRecord
    from app.services.blockchain.client import get_blockchain_client
    from app.services.notifications.notifier import notify_milestone_proposal_expired
    from sqlalchemy import select

    client = get_blockchain_client()

    async with AsyncSessionLocal() as db:
        # Fetch all proposals the DB thinks are still pending
        result = await db.execute(
            select(MilestoneProposalRecord).where(
                MilestoneProposalRecord.status == "pending"
            )
        )
        pending = result.scalars().all()

        now = datetime.now(timezone.utc)
        expired_count = 0

        for proposal in pending:
            if proposal.expires_at and proposal.expires_at < now:
                logger.info(
                    f"Expiring stale proposal: id={proposal.proposal_id_onchain} "
                    f"job={proposal.job_id}"
                )
                try:
                    # Call expireProposal() on MilestoneManager
                    fn = client.milestone_manager.functions.expireProposal(
                        proposal.proposal_id_onchain
                    )
                    await client._send_tx(fn, client._owner)

                    proposal.status = "expired"
                    proposal.updated_at = now
                    expired_count += 1

                    await notify_milestone_proposal_expired(
                        job_id=proposal.job_id,
                        proposal_id=proposal.proposal_id_onchain,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to expire proposal {proposal.proposal_id_onchain}: {e}"
                    )

        await db.commit()
        if expired_count:
            logger.info(f"Expired {expired_count} stale proposals")


# ── Task: timeout stuck verifications ─────────────────────────────────────────

@celery_app.task(name="workchain.timeout_stuck_verifications")
def timeout_stuck_verifications():
    """
    Runs every 10 minutes.
    Finds verification records that have been Pending for longer than
    verification_timeout_seconds and rejects them.

    This handles cases where the verification worker crashed mid-task
    or the proof URI was unreachable.
    """
    logger.info("Running timeout_stuck_verifications sweep")
    _run_async(_timeout_verifications())


async def _timeout_verifications():
    from app.db.session import AsyncSessionLocal
    from app.db.models import VerificationRecord
    from app.db.schemas import VerificationStatus
    from app.services.blockchain.client import get_blockchain_client
    from sqlalchemy import select

    client = get_blockchain_client()
    timeout_delta = timedelta(seconds=settings.verification_timeout_seconds)
    cutoff = datetime.now(timezone.utc) - timeout_delta

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(VerificationRecord).where(
                VerificationRecord.status == VerificationStatus.PENDING,
                VerificationRecord.created_at < cutoff,
            )
        )
        stuck = result.scalars().all()

        timed_out_count = 0
        for record in stuck:
            logger.warning(
                f"Timing out stuck verification: job={record.job_id} "
                f"milestone={record.milestone_index} "
                f"age={(datetime.now(timezone.utc) - record.created_at).seconds}s"
            )
            try:
                tx_hash = await client.reject_proof(
                    job_id=record.job_id,
                    milestone_index=record.milestone_index,
                    reason="Verification timed out — please resubmit your proof",
                )
                record.status = VerificationStatus.REJECTED
                record.rejection_reason = "Verification timeout"
                record.updated_at = datetime.now(timezone.utc)
                timed_out_count += 1

                logger.info(
                    f"Timed out verification: job={record.job_id} "
                    f"milestone={record.milestone_index} tx={tx_hash}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to timeout verification job={record.job_id}: {e}"
                )

        await db.commit()
        if timed_out_count:
            logger.info(f"Timed out {timed_out_count} stuck verifications")
