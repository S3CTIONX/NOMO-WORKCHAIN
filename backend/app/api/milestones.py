"""
api/milestones.py
REST endpoints for milestone proposals (MilestoneManager contract).

These endpoints expose the post-creation milestone proposal flow:
  POST /milestones/propose         — employer proposes new milestone
  POST /milestones/{id}/approve    — worker approves proposal
  POST /milestones/{id}/reject     — worker rejects proposal
  POST /milestones/{id}/cancel     — employer cancels proposal
  GET  /milestones/job/{job_id}    — all proposals for a job
  GET  /milestones/{id}            — single proposal detail

Note: In the primary frontend flow, these transactions are signed
directly by MetaMask via ethers.js. These endpoints exist for
server-side flows and testing.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from web3 import Web3

from app.services.blockchain.client import get_blockchain_client, BlockchainClient
from app.db.schemas import ProposeMilestoneRequest, MilestoneProposalResponse, TxResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/milestones", tags=["milestones"])


def get_client() -> BlockchainClient:
    return get_blockchain_client()


STATUS_MAP = {0: "pending", 1: "approved", 2: "rejected", 3: "expired", 4: "cancelled"}


def _parse_proposal(proposal_id: int, raw: tuple) -> dict:
    """Parse raw tuple from MilestoneManager.getProposal() into a clean dict."""
    return {
        "proposal_id":  proposal_id,
        "job_id":       raw[0],
        "employer":     raw[1],
        "worker":       raw[2],
        "description":  raw[3],
        "amount_wei":   str(raw[4]),
        "amount_mon":   float(Web3.from_wei(raw[4], "ether")),
        "proposed_at":  raw[5],
        "expires_at":   raw[6],
        "status":       STATUS_MAP.get(raw[7], "unknown"),
    }


# ── Read ───────────────────────────────────────────────────────────

@router.get("/job/{job_id}")
async def get_job_proposals(job_id: int, client: BlockchainClient = Depends(get_client)):
    """
    All milestone proposals for a job — pending, approved, rejected, etc.
    Frontend shows these on the job detail page.
    """
    try:
        proposal_ids = client.get_job_proposals(job_id)
        proposals = []
        for pid in proposal_ids:
            raw = client.milestone_manager.functions.getProposal(pid).call()
            proposals.append(_parse_proposal(pid, raw))
        return {"job_id": job_id, "proposals": proposals}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/job/{job_id}/pending")
async def get_pending_proposals(job_id: int, client: BlockchainClient = Depends(get_client)):
    """
    Only pending proposals for a job.
    Worker uses this to see what they need to approve or reject.
    """
    try:
        pending_ids = client.get_pending_proposals(job_id)
        proposals = []
        for pid in pending_ids:
            raw = client.milestone_manager.functions.getProposal(pid).call()
            proposals.append(_parse_proposal(pid, raw))
        return {"job_id": job_id, "pending": proposals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{proposal_id}")
async def get_proposal(proposal_id: int, client: BlockchainClient = Depends(get_client)):
    """Single proposal detail."""
    try:
        raw = client.milestone_manager.functions.getProposal(proposal_id).call()
        return _parse_proposal(proposal_id, raw)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Write (server-side transaction submission) ─────────────────────

@router.post("/expire/{proposal_id}", response_model=TxResponse)
async def expire_proposal(proposal_id: int, client: BlockchainClient = Depends(get_client)):
    """
    Expire a stalled proposal and refund the employer.
    Called by the timeout_worker automatically, but also available via API.
    Anyone can call this — no auth needed (matches contract's public expireProposal).
    """
    try:
        raw = client.milestone_manager.functions.getProposal(proposal_id).call()
        status = raw[7]
        expires_at = raw[6]
        import time
        if status != 0:
            raise HTTPException(status_code=400, detail="Proposal is not pending")
        if int(time.time()) <= expires_at:
            raise HTTPException(status_code=400, detail="Proposal has not expired yet")

        fn = client.milestone_manager.functions.expireProposal(proposal_id)
        tx_hash = client._send_tx(fn)

        return TxResponse(
            tx_hash=tx_hash,
            status="submitted",
            message=f"Proposal {proposal_id} expired — employer will be refunded",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/total")
async def get_proposal_stats(client: BlockchainClient = Depends(get_client)):
    """Total proposal counts across all statuses — for analytics."""
    try:
        total = client.milestone_manager.functions.proposalCount().call()
        counts = {s: 0 for s in STATUS_MAP.values()}

        for pid in range(total):
            raw = client.milestone_manager.functions.getProposal(pid).call()
            status_str = STATUS_MAP.get(raw[7], "unknown")
            counts[status_str] = counts.get(status_str, 0) + 1

        return {"total": total, "by_status": counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
