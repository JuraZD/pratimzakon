"""
Web Push notifikacije (VAPID).
Generiraj ključeve: python -m py_vapid --gen
Postavi VAPID_PUBLIC_KEY i VAPID_PRIVATE_KEY u .env.
"""

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import PushSubscription, User

router = APIRouter(prefix="/push", tags=["push"])

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS = {"sub": os.getenv("VAPID_SUBJECT", "mailto:admin@pratimzakon.hr")}


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid-public-key")
def get_vapid_key():
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifikacije nisu konfigurirane")
    return {"public_key": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
def subscribe(
    data: PushSubscribeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == data.endpoint
    ).first()
    if existing:
        existing.user_id = current_user.id
        existing.p256dh = data.keys.p256dh
        existing.auth = data.keys.auth
    else:
        db.add(PushSubscription(
            user_id=current_user.id,
            endpoint=data.endpoint,
            p256dh=data.keys.p256dh,
            auth=data.keys.auth,
        ))
    db.commit()
    return {"status": "ok"}


@router.delete("/unsubscribe")
def unsubscribe(
    data: PushUnsubscribeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id,
        PushSubscription.endpoint == data.endpoint,
    ).delete()
    db.commit()
    return {"status": "ok"}


@router.get("/status")
def push_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id
    ).count()
    return {"subscribed": count > 0, "count": count, "configured": bool(VAPID_PUBLIC_KEY)}


def send_push_notification(subscription: PushSubscription, title: str, body: str, url: str = "") -> bool:
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return False
    try:
        from pywebpush import WebPushException, webpush
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except Exception as e:
        logging.warning("Push failed for %s: %s", subscription.endpoint[:50], e)
        return False


def send_push_to_user(user_id: int, title: str, body: str, url: str, db: Session) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    sent = 0
    expired_ids = []
    for sub in subs:
        ok = send_push_notification(sub, title, body, url)
        if ok:
            sent += 1
        else:
            expired_ids.append(sub.id)
    if expired_ids:
        db.query(PushSubscription).filter(PushSubscription.id.in_(expired_ids)).delete()
        db.commit()
    return sent
