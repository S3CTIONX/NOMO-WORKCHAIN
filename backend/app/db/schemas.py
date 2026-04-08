"""
db/schemas.py
=============
Pydantic schemas for API request/response validation.
These are NOT the database models (that's models.py).
These are what FastAPI serialises to/from JSON.

Naming convention:
    FooCreate  — incoming request body (no id, no timestamps)
    FooRead    — outgoing response (includes id, timestamps)
    FooUpdate  — partial update body (all fields optional)
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator
from enum import IntEnum


# ── Shared enums (mirror the DB enums) ───────────────────────────────────────

class ProofType(IntEnum):
    GITHUB   = 0
    FILE_HASH = 1
    LINK     = 2
    MANUAL   = 3

class VerificationStatus(IntEnum):
    NONE     = 0
    PENDING  = 1
    VERIFIED = 2
    REJECTED = 3

class DisputeStatus(IntEnum):
    OPEN     = 0
    RESOLVED = 1
    DISMISSED = 2


# ── Verification Record ───────────────────────────────────────────────────────

class VerificationRecordCreate(BaseModel):
    job_id: int
    milestone_index: int
    worker_address: str
    proof_hash: str       # keccak256 hex string: "0x..."
    proof_uri: str
    proof_type: ProofType
    tx_hash: Optional[str] = None   # On-chain tx hash of submitProof()

    @field_validator("worker_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Invalid Ethereum address")
        return v.lower()

    @field_validator("proof_hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        if not v.startswith("0x") or len(v) != 66:
            raise ValueError("Invalid keccak256 hash — must be 0x + 64 hex chars")
        return v.lower()


class VerificationRecordRead(BaseModel):
    id: int
    job_id: int
    milestone_index: int
    worker_address: str
    proof_hash: str
    proof_uri: str
    proof_type: ProofType
    status: VerificationStatus
    rejection_reason: Optional[str]
    tx_hash: Optional[str]
    confirm_tx_hash: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Dispute Record ────────────────────────────────────────────────────────────

class DisputeRecordCreate(BaseModel):
    job_id: int
    dispute_id_onchain: int       # Dispute ID from DisputeResolver.sol
    employer_address: str
    worker_address: str
    remaining_escrow_wei: int     # Amount locked in DisputeResolver

    @field_validator("employer_address", "worker_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Invalid Ethereum address")
        return v.lower()


class DisputeRecordRead(BaseModel):
    id: int
    job_id: int
    dispute_id_onchain: int
    employer_address: str
    worker_address: str
    remaining_escrow_wei: int
    status: DisputeStatus
    resolution_note: Optional[str]
    employer_award_wei: Optional[int]
    worker_award_wei: Optional[int]
    resolve_tx_hash: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Evidence ──────────────────────────────────────────────────────────────────

class EvidenceCreate(BaseModel):
    dispute_id: int               # Internal DB dispute ID
    submitted_by: str             # Wallet address
    description: str
    evidence_hash: str            # keccak256 of document
    evidence_uri: str

    @field_validator("submitted_by")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Invalid Ethereum address")
        return v.lower()


class EvidenceRead(BaseModel):
    id: int
    dispute_id: int
    submitted_by: str
    description: str
    evidence_hash: str
    evidence_uri: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── API response wrappers ─────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str

class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None


# ── Verification API request bodies ──────────────────────────────────────────

class ConfirmProofRequest(BaseModel):
    """Body for POST /verify/confirm — called internally by the verification worker."""
    job_id: int
    milestone_index: int
    expected_hash: str

class RejectProofRequest(BaseModel):
    """Body for POST /verify/reject."""
    job_id: int
    milestone_index: int
    reason: str


# ── Contract query responses ──────────────────────────────────────────────────

class JobResponse(BaseModel):
    """Shaped response from WorkChain.getJob()"""
    job_id: int
    employer: str
    worker: str
    title: str
    total_escrowed_wei: int
    total_released_wei: int
    status: int               # 0=Active, 1=Complete, 2=Disputed
    worker_rating: int
    rating_submitted: bool
    created_at: int           # Unix timestamp
    milestone_count: int

class MilestoneResponse(BaseModel):
    """Single milestone from WorkChain.getMilestones()"""
    index: int
    description: str
    amount_wei: int
    released: bool
    verified: bool
