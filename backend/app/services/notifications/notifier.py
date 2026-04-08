"""
services/notifications/notifier.py
====================================
Telegram notifications for workers and employers.

All notifications are fire-and-forget — a failed notification never
blocks a verification or dispute flow. Errors are logged, not raised.

Setup:
  1. Create a Telegram bot via @BotFather → get TELEGRAM_BOT_TOKEN
  2. Add the bot to a group or get a user chat ID
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env

For per-user notifications (worker/employer specific), you would need
to store each user's Telegram chat_id when they register. V1 uses a
single alert channel — all events go to one group/channel.
"""

import logging
from typing import Optional

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def _send_telegram(message: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a plain-text message to a Telegram chat.
    Returns True on success, False on failure (never raises).
    """
    token = settings.telegram_bot_token
    target = chat_id or settings.telegram_chat_id

    if not token or not target:
        logger.debug("Telegram not configured — skipping notification")
        return False

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id":    target,
        "text":       message,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.warning(
                    f"Telegram notification failed: "
                    f"{response.status_code} {response.text}"
                )
                return False
        return True
    except Exception as e:
        logger.error(f"Telegram notification error: {e}")
        return False


def _truncate_address(address: str) -> str:
    """0x1234...5678 format for display."""
    if len(address) >= 10:
        return f"{address[:6]}...{address[-4:]}"
    return address


def _wei_to_mon(wei: int) -> str:
    """Convert wei to MON with 4 decimal places."""
    mon = wei / 10**18
    return f"{mon:.4f} MON"


# ── Notification functions ────────────────────────────────────────────────────

async def notify_proof_submitted(
    job_id: int,
    milestone_index: int,
    worker_address: str,
    proof_type: int,
) -> None:
    proof_type_labels = {0: "GitHub", 1: "File Hash", 2: "Link", 3: "Manual"}
    label = proof_type_labels.get(proof_type, "Unknown")
    worker = _truncate_address(worker_address)

    message = (
        f"📋 *Proof Submitted*\n"
        f"Job `#{job_id}` · Milestone `{milestone_index}`\n"
        f"Worker: `{worker}`\n"
        f"Type: {label}\n"
        f"Status: Awaiting verification..."
    )
    await _send_telegram(message)
    logger.info(f"Notified: proof submitted job={job_id} milestone={milestone_index}")


async def notify_proof_verified(
    job_id: int,
    milestone_index: int,
    worker_address: str,
    amount_wei: int,
) -> None:
    worker = _truncate_address(worker_address)
    amount = _wei_to_mon(amount_wei)

    message = (
        f"✅ *Milestone Verified & Paid*\n"
        f"Job `#{job_id}` · Milestone `{milestone_index}`\n"
        f"Worker: `{worker}`\n"
        f"Amount released: *{amount}*\n"
        f"Funds transferred automatically on Monad."
    )
    await _send_telegram(message)
    logger.info(f"Notified: proof verified job={job_id} milestone={milestone_index}")


async def notify_proof_rejected(
    job_id: int,
    milestone_index: int,
    worker_address: str,
    reason: str,
) -> None:
    worker = _truncate_address(worker_address)

    message = (
        f"❌ *Proof Rejected*\n"
        f"Job `#{job_id}` · Milestone `{milestone_index}`\n"
        f"Worker: `{worker}`\n"
        f"Reason: {reason}\n"
        f"Worker must resubmit proof."
    )
    await _send_telegram(message)
    logger.info(f"Notified: proof rejected job={job_id} milestone={milestone_index}")


async def notify_milestone_released(
    job_id: int,
    milestone_index: int,
    worker_address: str,
    amount_wei: int,
) -> None:
    worker = _truncate_address(worker_address)
    amount = _wei_to_mon(amount_wei)

    message = (
        f"💸 *Milestone Payment Sent*\n"
        f"Job `#{job_id}` · Milestone `{milestone_index}`\n"
        f"Worker: `{worker}`\n"
        f"Amount: *{amount}*"
    )
    await _send_telegram(message)


async def notify_job_completed(job_id: int) -> None:
    message = (
        f"🏁 *Job Complete*\n"
        f"Job `#{job_id}` — all milestones released.\n"
        f"Employer can now submit an on-chain rating."
    )
    await _send_telegram(message)
    logger.info(f"Notified: job completed job={job_id}")


async def notify_dispute_opened(
    job_id: int,
    employer: str,
    worker: str,
    raised_by: str,
) -> None:
    raiser = _truncate_address(raised_by)

    message = (
        f"⚠️ *Dispute Raised*\n"
        f"Job `#{job_id}`\n"
        f"Raised by: `{raiser}`\n"
        f"Both parties have {settings.dispute_evidence_window_hours}h to submit evidence.\n"
        f"Arbitration is in progress."
    )
    await _send_telegram(message)
    logger.info(f"Notified: dispute opened job={job_id}")


async def notify_dispute_resolved(
    job_id: int,
    employer: str,
    worker: str,
    employer_award_wei: int,
    worker_award_wei: int,
    note: str,
) -> None:
    emp_amount = _wei_to_mon(employer_award_wei)
    wrk_amount = _wei_to_mon(worker_award_wei)

    message = (
        f"⚖️ *Dispute Resolved*\n"
        f"Job `#{job_id}`\n"
        f"Employer awarded: *{emp_amount}*\n"
        f"Worker awarded: *{wrk_amount}*\n"
        f"Note: {note}"
    )
    await _send_telegram(message)
    logger.info(f"Notified: dispute resolved job={job_id}")


async def notify_milestone_proposal(
    job_id: int,
    proposal_id: int,
    worker_address: str,
    description: str,
    amount_wei: int,
) -> None:
    """Notify worker that employer has proposed a new milestone."""
    worker = _truncate_address(worker_address)
    amount = _wei_to_mon(amount_wei)

    message = (
        f"📝 *New Milestone Proposed*\n"
        f"Job `#{job_id}` · Proposal `#{proposal_id}`\n"
        f"Description: {description}\n"
        f"Amount: *{amount}*\n"
        f"Worker `{worker}` must approve or reject within 72h."
    )
    await _send_telegram(message)


async def notify_milestone_proposal_expired(
    job_id: int,
    proposal_id: int,
) -> None:
    message = (
        f"⏰ *Milestone Proposal Expired*\n"
        f"Job `#{job_id}` · Proposal `#{proposal_id}`\n"
        f"Worker did not respond in time. Employer deposit refunded."
    )
    await _send_telegram(message)
