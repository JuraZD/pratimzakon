"""
Job koji se pokreće svaki dan u 01:00.
Provjerava istekle pretplate i downgrade-a korisnike na free paket.
Šalje admin alert za korisnike kojima pretplata ističe za 5 dana.
"""

import os
import smtplib
import logging
from datetime import date, timedelta
from email.mime.text import MIMEText

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def run():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

    from app.database import SessionLocal
    from app.models import User, Log

    db = SessionLocal()
    today = date.today()
    expired_count = 0
    expiring_soon = []

    try:
        active_users = db.query(User).filter(User.subscription_status == "active").all()

        for user in active_users:
            if not user.subscription_end:
                continue

            days_left = (user.subscription_end - today).days

            if days_left < 0:
                # Downgrade na free – NE brišemo korisnika
                user.subscription_status = "expired"
                user.keyword_limit = 7
                user.plan = "free"
                db.add(Log(event_type="subscription_expired", user_id=user.id))
                expired_count += 1
                logging.info(f"Pretplata istekla: {user.email}")

            elif days_left == 5:
                expiring_soon.append(user.email)

        db.commit()
        logging.info(f"Istek pretplata: {expired_count} korisnika downgrade-ano")

        if expiring_soon:
            _notify_admin(expiring_soon)

    finally:
        db.close()


def _notify_admin(expiring_emails: list):
    """Šalje admin upozorenje za pretplate koje uskoro ističu."""
    admin_email = os.getenv("ADMIN_EMAIL", "")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    if not admin_email or not smtp_user:
        return

    body = "Korisnici čija pretplata ističe za 5 dana:\n\n"
    body += "\n".join(expiring_emails)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"PratimZakon: {len(expiring_emails)} pretplata ističe za 5 dana"
    msg["From"] = from_email
    msg["To"] = admin_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_email, [admin_email], msg.as_string())
        logging.info(f"Admin alert poslan za {len(expiring_emails)} korisnika")
    except Exception as e:
        logging.error(f"Greška pri slanju admin alertа: {e}")


if __name__ == "__main__":
    run()
