# NOMO-WORKCHAIN

> On-chain labor escrow for the Nigerian freelance market — built on Monad.

WorkChain eliminates payment disputes between employers and freelancers. Funds lock in a smart contract at job creation, release per verified milestone, and build a permanent on-chain reputation for every worker. No middleman. No ghosting. No trust required.

Built for **MONAD BLITZ LAGOS** — leveraging Monad's parallel EVM for near-instant milestone releases.

---

## The Problem

Freelancers in Nigeria get paid late, underpaid, or not at all. Employers have no way to verify work before paying. There is no neutral enforcement layer — just trust, and trust fails.

## The Solution

WorkChain puts the agreement on-chain. Funds are locked before work starts. Released only when milestones are verified. Disputes go to an on-chain arbitration system. Every job, payment, and rating is permanent and public.

---

## Architecture

```
NOMO-WORKCHAIN/
│
├── contracts/                    # Solidity — deployed on Monad Testnet
│   ├── WorkChain.sol             # Core escrow: create jobs, release milestones
│   ├── VerificationRegistry.sol  # Two-step proof verification → auto-release
│   ├── MilestoneManager.sol      # Post-creation milestone proposals (mutual consent)
│   └── DisputeResolver.sol       # Arbitration: evidence + ruled fund distribution
│
├── backend/                      # Python (FastAPI)
│   └── app/
│       ├── api/                  # REST endpoints
│       ├── services/
│       │   ├── verification/     # GitHub, file hash, link, manual verification
│       │   ├── blockchain/       # Web3.py — contract reads, writes, event listening
│       │   ├── dispute/          # Dispute lifecycle management
│       │   └── notifications/    # Worker/employer alerts
│       ├── workers/              # Celery async jobs
│       └── db/                   # SQLAlchemy models + Pydantic schemas
│
└── frontend/                     # Next.js 14
    ├── app/                      # Routes: dashboard, create job, job detail, profile
    ├── components/               # JobCard, MilestoneList, EscrowBar, TxButton...
    └── lib/                      # contract.js (ABI + address), wagmi.js, format.js
```

---

## Smart Contracts

### WorkChain.sol
The core escrow contract. All funds flow through here.

| Function | Caller | Description |
|---|---|---|
| `createJob()` | Employer | Lock total payment in escrow, define milestones |
| `releaseMilestone()` | Employer | Manually approve and pay a milestone |
| `releaseMilestoneFromRegistry()` | VerificationRegistry only | Auto-release after verified proof |
| `addMilestoneFromManager()` | MilestoneManager only | Add approved new milestone post-creation |
| `raiseDispute()` | Either party | Freeze job, transfer escrow to DisputeResolver |
| `resolveFromDisputer()` | DisputeResolver only | Restore job or finalise resolution |
| `submitRating()` | Employer | Rate worker 1–5 after job completion |

### VerificationRegistry.sol
Two-step proof system. Worker submits → backend confirms → funds release automatically.

| Function | Caller | Description |
|---|---|---|
| `submitProof()` | Worker | Submit proof hash + URI (GitHub / file / link / manual) |
| `confirmProof()` | Backend verifier wallet | Confirm hash matches — triggers auto-release |
| `rejectProof()` | Backend verifier wallet | Reject — worker must resubmit |
| `setVerifier()` | Owner | Rotate backend wallet if compromised |

### MilestoneManager.sol
Handles post-creation milestone changes with mutual consent.

| Function | Caller | Description |
|---|---|---|
| `proposeMilestone()` | Employer | Propose new milestone + lock funds. 72h window. |
| `approveMilestone()` | Worker | Accept — milestone added to WorkChain |
| `rejectMilestone()` | Worker | Decline — employer refunded |
| `cancelProposal()` | Employer | Cancel before worker responds |
| `expireProposal()` | Anyone | Expire stale proposal — employer refunded |

### DisputeResolver.sol
Owner-arbitrated dispute resolution with on-chain evidence and fund distribution.

| Function | Caller | Description |
|---|---|---|
| `openDispute()` | WorkChain only | Receive frozen escrow, open case |
| `submitEvidence()` | Either party | Submit evidence hash + URI (max 10 per dispute) |
| `resolve()` | Arbitrator | Rule on split — transfers execute immediately |
| `dismiss()` | Arbitrator | Dismiss invalid dispute — job restored |

---

## Deployment Order

These contracts are interdependent. Follow this exact sequence:

