import os
import stripe
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Log
from ..auth import get_current_user

router = APIRouter(prefix="/stripe", tags=["stripe"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")
PRICE_PLUS = os.getenv("STRIPE_PRICE_PLUS", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost")

PLAN_CONFIG = {
    "basic": {"price_id": PRICE_BASIC, "keyword_limit": 10},
    "plus": {"price_id": PRICE_PLUS, "keyword_limit": 20},
}


@router.post("/checkout/{plan}")
def create_checkout(
    plan: str,
    current_user: User = Depends(get_current_user),
):
    if plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Nepoznat paket")

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[{"price": PLAN_CONFIG[plan]["price_id"], "quantity": 1}],
        success_url=f"{FRONTEND_URL}/dashboard.html?success=1",
        cancel_url=f"{FRONTEND_URL}/dashboard.html?cancelled=1",
        customer_email=current_user.email,
        metadata={"user_id": str(current_user.id), "plan": plan},
    )
    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Neispravan webhook potpis")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session["metadata"]["user_id"])
        plan = session["metadata"]["plan"]

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.subscription_status = "active"
            user.subscription_end = date.today() + timedelta(days=30)
            user.keyword_limit = PLAN_CONFIG[plan]["keyword_limit"]
            user.plan_type = plan
            db.add(Log(event_type="subscription_activated", user_id=user.id, detail=plan))
            db.commit()

    elif event["type"] == "customer.subscription.deleted":
        customer_email = event["data"]["object"].get("customer_email")
        if customer_email:
            user = db.query(User).filter(User.email == customer_email).first()
            if user:
                user.subscription_status = "expired"
                user.keyword_limit = 3
                user.plan_type = "free"
                db.add(Log(event_type="subscription_cancelled", user_id=user.id))
                db.commit()

    return {"status": "ok"}
