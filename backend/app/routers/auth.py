import os
import smtplib
import secrets
from email.mime.text import MIMEText
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, Log
from ..schemas import UserRegister, UserLogin, Token, UserOut
from ..auth import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", os.getenv("FROM_EMAIL", ""))


def _send_plan_interest_email(user_email: str, plan: str):
    """Šalje adminu obavijest da je korisnik odabrao plaćeni plan."""
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("FROM_EMAIL", smtp_user)
    from_name = os.getenv("FROM_NAME", "PratimZakon")
    admin_email = os.getenv("ADMIN_EMAIL", from_email)

    plan_labels = {"pro": "Pro (€4,99/mj)", "expert": "Expert (€7,99/mj)"}
    label = plan_labels.get(plan, plan)

    body = f"""Novi korisnik odabrao plaćeni plan pri registraciji.

Email: {user_email}
Plan: {label}

Kontaktirajte korisnika za aktivaciju plana.
"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"PratimZakon: Novi zahtjev za plan {label}"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = admin_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_email, [admin_email], msg.as_string())
    except Exception:
        pass


def _send_verification_email(email: str, token: str):
    link = f"{BASE_URL}/auth/verify-email?token={token}"
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("FROM_EMAIL", smtp_user)
    from_name = os.getenv("FROM_NAME", "PratimZakon")

    body = f"""Dobrodošli u PratimZakon!

Potvrdite svoju email adresu klikom na link:
{link}

Ukoliko niste vi kreirali račun, ignorirajte ovaj email.
"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Potvrdite email – PratimZakon"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_email, [email], msg.as_string())
    except Exception:
        pass  # Ne blokiraj registraciju ako email fail


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email već postoji")

    verification_token = secrets.token_urlsafe(32)
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        unsubscribe_token=secrets.token_urlsafe(32),
        email_verified=True,  # Auto-verify za testiranje
    )
    # Koristimo unsubscribe_token privremeno za verifikaciju – u produkciji dodaj poseban stupac
    user._verification_token = verification_token
    db.add(user)
    db.commit()
    db.refresh(user)

    log_detail = user.email
    if data.selected_plan and data.selected_plan in ("pro", "expert"):
        log_detail = f"{user.email} [plan_interest={data.selected_plan}]"
    db.add(Log(event_type="signup", user_id=user.id, detail=log_detail))
    db.commit()

    _send_verification_email(data.email, user.unsubscribe_token)

    if data.selected_plan and data.selected_plan in ("pro", "expert"):
        _send_plan_interest_email(data.email, data.selected_plan)

    return user


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Neispravan token")
    if user.email_verified:
        return {"message": "Email već verificiran"}
    user.email_verified = True
    # Generiraj novi unsubscribe token (da ne bude isti kao verifikacijski)
    user.unsubscribe_token = secrets.token_urlsafe(32)
    db.commit()
    return {"message": "Email verificiran. Možete se prijaviti."}


@router.post("/login", response_model=Token)
def login(data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Pogrešan email ili lozinka")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Potvrdite email adresu")

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/unsubscribe")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Neispravan token")
    user.subscription_status = "inactive"
    db.add(Log(event_type="unsubscribe", user_id=user.id))
    db.commit()
    return {"message": "Uspješno odjavljeni od email obavijesti."}