```bash
# 1. Deploy WorkChain
npx hardhat run scripts/deploy.js --network monad
# → Save WORKCHAIN_ADDRESS

# 2. Deploy VerificationRegistry
#    Constructor args: (backendVerifierWallet, WORKCHAIN_ADDRESS)
npx hardhat run scripts/deploy_registry.js --network monad
# → Save REGISTRY_ADDRESS

# 3. Deploy MilestoneManager
#    Constructor args: (WORKCHAIN_ADDRESS)
npx hardhat run scripts/deploy_milestone_manager.js --network monad
# → Save MILESTONE_MANAGER_ADDRESS

# 4. Deploy DisputeResolver
#    Constructor args: (arbitratorWallet, WORKCHAIN_ADDRESS)
npx hardhat run scripts/deploy_dispute_resolver.js --network monad
# → Save DISPUTE_RESOLVER_ADDRESS

# 5. Connect everything to WorkChain
npx hardhat run scripts/connect.js --network monad
# Calls:
#   WorkChain.setRegistry(REGISTRY_ADDRESS)
#   WorkChain.setMilestoneManager(MILESTONE_MANAGER_ADDRESS)
#   WorkChain.setDisputeResolver(DISPUTE_RESOLVER_ADDRESS)
```

---

## Monad Testnet

| Field | Value |
|---|---|
| Network name | Monad Testnet |
| Chain ID | 10143 |
| RPC URL | https://testnet-rpc.monad.xyz |
| Currency | MON |
| Explorer | https://testnet.monadexplorer.com |
| Faucet | https://faucet.monad.xyz |

---

## Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in: RPC_URL, VERIFIER_PRIVATE_KEY, CONTRACT_ADDRESSES, DATABASE_URL

# Run database migrations
alembic upgrade head

# Start Redis (required for Celery workers)
redis-server

# Start verification worker (separate terminal)
celery -A app.workers.verification_worker worker --loglevel=info

# Start API server
uvicorn app.main:app --reload --port 8000
```

API docs available at `http://localhost:8000/docs` after startup.

---

## Frontend Setup

```bash
cd frontend

npm install

# Configure environment
cp .env.example .env.local
# Fill in: NEXT_PUBLIC_CONTRACT_ADDRESS, NEXT_PUBLIC_WALLETCONNECT_ID

npm run dev
# → http://localhost:3000
```

---

## How Verification Works

```
Worker completes milestone
        ↓
Worker calls submitProof(jobId, milestoneIndex, proofHash, proofURI, proofType)
        ↓
ProofSubmitted event emitted on-chain
        ↓
Backend listener picks up event (blockchain/listener.py)
        ↓
Verification worker processes proof:
  GitHub  → check commit exists, PR merged, repo is public
  File    → fetch from IPFS, recompute SHA256, compare hash
  Link    → HTTP GET, check status 200, extract metadata
  Manual  → flag for human review, notify arbitrator
        ↓
Hash matches + verification passes
        ↓
Backend calls confirmProof(jobId, milestoneIndex, expectedHash)
        ↓
Registry calls WorkChain.releaseMilestoneFromRegistry()
        ↓
Funds transfer to worker — settled in < 1 second on Monad
```

---

## Environment Variables

### Backend (.env)
```
# Monad
RPC_URL=https://testnet-rpc.monad.xyz
CHAIN_ID=10143

# Backend verifier wallet — signs confirmProof() transactions
VERIFIER_PRIVATE_KEY=your_verifier_private_key

# Contract addresses (fill after deployment)
WORKCHAIN_ADDRESS=
REGISTRY_ADDRESS=
MILESTONE_MANAGER_ADDRESS=
DISPUTE_RESOLVER_ADDRESS=

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/workchain

# Redis (Celery broker)
REDIS_URL=redis://localhost:6379/0

# GitHub verification
GITHUB_TOKEN=your_github_personal_access_token

# Optional
SENTRY_DSN=
```

### Frontend (.env.local)
```
NEXT_PUBLIC_CONTRACT_ADDRESS=
NEXT_PUBLIC_WALLETCONNECT_ID=
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Blockchain | Monad Testnet (EVM-compatible) |
| Contracts | Solidity 0.8.20 |
| Contract tooling | Hardhat |
| Backend | Python 3.11 / FastAPI |
| Task queue | Celery + Redis |
| Database | PostgreSQL (SQLAlchemy) |
| Blockchain client | Web3.py |
| Frontend | Next.js 14 (App Router) |
| Styling | Tailwind CSS |
| Wallet | wagmi + RainbowKit |
| Chain interaction | ethers.js v6 |
| Deployment | Vercel (frontend) / Docker (backend) |

---

## Project Status

Built for MONAD BLITZ LAGOS hackathon. Testnet only. Not audited. Do not use with real funds.

---

*NOMOLABS — Intelligence first.*
