import os
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging
import threading
from ..scraper.nn_scraper import run_check

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
    free = (
        db.query(func.count(User.id))
        .filter(User.subscription_status == "free")
        .scalar()
    )
    active = (
        db.query(func.count(User.id))
        .filter(User.subscription_status == "active")
        .scalar()
    )
    expired = (
        db.query(func.count(User.id))
        .filter(User.subscription_status == "expired")
        .scalar()
    )
    return AdminStats(
        total_users=total, free_users=free, active_users=active, expired_users=expired
    )


class SetPlanRequest(BaseModel):
    email: str
    plan: str  # "free" | "basic" | "plus"
    months: int = 1


PLAN_CONFIG = {
    "free": {"subscription_status": "free", "plan_type": "free", "keyword_limit": 3},
    "basic": {
        "subscription_status": "active",
        "plan_type": "basic",
        "keyword_limit": 10,
    },
    "plus": {"subscription_status": "active", "plan_type": "plus", "keyword_limit": 20},
}


@router.post("/set-plan")
def set_plan(
    data: SetPlanRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if data.plan not in PLAN_CONFIG:
        raise HTTPException(
            status_code=400, detail="Neispravan plan. Dozvoljeni: free, basic, plus"
        )
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Korisnik nije pronađen")
    cfg = PLAN_CONFIG[data.plan]
    user.subscription_status = cfg["subscription_status"]
    user.plan_type = cfg["plan_type"]
    user.keyword_limit = cfg["keyword_limit"]
    if data.plan != "free":
        user.subscription_end = date.today() + timedelta(days=30 * data.months)
    else:
        user.subscription_end = None
    db.add(
        Log(
            event_type="plan_set",
            user_id=user.id,
            detail=f"admin set plan={data.plan} months={data.months}",
        )
    )
    db.commit()
    return {"message": f"Plan korisnika {user.email} postavljen na {data.plan}"}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "subscription_status": u.subscription_status,
            "plan_type": u.plan_type,
            "keyword_limit": u.keyword_limit,
            "subscription_end": u.subscription_end,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.get("/logs")
def get_logs(
    limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
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


@router.post("/trigger-scraper")
def trigger_scraper(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ručno pokretanje tražilice — dostupno svim korisnicima."""

    def run_in_background():
        try:
            run_check()
        except Exception as e:
            logging.error(f"Scraper greška: {e}")

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()

    return {"status": "ok", "message": "Tražilica pokrenuta u pozadini."}

@router.post("/trigger-scraper")
def trigger_scraper(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ručno pokretanje tražilice — dostupno svim korisnicima."""
    def run_in_background():
        try:
            run_check()
        except Exception as e:
            logging.error(f"Scraper greška: {e}")

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()

    return {"status": "ok", "message": "Tražilica pokrenuta u pozadini."}
