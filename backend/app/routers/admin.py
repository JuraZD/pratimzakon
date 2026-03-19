import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import User, Log
from ..schemas import AdminStats
from ..auth import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")


def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Pristup odbijen")
    return current_user


@router.get("/stats", response_model=AdminStats)
def get_stats(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    total = db.query(func.count(User.id)).scalar()
    free = db.query(func.count(User.id)).filter(User.subscription_status == "free").scalar()
    active = db.query(func.count(User.id)).filter(User.subscription_status == "active").scalar()
    expired = db.query(func.count(User.id)).filter(User.subscription_status == "expired").scalar()
    return AdminStats(total_users=total, free_users=free, active_users=active, expired_users=expired)


@router.get("/logs")
def get_logs(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    logs = db.query(Log).order_by(Log.timestamp.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "event_type": l.event_type,
            "user_id": l.user_id,
            "detail": l.detail,
            "timestamp": l.timestamp,
        }
        for l in logs
    ]
