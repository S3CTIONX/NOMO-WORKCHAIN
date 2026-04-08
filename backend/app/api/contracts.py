"""
api/contracts.py
REST endpoints for job and contract state.

Routes:
  GET  /contracts/jobs/{job_id}                           — fetch job details
  GET  /contracts/jobs/{job_id}/milestones                — fetch all milestones
  GET  /contracts/jobs/{job_id}/progress                  — escrow % released
  GET  /contracts/address/{address}/jobs                  — all jobs for a wallet
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from web3 import Web3

from app.services.blockchain.client import get_blockchain_client, BlockchainClient
from app.db.schemas import JobResponse, TxResponse, ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contracts", tags=["contracts"])


def get_client() -> BlockchainClient:
    return get_blockchain_client()


# ── Job reads ──────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, client: BlockchainClient = Depends(get_client)):
    """
    Fetch full job state from WorkChain.
    Used by the frontend job detail page (/jobs/[id]).
    """
    try:
        job = client.get_job(job_id)
        progress = client.get_escrow_progress(job_id)
        return JobResponse(
            job_id=job_id,
            employer=job["employer"],
            worker=job["worker"],
            title=job["title"],
            total_escrowed=str(job["totalEscrowed"]),
            total_released=str(job["totalReleased"]),
            status=job["status"],
            milestone_count=job["milestoneCount"],
            escrow_progress=progress,
        )
    except Exception as e:
        logger.error(f"get_job failed | job={job_id} | {e}")
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found or chain error: {e}")


@router.get("/jobs/{job_id}/milestones")
async def get_milestones(job_id: int, client: BlockchainClient = Depends(get_client)):
    """
    Fetch all milestones for a job with their release and verification status.
    """
    try:
        milestones = client.get_milestones(job_id)
        return {
            "job_id": job_id,
            "milestones": [
                {
                    "index":       i,
                    "description": m["description"],
                    "amount_wei":  str(m["amount"]),
                    "amount_mon":  float(Web3.from_wei(m["amount"], "ether")),
                    "released":    m["released"],
                    "verified":    m["verified"],
                }
                for i, m in enumerate(milestones)
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/jobs/{job_id}/progress")
async def get_escrow_progress(job_id: int, client: BlockchainClient = Depends(get_client)):
    """Returns integer 0–100 representing % of escrow released."""
    try:
        progress = client.get_escrow_progress(job_id)
        return {"job_id": job_id, "progress": progress}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Wallet-based lookups ───────────────────────────────────────────

@router.get("/address/{address}/jobs")
async def get_jobs_for_address(
    address: str,
    client: BlockchainClient = Depends(get_client)
):
    """
    Returns all job IDs for a wallet — both as employer and as worker.
    Frontend uses this to populate the dashboard.
    """
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    try:
        checksummed = Web3.to_checksum_address(address)
        employer_job_ids = client.get_employer_jobs(checksummed)
        worker_job_ids   = client.get_worker_jobs(checksummed)

        return {
            "address":         checksummed,
            "employer_job_ids": employer_job_ids,
            "worker_job_ids":   worker_job_ids,
            "total":           len(employer_job_ids) + len(worker_job_ids),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/address/{address}/profile")
async def get_worker_profile(
    address: str,
    client: BlockchainClient = Depends(get_client)
):
    """
    Builds an on-chain worker profile — completed jobs, average rating.
    Used by /profile/[address] in the frontend.
    """
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

    try:
        checksummed = Web3.to_checksum_address(address)
        job_ids = client.get_worker_jobs(checksummed)

        completed = []
        ratings = []
        total_earned_wei = 0

        for job_id in job_ids:
            job = client.get_job(job_id)
            if job["status"] == 1:  # Complete
                completed.append(job_id)
                total_earned_wei += job["totalReleased"]
                if job["ratingSubmitted"]:
                    ratings.append(job["workerRating"])

        avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None

        return {
            "address":            checksummed,
            "total_jobs":         len(job_ids),
            "completed_jobs":     len(completed),
            "total_earned_mon":   float(Web3.from_wei(total_earned_wei, "ether")),
            "average_rating":     avg_rating,
            "rating_count":       len(ratings),
            "completed_job_ids":  completed,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
