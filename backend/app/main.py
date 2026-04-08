from fastapi import FastAPI
from app.api.verify import router as verify_router
from app.db.session import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="NOMO Workchain Backend")

app.include_router(verify_router, prefix="/verify")