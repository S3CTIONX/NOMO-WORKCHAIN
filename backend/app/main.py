from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.verify import router as verify_router
from app.api.contracts import router as contracts_router
from app.api.milestones import router as milestones_router

app = FastAPI(title="NOMO Workchain Backend")

# ── CORS — allow frontend to call the API ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",                  # Local dev
        "https://*.vercel.app",                   # Vercel preview deploys
        "*",                                      # Hackathon: allow all (lock down later)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────────
app.include_router(verify_router, prefix="/verify")
app.include_router(contracts_router)          # Already has prefix="/contracts"
app.include_router(milestones_router)         # Already has prefix="/milestones"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nomo-workchain-backend"}