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
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
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


def _keyword_matches_document(kw_obj, doc) -> bool:
    """
    Provjerava odgovara li keyword filtrima za dani dokument.
    Ako filter nije postavljen (None), prolazi sve.
    """
    # Provjera teksta
    if kw_obj.keyword.lower() not in doc.title.lower():
        return False

    # Filter po dijelu (SL/MU)
    if kw_obj.part_filter and doc.part:
        if kw_obj.part_filter.upper() != doc.part.upper():
            return False

    # Filter po tipu dokumenta (može biti lista: "ZAKON,UREDBA")
    if kw_obj.doc_type_filter and doc.type:
        allowed_types = [t.strip().upper() for t in kw_obj.doc_type_filter.split(",")]
        if doc.type.upper() not in allowed_types:
            return False

    # Filter po instituciji (substring, case-insensitive)
    if kw_obj.institution_filter and doc.institution:
        if kw_obj.institution_filter.lower() not in doc.institution.lower():
            return False

    return True


def _build_email(user, matches: List[Dict], show_pdf: bool = False) -> tuple[str, str]:
    """
    Gradi HTML i plain-text email s popisom matcheva.
    matches = [{
        "keyword": str,
        "document_title": str,
        "document_url": str,
        "document_pdf_url": str | None,
        "doc_type": str | None,
        "institution": str | None,
    }]
    show_pdf: True za Pro/Expert korisnike
    """
    unsubscribe_url = f"{BASE_URL}/auth/unsubscribe?token={user.unsubscribe_token}"

    plan_labels = {
        "free": "Besplatni plan",
        "basic": "Basic plan",
        "plus": "Plus plan",
        "pro": "Pro plan",
        "expert": "Expert plan",
    }
    plan_name = plan_labels.get(getattr(user, "plan_type", "free"), "Besplatni plan")
    if user.subscription_status == "active" and user.subscription_end:
        status_text = f"{plan_name} · aktivna do {user.subscription_end.strftime('%d.%m.%Y.')}"
    else:
        status_text = plan_name

    user_display = user.email

    # Plain text
    lines = [
        f"Poštovani {user_display},",
        "",
        "Novi pronalasci u Narodnim novinama",
        "=" * 40,
        "",
    ]
    for m in matches:
        lines += [f"Ključna riječ: {m['keyword']}"]
        if m.get("doc_type"):
            lines.append(f"Tip: {m['doc_type']}")
        if m.get("institution"):
            lines.append(f"Institucija: {m['institution']}")
        lines += [
            f"Dokument: {m['document_title']}",
            f"HTML: {m['document_url']}",
        ]
        if show_pdf and m.get("document_pdf_url"):
            lines.append(f"PDF: {m['document_pdf_url']}")
        lines.append("")
    lines += [
        f"Pretplata: {status_text}",
        "",
        f"Odjava od obavijesti: {unsubscribe_url}",
        "",
        "PratimZakon – pratimo zakone umjesto vas.",
    ]
    plain = "\n".join(lines)

    # HTML kartice
    cards_html = ""
    for m in matches:
        meta_parts = []
        if m.get("doc_type"):
            meta_parts.append(
                f'<span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600;letter-spacing:.4px;">'
                f'{m["doc_type"]}</span>'
            )
        if m.get("institution"):
            meta_parts.append(
                f'<span style="color:#6b7280;font-size:12px;">{m["institution"]}</span>'
            )
        meta_html = (
            f'<p style="margin:0 0 8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
            f'{"&nbsp;·&nbsp;".join(meta_parts)}</p>'
            if meta_parts else ""
        )

        pdf_html = ""
        if show_pdf and m.get("document_pdf_url"):
            pdf_html = (
                f'<a href="{m["document_pdf_url"]}" style="display:inline-block;margin-top:8px;'
                f'padding:5px 12px;background:#ef4444;color:#fff;font-size:12px;font-weight:600;'
                f'border-radius:4px;text-decoration:none;">↓ PDF</a>'
            )

        cards_html += f"""
        <div style="background:#f8f9fa;border-left:4px solid #2563eb;padding:16px;margin-bottom:16px;border-radius:4px;">
            <p style="margin:0 0 4px;font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;">
                Ključna riječ: <strong>{m['keyword']}</strong>
            </p>
            {meta_html}
            <p style="margin:0 0 8px;font-size:15px;font-weight:600;color:#111827;">
                {m['document_title']}
            </p>
            <a href="{m['document_url']}" style="color:#2563eb;font-size:14px;text-decoration:none;">
                Otvori dokument →
            </a>
            {pdf_html}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:32px 0;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.10);">
    <div style="background:#2563eb;padding:24px 36px;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;letter-spacing:-.3px;">PratimZakon</h1>
      <p style="color:#bfdbfe;margin:6px 0 0;font-size:14px;">Novi pronalasci u Narodnim novinama</p>
    </div>
    <div style="padding:32px 36px;">
      <p style="color:#374151;margin:0 0 6px;font-size:14px;">Poštovani <strong>{user_display}</strong>,</p>
      <p style="color:#374151;margin:0 0 24px;font-size:15px;line-height:1.6;">
        Pronašli smo <strong>{len(matches)} {'novi dokument' if len(matches) == 1 else 'nova dokumenta' if len(matches) < 5 else 'novih dokumenata'}</strong>
        koji odgovaraju vašim ključnim riječima:
      </p>
      {cards_html}
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0 16px;">
      <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px 16px;margin-bottom:16px;">
        <p style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;margin:0 0 4px;">Vaša pretplata</p>
        <p style="font-size:14px;color:#111827;font-weight:600;margin:0;">{status_text}</p>
      </div>
      <p style="font-size:12px;color:#9ca3af;margin:0;line-height:1.6;">
        Primili ste ovaj email jer pratite Narodne novine putem PratimZakon.<br>
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
    Primjenjuje filtere (tip, institucija, dio) po ključnoj riječi.
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

        plan = getattr(user, "plan_type", "free")
        show_pdf = plan in ("pro", "expert")

        matches = []
        for kw_obj in user.keywords:
            for doc in documents:
                if not _keyword_matches_document(kw_obj, doc):
                    continue
                matches.append({
                    "keyword": kw_obj.keyword,
                    "document_title": doc.title,
                    "document_url": doc.url,
                    "document_pdf_url": doc.pdf_url,
                    "doc_type": doc.type or None,
                    "institution": doc.institution or None,
                })

        if not matches:
            continue

        html_body, text_body = _build_email(user, matches, show_pdf=show_pdf)
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
