import os
import stripe
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Log, PLAN_LIMITS
from ..auth import get_current_user

router = APIRouter(prefix="/stripe", tags=["stripe"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")
PRICE_PLUS = os.getenv("STRIPE_PRICE_PLUS", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost")

PLAN_CONFIG = {
    "basic": {"price_id": PRICE_BASIC, "keyword_limit": PLAN_LIMITS["basic"]},
    "plus":  {"price_id": PRICE_PLUS,  "keyword_limit": PLAN_LIMITS["plus"]},
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


@router.post("/switch-plan/{plan}")
def switch_plan(
    plan: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Prebacuje između plaćenih planova (Basic <-> Plus) via Stripe Subscription Update."""
    if plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Nepoznat paket")

    if current_user.subscription_status != "active":
        raise HTTPException(status_code=400, detail="Nemate aktivnu pretplatu")

    current_plan_type = current_user.plan_type
    if current_plan_type == plan or (current_plan_type in ("pro",) and plan == "basic") or (current_plan_type in ("expert",) and plan == "plus"):
        raise HTTPException(status_code=400, detail="Već ste na tom planu")

    sub_id = getattr(current_user, "stripe_subscription_id", None)
    if sub_id:
        try:
            subscription = stripe.Subscription.retrieve(sub_id)
            item_id = subscription["items"]["data"][0]["id"]
            stripe.Subscription.modify(
                sub_id,
                items=[{"id": item_id, "price": PLAN_CONFIG[plan]["price_id"]}],
                proration_behavior="always_invoice",
            )
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe greška: {str(e)}")

    # Ažuriraj plan u bazi
    current_user.plan = plan
    current_user.plan_type = plan
    current_user.keyword_limit = PLAN_CONFIG[plan]["keyword_limit"]
    db.add(Log(event_type="plan_set", user_id=current_user.id, detail=f"{current_user.email} [switch->{plan}]"))
    db.commit()

    return {"message": f"Plan uspješno promijenjen na {plan}"}


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
            user.plan = plan
            user.plan_type = plan
            user.stripe_subscription_id = session.get("subscription")
            db.add(Log(event_type="subscription_activated", user_id=user.id, detail=plan))
            db.commit()

    elif event["type"] == "customer.subscription.deleted":
        customer_email = event["data"]["object"].get("customer_email")
        if customer_email:
            user = db.query(User).filter(User.email == customer_email).first()
            if user:
                user.subscription_status = "expired"
                user.keyword_limit = PLAN_LIMITS["free"]
                user.plan = "free"
                user.plan_type = "free"
                db.add(Log(event_type="subscription_cancelled", user_id=user.id))
                db.commit()

    return {"status": "ok"}
