from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator

from app.workers.verification_worker import queue_verification
from app.db.models import get_verification_by_milestone

router = APIRouter()

class VerifyRequest(BaseModel):
    milestone_id: int
    proof_type: str
    data: str

    @validator("proof_type")
    def validate_type(cls, v):
        allowed = ["github", "file", "link", "manual"]
        if v not in allowed:
            raise ValueError("Invalid proof type")
        return v


@router.post("/")
def verify(req: VerifyRequest):
    try:
        queue_verification(req.dict())
        return {
            "status": "queued",
            "message": "Verification pipeline initiated",
            "milestone_id": req.milestone_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{milestone_id}")
def get_status(milestone_id: int):
    record = get_verification_by_milestone(milestone_id)

    if not record:
        return {
            "status": "pending",
            "message": "Awaiting verification"
        }

    return {
        "status": record.status,
        "proof_type": record.proof_type,
        "confidence_score": record.confidence_score,
        "verification_layers": record.verification_layers,
        "message": "Verification completed"
    }