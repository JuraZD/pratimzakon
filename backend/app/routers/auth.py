import os
import smtplib
import secrets
from email.mime.multipart import MIMEMultipart
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

    plan_labels = {"pro": "Pro (€4,99/mj)", "expert": "Expert (€7,99/mj)", "basic": "Basic (€4,99/mj)", "plus": "Plus (€7,99/mj)"}
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


def _smtp_cfg():
    return {
        "server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_email": os.getenv("FROM_EMAIL", os.getenv("SMTP_USERNAME", "")),
        "from_name": os.getenv("FROM_NAME", "PratimZakon"),
    }


def _send_multipart(to_email: str, subject: str, html: str, plain: str):
    cfg = _smtp_cfg()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
    msg["To"] = to_email
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(cfg["server"], cfg["port"]) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["from_email"], [to_email], msg.as_string())
    except Exception:
        pass


def _send_verification_email(email: str, token: str):
    dashboard_url = os.getenv("FRONTEND_URL", "https://jurazd.github.io/pratimzakon/dashboard.html")
    unsubscribe_url = f"{BASE_URL}/auth/unsubscribe?token={token}"

    plain = f"""Dobrodošli u PratimZakon!

Hvala što ste se registrirali. Vaš besplatni račun je aktivan.

Što dobivate:
- Praćenje do 3 ključne riječi
- Automatska obavijest svaki radni dan u 07:00
- Pratimo nova objavljivanja u Narodnim novinama

Otvorite dashboard: {dashboard_url}

Ako ne želite primati obavijesti: {unsubscribe_url}

S poštovanjem,
Tim PratimZakon
"""

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:32px 0;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.10);">

    <div style="background:#2563eb;padding:28px 36px;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;letter-spacing:-.3px;">PratimZakon</h1>
      <p style="color:#bfdbfe;margin:6px 0 0;font-size:14px;">pratimo zakone umjesto vas</p>
    </div>

    <div style="padding:36px;">
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Dobrodošli! 👋</h2>
      <p style="color:#374151;font-size:15px;margin:0 0 24px;line-height:1.6;">
        Hvala što ste se registrirali na <strong>PratimZakon</strong>.<br>
        Vaš besplatni račun je aktivan — pratit ćemo Narodne novine
        i javljati vam samo kada ima nešto relevantno za vaše ključne riječi.
      </p>

      <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:20px;margin-bottom:28px;">
        <p style="margin:0 0 12px;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#1d4ed8;">Što dobivate uz besplatni plan</p>
        <ul style="margin:0;padding:0 0 0 18px;color:#374151;font-size:14px;line-height:2;">
          <li>Praćenje do <strong>3 ključne riječi</strong></li>
          <li>Automatska obavijest svaki radni dan u <strong>07:00</strong></li>
          <li>Javljamo se samo kada ima novih pronalazaka</li>
        </ul>
      </div>

      <a href="{dashboard_url}"
         style="display:inline-block;background:#2563eb;color:#fff;font-size:15px;font-weight:700;
                padding:13px 28px;border-radius:7px;text-decoration:none;letter-spacing:-.1px;">
        Otvori dashboard →
      </a>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0 20px;">
      <p style="font-size:12px;color:#9ca3af;margin:0;line-height:1.6;">
        Primili ste ovaj email jer ste se registrirali na PratimZakon.<br>
        Ako ne želite primati obavijesti,
        <a href="{unsubscribe_url}" style="color:#9ca3af;">odjavite se ovdje</a>.
      </p>
    </div>
  </div>
</body>
</html>"""

    _send_multipart(email, "Dobrodošli u PratimZakon!", html, plain)


def _send_goodbye_email(email: str):
    frontend_url = os.getenv("FRONTEND_URL", "https://jurazd.github.io/pratimzakon/index.html")

    plain = f"""Odjava potvrđena – PratimZakon

Poštovani {email},

žao nam je što odlazite.

Uspješno smo vas odjavili od svih email obavijesti. Više nećete primati
obavijesti o novim objavama u Narodnim novinama.

Vaš korisnički račun ostaje aktivan — možete se prijaviti i nastaviti
pratiti zakone kada god poželite.

Ako se ikada predomislite, jednostavno se prijavite na dashboard
i obavijesti će opet krenuti:
{frontend_url}

S poštovanjem,
Tim PratimZakon
"""

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:32px 0;">
  <div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.10);">

    <div style="background:#2563eb;padding:28px 36px;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;letter-spacing:-.3px;">PratimZakon</h1>
      <p style="color:#bfdbfe;margin:6px 0 0;font-size:14px;">pratimo zakone umjesto vas</p>
    </div>

    <div style="padding:36px;">
      <h2 style="margin:0 0 8px;font-size:20px;color:#111827;">Žao nam je što odlazite</h2>
      <p style="color:#374151;font-size:15px;margin:0 0 20px;line-height:1.6;">
        Poštovani <strong>{email}</strong>,<br><br>
        uspješno smo vas odjavili od svih email obavijesti.
        Više nećete primati obavijesti o novim objavama u Narodnim novinama.
      </p>

      <div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:18px;margin-bottom:28px;">
        <p style="margin:0;font-size:14px;color:#713f12;line-height:1.6;">
          💡 <strong>Vaš korisnički račun ostaje aktivan.</strong><br>
          Možete se prijaviti i nastaviti pratiti zakone kada god poželite —
          obavijesti ćete moći ponovo uključiti iz dashboarda.
        </p>
      </div>

      <a href="{frontend_url}"
         style="display:inline-block;background:#2563eb;color:#fff;font-size:15px;font-weight:700;
                padding:13px 28px;border-radius:7px;text-decoration:none;">
        Povratak na PratimZakon
      </a>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0 20px;">
      <p style="font-size:12px;color:#9ca3af;margin:0;line-height:1.6;">
        Ako ste se odjavili greškom, prijavite se na dashboard i ključne
        riječi će nastaviti s praćenjem.
      </p>
    </div>
  </div>
</body>
</html>"""

    _send_multipart(email, "Odjava potvrđena – PratimZakon", html, plain)


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


@router.post("/request-plan")
def request_plan(plan: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if plan not in ("basic", "plus"):
        raise HTTPException(status_code=400, detail="Neispravan plan")
    db.add(Log(event_type="plan_request", user_id=current_user.id, detail=f"{current_user.email} [plan={plan}]"))
    db.commit()
    _send_plan_interest_email(current_user.email, plan)
    return {"message": "Zahtjev primljen"}


@router.get("/unsubscribe")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Neispravan token")
    user.subscription_status = "inactive"
    db.add(Log(event_type="unsubscribe", user_id=user.id))
    db.commit()
    _send_goodbye_email(user.email)
    return {"message": "Uspješno odjavljeni od email obavijesti."}
