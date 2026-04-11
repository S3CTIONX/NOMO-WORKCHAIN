from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.verify import router as verify_router

# NOTE: contracts and milestones routers have missing schema imports
# (TxResponse, ProposeMilestoneRequest, etc.) — uncomment once those
# schemas are added to db/schemas.py
# from app.api.contracts import router as contracts_router
# from app.api.milestones import router as milestones_router

app = FastAPI(title="NOMO Workchain Backend")

# ── CORS — allow frontend to call the API ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Hackathon: allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────────
app.include_router(verify_router, prefix="/verify")
# app.include_router(contracts_router)      # Enable after schemas are complete
# app.include_router(milestones_router)     # Enable after schemas are complete


@app.get("/")
async def root():
    return {"service": "NOMO-WORKCHAIN Backend", "status": "running", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nomo-workchain-backend"}