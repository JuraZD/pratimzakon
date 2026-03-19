"""
Email notifikacije za PratimZakon.
Šalje email korisnicima kada njihove ključne riječi matchaju nove dokumente u NN.
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict
from sqlalchemy.orm import Session

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USERNAME)
FROM_NAME = os.getenv("FROM_NAME", "PratimZakon")


def _send_smtp(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """Šalje email putem SMTP-a. Vraća True ako je uspješno."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as e:
        logging.error(f"Greška pri slanju emaila na {to_email}: {e}")
        return False


def _build_email(user, matches: List[Dict]) -> tuple[str, str]:
    """
    Gradi HTML i plain-text email s popisom matcheva.
    matches = [{"keyword": str, "document_title": str, "document_url": str}]
    """
    unsubscribe_url = f"{BASE_URL}/auth/unsubscribe?token={user.unsubscribe_token}"

    if user.subscription_status == "active" and user.subscription_end:
        status_text = f"Aktivna pretplata do {user.subscription_end.strftime('%d.%m.%Y.')}"
    else:
        status_text = "Besplatni paket"

    # Plain text
    lines = [
        "Novi pronalasci u Narodnim novinama",
        "=" * 40,
        "",
    ]
    for m in matches:
        lines += [
            f"Ključna riječ: {m['keyword']}",
            f"Dokument: {m['document_title']}",
            f"Link: {m['document_url']}",
            "",
        ]
    lines += [
        f"Status: {status_text}",
        "",
        f"Odjava od obavijesti: {unsubscribe_url}",
        "",
        "PratimZakon – pratimo zakone umjesto vas.",
    ]
    plain = "\n".join(lines)

    # HTML
    cards_html = ""
    for m in matches:
        cards_html += f"""
        <div style="background:#f8f9fa;border-left:4px solid #2563eb;padding:16px;margin-bottom:16px;border-radius:4px;">
            <p style="margin:0 0 4px;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;">
                Ključna riječ: <strong>{m['keyword']}</strong>
            </p>
            <p style="margin:0 0 8px;font-size:15px;font-weight:600;color:#111827;">
                {m['document_title']}
            </p>
            <a href="{m['document_url']}" style="color:#2563eb;font-size:14px;text-decoration:none;">
                Otvori dokument →
            </a>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:32px 0;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <div style="background:#2563eb;padding:24px 32px;">
      <h1 style="color:#fff;margin:0;font-size:20px;font-weight:700;">PratimZakon</h1>
      <p style="color:#bfdbfe;margin:4px 0 0;font-size:14px;">Novi pronalasci u Narodnim novinama</p>
    </div>
    <div style="padding:32px;">
      <p style="color:#374151;margin:0 0 24px;">
        Pronašli smo <strong>{len(matches)} {'dokument' if len(matches) == 1 else 'dokumenata'}</strong>
        koji odgovaraju vašim ključnim riječima:
      </p>
      {cards_html}
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
      <p style="font-size:13px;color:#6b7280;margin:0 0 4px;">Status: {status_text}</p>
      <p style="font-size:12px;color:#9ca3af;margin:0;">
        <a href="{unsubscribe_url}" style="color:#9ca3af;">Odjava od email obavijesti</a>
      </p>
    </div>
  </div>
</body>
</html>"""

    return html, plain


def send_keyword_notifications(new_document_ids: List[int], db: Session) -> Dict[str, int]:
    """
    Pronalazi matcheve između novih dokumenata i korisničkih ključnih riječi.
    Šalje objedinjeni email po korisniku.
    Vraća {"sent": N, "failed": N}.
    """
    from app.models import User, Document, Log

    if not new_document_ids:
        return {"sent": 0, "failed": 0}

    documents = db.query(Document).filter(Document.id.in_(new_document_ids)).all()
    if not documents:
        return {"sent": 0, "failed": 0}

    users = (
        db.query(User)
        .filter(
            User.email_verified == True,
            User.subscription_status != "inactive",
        )
        .all()
    )

    sent = failed = 0

    for user in users:
        if not user.keywords:
            continue

        matches = []
        for kw_obj in user.keywords:
            kw = kw_obj.keyword.lower()
            for doc in documents:
                if kw in doc.title.lower():
                    matches.append({
                        "keyword": kw_obj.keyword,
                        "document_title": doc.title,
                        "document_url": doc.url,
                    })

        if not matches:
            continue

        html_body, text_body = _build_email(user, matches)
        subject = f"PratimZakon: {len(matches)} novih pronalazaka u NN"
        success = _send_smtp(user.email, subject, html_body, text_body)

        event = "email_sent" if success else "email_failed"
        db.add(Log(event_type=event, user_id=user.id, detail=f"{len(matches)} matcheva"))

        if success:
            sent += 1
        else:
            failed += 1

    db.commit()
    logging.info(f"Email notifikacije: {sent} poslano, {failed} neuspješno")
    return {"sent": sent, "failed": failed}
