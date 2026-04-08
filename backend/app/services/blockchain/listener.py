"""
services/blockchain/listener.py
================================
Polls Monad for on-chain events and dispatches them to Celery workers.

Why polling instead of WebSocket subscriptions?
  Monad Testnet RPC is HTTP-only for now. WebSocket support may come later.
  Polling every N seconds is reliable and simple enough for v1.

Events listened for:
  - VerificationRegistry.ProofSubmitted  → triggers verification_worker
  - WorkChain.DisputeRaised              → triggers dispute_worker
  - WorkChain.JobCompleted               → triggers notification (job done)
  - WorkChain.MilestoneReleased          → triggers notification (payment sent)

Run this as a background task from main.py on startup, or as a standalone
process:
    python -m app.services.blockchain.listener
"""

import asyncio
import logging
from typing import Optional

from app.config.settings import get_settings
from app.services.blockchain.client import get_blockchain_client

logger = logging.getLogger(__name__)
settings = get_settings()

# Poll interval — 3 seconds is safe for Monad testnet
POLL_INTERVAL_SECONDS = 3


class EventListener:
    """
    Continuously polls the chain for new events from the last seen block.
    Dispatches each event to the appropriate Celery task.
    """

    def __init__(self):
        self.client = get_blockchain_client()
        self._last_block: Optional[int] = None
        self._running = False

    async def start(self):
        """Start the polling loop. Runs until stop() is called."""
        self._running = True
        self._last_block = await self.client.get_block_number()
        logger.info(f"Event listener started from block {self._last_block}")

        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.error(f"Listener poll error: {e}", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        self._running = False
        logger.info("Event listener stopped")

    async def _poll(self):
        """Check for new events since _last_block."""
        current_block = await self.client.get_block_number()
        if current_block <= self._last_block:
            return  # No new blocks

        from_block = self._last_block + 1
        to_block   = current_block

        logger.debug(f"Scanning blocks {from_block} → {to_block}")

        await asyncio.gather(
            self._handle_proof_submitted(from_block, to_block),
            self._handle_dispute_raised(from_block, to_block),
            self._handle_job_completed(from_block, to_block),
            self._handle_milestone_released(from_block, to_block),
        )

        self._last_block = current_block

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _handle_proof_submitted(self, from_block: int, to_block: int):
        """
        VerificationRegistry.ProofSubmitted
        → Dispatch to verification_worker to verify the proof off-chain.
        """
        try:
            events = await self.client.registry.events.ProofSubmitted.get_logs(
                fromBlock=from_block,
                toBlock=to_block,
            )
        except Exception as e:
            logger.warning(f"ProofSubmitted fetch error: {e}")
            return

        for event in events:
            args = event["args"]
            job_id          = args["jobId"]
            milestone_index = args["milestoneIndex"]
            worker          = args["worker"]
            proof_type      = args["proofType"]
            proof_hash      = args["proofHash"].hex()
            proof_uri       = args["proofURI"]

            logger.info(
                f"ProofSubmitted: job={job_id} milestone={milestone_index} "
                f"worker={worker} type={proof_type}"
            )

            # Dispatch to Celery — import here to avoid circular imports
            from app.workers.verification_worker import process_verification
            process_verification.delay(
                job_id=job_id,
                milestone_index=milestone_index,
                worker_address=worker,
                proof_hash=proof_hash,
                proof_uri=proof_uri,
                proof_type=proof_type,
            )

    async def _handle_dispute_raised(self, from_block: int, to_block: int):
        """
        WorkChain.DisputeRaised
        → Dispatch to dispute_worker to open the case in DisputeResolver.
        """
        try:
            events = await self.client.workchain.events.DisputeRaised.get_logs(
                fromBlock=from_block,
                toBlock=to_block,
            )
        except Exception as e:
            logger.warning(f"DisputeRaised fetch error: {e}")
            return

        for event in events:
            args     = event["args"]
            job_id   = args["jobId"]
            raised_by = args["raisedBy"]

            logger.info(f"DisputeRaised: job={job_id} by={raised_by}")

            from app.workers.dispute_worker import handle_dispute_opened
            handle_dispute_opened.delay(job_id=job_id, raised_by=raised_by)

    async def _handle_job_completed(self, from_block: int, to_block: int):
        """
        WorkChain.JobCompleted
        → Notify both parties that the job is complete and rating is available.
        """
        try:
            events = await self.client.workchain.events.JobCompleted.get_logs(
                fromBlock=from_block,
                toBlock=to_block,
            )
        except Exception as e:
            logger.warning(f"JobCompleted fetch error: {e}")
            return

        for event in events:
            job_id = event["args"]["jobId"]
            logger.info(f"JobCompleted: job={job_id}")

            from app.services.notifications.notifier import notify_job_completed
            asyncio.create_task(notify_job_completed(job_id))

    async def _handle_milestone_released(self, from_block: int, to_block: int):
        """
        WorkChain.MilestoneReleased
        → Notify worker that payment has been sent.
        """
        try:
            events = await self.client.workchain.events.MilestoneReleased.get_logs(
                fromBlock=from_block,
                toBlock=to_block,
            )
        except Exception as e:
            logger.warning(f"MilestoneReleased fetch error: {e}")
            return

        for event in events:
            args            = event["args"]
            job_id          = args["jobId"]
            milestone_index = args["milestoneIndex"]
            amount          = args["amount"]
            worker          = args["worker"]
            auto_released   = args["autoReleased"]

            logger.info(
                f"MilestoneReleased: job={job_id} milestone={milestone_index} "
                f"amount={amount} auto={auto_released}"
            )

            from app.services.notifications.notifier import notify_milestone_released
            asyncio.create_task(
                notify_milestone_released(job_id, milestone_index, worker, amount)
            )


# ── Singleton ─────────────────────────────────────────────────────────────────
_listener: Optional[EventListener] = None

def get_listener() -> EventListener:
    global _listener
    if _listener is None:
        _listener = EventListener()
    return _listener


# ── Standalone entrypoint ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from app.services.blockchain.client import get_blockchain_client

    async def main():
        client = get_blockchain_client()
        await client.initialise()
        listener = get_listener()
        await listener.start()

    asyncio.run(main())
