from sqlalchemy import Column, Integer, String, Float
from app.db.session import Base, SessionLocal

class VerificationRecord(Base):
    __tablename__ = "verifications"

    id = Column(Integer, primary_key=True, index=True)
    milestone_id = Column(Integer, index=True)
    proof = Column(String)
    proof_type = Column(String)
    status = Column(String)
    confidence_score = Column(Float)
    verification_layers = Column(Integer)


def save_verification(
    milestone_id,
    proof,
    proof_type,
    status,
    confidence_score,
    verification_layers
):
    db = SessionLocal()

    record = VerificationRecord(
        milestone_id=milestone_id,
        proof=proof,
        proof_type=proof_type,
        status=status,
        confidence_score=confidence_score,
        verification_layers=verification_layers
    )

    db.add(record)
    db.commit()
    db.refresh(record)
    db.close()

    return record


def get_verification_by_milestone(milestone_id):
    db = SessionLocal()

    record = db.query(VerificationRecord)\
        .filter_by(milestone_id=milestone_id)\
        .order_by(VerificationRecord.id.desc())\
        .first()

    db.close()
    return record