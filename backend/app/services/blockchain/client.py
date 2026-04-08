"""
services/blockchain/client.py
==============================
Web3.py client for all on-chain reads and writes.
Single entry point for every contract interaction in the backend.

All write functions (confirm_proof, reject_proof, resolve_dispute)
are signed with the verifier or owner private key from settings.
Read functions require no signing.

Usage:
    from app.services.blockchain.client import get_blockchain_client
    client = get_blockchain_client()
    job = await client.get_job(job_id=0)
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from eth_account import Account
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _load_abi(filename: str) -> list:
    """Load ABI from the abis/ directory next to this file."""
    abi_path = Path(__file__).parent / "abis" / filename
    if not abi_path.exists():
        raise FileNotFoundError(
            f"ABI not found: {abi_path}\n"
            f"Export ABIs from Hardhat artifacts/ after compile and place in "
            f"backend/app/services/blockchain/abis/"
        )
    with open(abi_path) as f:
        data = json.load(f)
    # Hardhat artifact format wraps ABI in a dict; raw ABI is a list
    return data["abi"] if isinstance(data, dict) else data


class BlockchainClient:
    """
    Async Web3.py client. Wraps all four WorkChain contracts.
    Instantiated once via get_blockchain_client().
    """

    def __init__(self):
        self.w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.rpc_url))
        # Monad is EVM-compatible but uses POA — add middleware
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # Signing accounts
        self._verifier = (
            Account.from_key(settings.verifier_private_key)
            if settings.verifier_private_key else None
        )
        self._owner = (
            Account.from_key(settings.owner_private_key)
            if settings.owner_private_key else None
        )

        # Contracts — lazy loaded in _load_contracts()
        self.workchain = None
        self.registry = None
        self.milestone_manager = None
        self.dispute_resolver = None

    async def initialise(self):
        """
        Called once from main.py on startup.
        Loads ABIs and instantiates contract objects.
        """
        connected = await self.w3.is_connected()
        if not connected:
            raise ConnectionError(f"Cannot connect to RPC: {settings.rpc_url}")

        chain_id = await self.w3.eth.chain_id
        logger.info(f"Connected to chain ID {chain_id} via {settings.rpc_url}")

        self.workchain = self.w3.eth.contract(
            address=self.w3.to_checksum_address(settings.workchain_address),
            abi=_load_abi("WorkChain.json"),
        )
        self.registry = self.w3.eth.contract(
            address=self.w3.to_checksum_address(settings.registry_address),
            abi=_load_abi("VerificationRegistry.json"),
        )
        self.milestone_manager = self.w3.eth.contract(
            address=self.w3.to_checksum_address(settings.milestone_manager_address),
            abi=_load_abi("MilestoneManager.json"),
        )
        self.dispute_resolver = self.w3.eth.contract(
            address=self.w3.to_checksum_address(settings.dispute_resolver_address),
            abi=_load_abi("DisputeResolver.json"),
        )
        logger.info("All contracts loaded")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _send_tx(self, fn, signer: Account) -> str:
        """
        Build, sign, and send a contract transaction.
        Returns the transaction hash as a hex string.
        """
        nonce = await self.w3.eth.get_transaction_count(signer.address)
        gas_price = await self.w3.eth.gas_price

        tx = await fn.build_transaction({
            "from":     signer.address,
            "nonce":    nonce,
            "gasPrice": gas_price,
        })

        # Estimate gas with 20% buffer
        try:
            estimated = await self.w3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated * 1.2)
        except Exception as e:
            logger.warning(f"Gas estimation failed: {e} — using 300000")
            tx["gas"] = 300_000

        signed = signer.sign_transaction(tx)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt["status"] != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

        logger.info(f"Tx confirmed: {tx_hash.hex()} (block {receipt['blockNumber']})")
        return tx_hash.hex()

    # ── WorkChain reads ───────────────────────────────────────────────────────

    async def get_job(self, job_id: int) -> dict:
        """Fetch full job state from WorkChain."""
        result = await self.workchain.functions.getJob(job_id).call()
        keys = [
            "employer", "worker", "title", "total_escrowed", "total_released",
            "status", "worker_rating", "rating_submitted", "created_at", "milestone_count"
        ]
        return dict(zip(keys, result))

    async def get_milestones(self, job_id: int) -> list[dict]:
        """Fetch all milestones for a job."""
        raw = await self.workchain.functions.getMilestones(job_id).call()
        return [
            {
                "index":       i,
                "description": m[0],
                "amount":      m[1],
                "released":    m[2],
                "verified":    m[3],
            }
            for i, m in enumerate(raw)
        ]

    async def get_escrow_progress(self, job_id: int) -> int:
        """Returns 0–100 percent of escrow released."""
        return await self.workchain.functions.escrowProgress(job_id).call()

    async def get_worker_jobs(self, address: str) -> list[int]:
        return await self.workchain.functions.getWorkerJobs(
            self.w3.to_checksum_address(address)
        ).call()

    async def get_employer_jobs(self, address: str) -> list[int]:
        return await self.workchain.functions.getEmployerJobs(
            self.w3.to_checksum_address(address)
        ).call()

    # ── VerificationRegistry reads ────────────────────────────────────────────

    async def get_proof_status(self, job_id: int, milestone_index: int) -> int:
        """Returns VerificationStatus enum value (0=None,1=Pending,2=Verified,3=Rejected)."""
        return await self.registry.functions.getProofStatus(
            job_id, milestone_index
        ).call()

    async def get_proof(self, job_id: int, milestone_index: int) -> dict:
        raw = await self.registry.functions.getProof(job_id, milestone_index).call()
        return {
            "submitted_by":     raw[0],
            "proof_hash":       raw[1].hex() if isinstance(raw[1], bytes) else raw[1],
            "proof_uri":        raw[2],
            "proof_type":       raw[3],
            "submitted_at":     raw[4],
            "confirmed_at":     raw[5],
            "status":           raw[6],
            "rejection_reason": raw[7],
        }

    # ── VerificationRegistry writes ───────────────────────────────────────────

    async def confirm_proof(
        self,
        job_id: int,
        milestone_index: int,
        expected_hash: bytes,
    ) -> str:
        """
        Verifier confirms a pending proof.
        Triggers automatic milestone release in WorkChain.
        expected_hash must be bytes32.
        """
        if not self._verifier:
            raise RuntimeError("VERIFIER_PRIVATE_KEY not configured")

        fn = self.registry.functions.confirmProof(
            job_id,
            milestone_index,
            expected_hash,
        )
        tx_hash = await self._send_tx(fn, self._verifier)
        logger.info(f"Proof confirmed — job {job_id} milestone {milestone_index}")
        return tx_hash

    async def reject_proof(
        self,
        job_id: int,
        milestone_index: int,
        reason: str,
    ) -> str:
        """Verifier rejects a pending proof. Worker must resubmit."""
        if not self._verifier:
            raise RuntimeError("VERIFIER_PRIVATE_KEY not configured")

        fn = self.registry.functions.rejectProof(
            job_id,
            milestone_index,
            reason,
        )
        tx_hash = await self._send_tx(fn, self._verifier)
        logger.info(f"Proof rejected — job {job_id} milestone {milestone_index}: {reason}")
        return tx_hash

    # ── DisputeResolver reads ─────────────────────────────────────────────────

    async def get_dispute_by_job(self, job_id: int) -> Optional[dict]:
        dispute_id, exists = await self.dispute_resolver.functions.getDisputeByJob(
            job_id
        ).call()
        if not exists:
            return None
        raw = await self.dispute_resolver.functions.getDispute(dispute_id).call()
        keys = [
            "job_id", "employer", "worker", "remaining_escrow", "opened_at",
            "resolved_at", "status", "resolved_by", "resolution_note",
            "employer_award", "worker_award", "evidence_count"
        ]
        result = dict(zip(keys, raw))
        result["dispute_id"] = dispute_id
        return result

    async def get_all_evidence(self, dispute_id: int) -> list[dict]:
        raw = await self.dispute_resolver.functions.getAllEvidence(dispute_id).call()
        return [
            {
                "submitted_by":  e[0],
                "description":   e[1],
                "evidence_hash": e[2].hex() if isinstance(e[2], bytes) else e[2],
                "evidence_uri":  e[3],
                "submitted_at":  e[4],
            }
            for e in raw
        ]

    # ── Chain utilities ───────────────────────────────────────────────────────

    async def get_block_number(self) -> int:
        return await self.w3.eth.block_number

    async def is_connected(self) -> bool:
        return await self.w3.is_connected()

    def to_checksum(self, address: str) -> str:
        return self.w3.to_checksum_address(address)

    @staticmethod
    def hash_to_bytes32(hex_hash: str) -> bytes:
        """Convert '0x...' hex string to bytes32 for contract calls."""
        clean = hex_hash.removeprefix("0x")
        return bytes.fromhex(clean.zfill(64))


@lru_cache()
def get_blockchain_client() -> BlockchainClient:
    """Returns the singleton blockchain client. Initialise() called on startup."""
    return BlockchainClient()
