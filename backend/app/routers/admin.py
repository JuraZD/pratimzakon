import os
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging
import threading
from ..database import get_db
from ..models import User, Log, PLAN_LIMITS
from ..schemas import AdminStats
from ..auth import get_current_user
from ..utils.stemmer import stem_keyword as _stem

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
        total_users=total,
        free_users=free,
        active_users=active,
        expired_users=expired,
    )


class SetPlanRequest(BaseModel):
    email: str
    plan: str  # "free" | "basic" | "plus"
    months: int = 1


PLAN_CONFIG = {
    "free":  {"subscription_status": "free",   "keyword_limit": PLAN_LIMITS["free"]},
    "basic": {"subscription_status": "active", "keyword_limit": PLAN_LIMITS["basic"]},
    "plus":  {"subscription_status": "active", "keyword_limit": PLAN_LIMITS["plus"]},
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
            "keyword_limit": u.keyword_limit,
            "subscription_end": u.subscription_end,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.get("/logs")
def get_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
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


@router.post("/trigger-user-scan")
def trigger_user_scan(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Skenira SVE postojeće dokumente u bazi za ključne riječi trenutnog korisnika.
    Sprema keyword_match logove bez slanja emaila.
    Namjerno ne importa notifier.py kako bi se izbjegla ovisnost o Anthropic SDK-u.
    """
    from ..database import SessionLocal
    from ..models import Document, Keyword, Log as ScanLog

    user_id = current_user.id

    def run_in_background():
        try:
            bg_db = SessionLocal()
            try:
                keywords = bg_db.query(Keyword).filter(Keyword.user_id == user_id).all()
                if not keywords:
                    logging.info(f"Korisnik {user_id}: nema ključnih riječi za skeniranje")
                    return

                existing = {
                    (p.get("keyword", "").lower(), p.get("doc_id", ""))
                    for row in bg_db.query(ScanLog.detail)
                    .filter(ScanLog.user_id == user_id, ScanLog.event_type == "keyword_match")
                    .all()
                    for p in [dict(x.split(":", 1) for x in (row[0] or "").split("|") if ":" in x)]
                }

                new_count = 0
                for kw in keywords:
                    term = _stem(kw.keyword)
                    query = bg_db.query(Document).filter(Document.title.ilike(f"%{term}%"))
                    if kw.part_filter:
                        query = query.filter(Document.part == kw.part_filter.upper())
                    if kw.institution_filter:
                        query = query.filter(Document.institution.ilike(f"%{kw.institution_filter}%"))
                    docs = query.all()
                    logging.info(f"Korisnik {user_id} keyword='{kw.keyword}' (stem='{term}'): {len(docs)} dokumenata")

                    for doc in docs:
                        pair = (kw.keyword.lower(), str(doc.id))
                        if pair in existing:
                            continue
                        detail = f"keyword:{kw.keyword}|doc_id:{doc.id}|title:{doc.title[:100]}"
                        bg_db.add(ScanLog(event_type="keyword_match", user_id=user_id, detail=detail))
                        existing.add(pair)
                        new_count += 1

                if new_count > 0:
                    bg_db.commit()
                logging.info(f"Korisnik {user_id}: {new_count} novih podudaranja spremljeno")
            finally:
                bg_db.close()
        except Exception as e:
            logging.error(f"Greška pri skeniranju za korisnika {user_id}: {e}", exc_info=True)

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()
    return {"status": "ok", "message": "Skeniranje u tijeku."}
