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
from app.ai.matcher import check_document_for_user, generate_summary

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
    is_paid = getattr(user, "plan", "free") in ("pro", "expert")

    plan_labels = {
        "free": "Besplatni plan",
        "basic": "Basic plan",
        "plus": "Plus plan",
        "pro": "Pro plan",
        "expert": "Expert plan",
    }
    plan_name = plan_labels.get(getattr(user, "plan_type", "free"), "Besplatni plan")
    if user.subscription_status == "active" and user.subscription_end:
        status_text = (
            f"{plan_name} · aktivna do {user.subscription_end.strftime('%d.%m.%Y.')}"
        )
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
        badges_html = ""
        if m.get("doc_type"):
            badges_html += (
                f"<span style=\"font-family:'Courier New',monospace;display:inline-block;"
                f"border:1px solid #111111;padding:1px 8px;font-size:10px;letter-spacing:.5px;"
                f'color:#111111;margin-right:6px;">{m["doc_type"]}</span>'
            )
        meta_parts = []
        if m.get("institution"):
            meta_parts.append(m["institution"])
        meta_html = (
            f"<p style=\"font-family:'Courier New',monospace;margin:4px 0 8px;"
            f'font-size:11px;color:#6b6b6b;">{" · ".join(meta_parts)}</p>'
            if meta_parts
            else ""
        )

        pdf_html = ""
        if show_pdf and m.get("document_pdf_url"):
            pdf_html = (
                f'<a href="{m["document_pdf_url"]}" style="display:inline-block;margin-left:12px;'
                f"border:1px solid #111111;padding:3px 10px;font-size:11px;color:#111111;"
                f'text-decoration:none;">↓ PDF</a>'
            )

        cards_html += f"""
        <div style="border:1px solid rgba(0,0,0,0.12);border-left:3px solid #111111;padding:16px 20px;margin-bottom:12px;background:#ffffff;">
            <p style="font-family:'Courier New',monospace;margin:0 0 6px;font-size:10px;color:#6b6b6b;text-transform:uppercase;letter-spacing:1px;">
                {m['keyword']}
            </p>
            {badges_html}
            {meta_html}
            <p style="margin:6px 0 10px;font-size:14px;font-weight:600;color:#111111;font-family:Georgia,serif;line-height:1.4;">
                {m['document_title']}
            </p>
            {f'''<p style="margin:0 0 10px;font-size:13px;color:#444;line-height:1.6;font-family:system-ui,sans-serif;">
            {m.get("summary", "")}
            </p>''' if m.get("summary") else ""}
            <a href="{m['document_url']}" style="font-family:'Courier New',monospace;color:#111111;font-size:12px;text-decoration:none;border-bottom:1px solid #111111;">
                Otvori dokument →
            </a>{pdf_html}
        </div>"""

    n = len(matches)
    doc_word = (
        "novi dokument"
        if n == 1
        else ("nova dokumenta" if n < 5 else "novih dokumenata")
    )

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#F5F4F1;margin:0;padding:32px 16px;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid rgba(0,0,0,0.12);">
    <div style="background:#111111;padding:24px 32px;">
      <h1 style="color:#ffffff;margin:0;font-size:20px;font-weight:600;letter-spacing:-.3px;font-family:Georgia,serif;">PratimZakon</h1>
      <p style="font-family:'Courier New',monospace;color:rgba(255,255,255,0.5);margin:6px 0 0;font-size:11px;letter-spacing:.5px;text-transform:uppercase;">Novi pronalasci u Narodnim novinama</p>
    </div>
    <div style="padding:32px;">
      <p style="font-family:'Courier New',monospace;color:#6b6b6b;margin:0 0 4px;font-size:11px;">{user_display}</p>
      <p style="font-family:Georgia,serif;color:#111111;margin:0 0 24px;font-size:18px;font-weight:400;line-height:1.4;">
        Pronašli smo <strong>{n} {doc_word}</strong> koji odgovaraju vašim ključnim riječima.
      </p>
      {cards_html}
      <hr style="border:none;border-top:1px solid rgba(0,0,0,0.12);margin:24px 0 20px;">
      <div style="border:1px solid rgba(0,0,0,0.12);padding:12px 16px;margin-bottom:20px;background:#F5F4F1;">
        <p style="font-family:'Courier New',monospace;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#6b6b6b;margin:0 0 4px;">Vaša pretplata</p>
        <p style="font-family:'Courier New',monospace;font-size:13px;color:#111111;margin:0;">{status_text}</p>
      </div>
      <p style="font-family:'Courier New',monospace;font-size:11px;color:#9ca3af;margin:0;line-height:1.8;">
        Primili ste ovaj email jer pratite Narodne novine putem PratimZakon.<br>
        <a href="{unsubscribe_url}" style="color:#9ca3af;text-decoration:none;border-bottom:1px solid #9ca3af;">Odjava od email obavijesti</a>
      </p>
    </div>
  </div>
</body>
</html>"""

    return html, plain


def scan_documents_for_user(user_id: int, db: Session) -> int:
    """
    Skenira SVE dokumente u bazi za ključne riječi jednog korisnika.
    Sprema keyword_match logove bez slanja emaila.
    Preskače već postojeće matcheve (ne duplikira).
    Vraća broj novih podudaranja.
    """
    from app.models import User, Document, Log

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.keywords:
        return 0

    # Dohvati detalje već postojećih matcheva za korisnika (doc_id + keyword)
    existing_rows = (
        db.query(Log.detail)
        .filter(Log.user_id == user_id, Log.event_type == "keyword_match")
        .all()
    )
    existing_details = {r[0] for r in existing_rows if r[0]}

    documents = db.query(Document).all()

    new_count = 0
    for doc in documents:
        for kw in user.keywords:
            if _keyword_matches_document(kw, doc):
                detail = f"keyword:{kw.keyword}|doc_id:{doc.id}|title:{doc.title[:100]}"
                if detail not in existing_details:
                    db.add(
                        Log(
                            event_type="keyword_match",
                            user_id=user_id,
                            detail=detail,
                        )
                    )
                    existing_details.add(detail)
                    new_count += 1

    if new_count > 0:
        db.commit()

    logging.info(f"scan_documents_for_user({user_id}): {new_count} novih podudaranja")
    return new_count


def send_keyword_notifications(
    new_document_ids: List[int], db: Session
) -> Dict[str, int]:
    """
    Pronalazi matcheve između novih dokumenata i korisnika.
    Koristi AI matcher — tri razine provjere.
    Šalje objedinjeni email po korisniku.
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
            User.email_notifications_enabled == True,
        )
        .all()
    )

    sent = failed = 0

    for user in users:
        # Preskoči korisnike bez keywords i bez situacije
        if not user.keywords and not getattr(user, "situation", None):
            continue

        plan = getattr(user, "plan_type", "free")
        show_pdf = plan in ("pro", "expert")
        situation = getattr(user, "situation", "") or ""

        matches = []

        for doc in documents:
            is_rel, reason = check_document_for_user(doc, user)

            if not is_rel:
                continue

            # Generiraj sažetak samo za relevantne dokumente
            summary = generate_summary(doc, situation)

            matches.append(
                {
                    "keyword": reason,
                    "document_title": doc.title,
                    "document_url": doc.url,
                    "document_pdf_url": doc.pdf_url,
                    "doc_type": doc.type or None,
                    "institution": doc.institution or None,
                    "summary": summary,
                }
            )

        if not matches:
            continue

        html_body, text_body = _build_email(user, matches, show_pdf=show_pdf)
        subject = f"PratimZakon: {len(matches)} novih pronalazaka u NN"
        success = _send_smtp(user.email, subject, html_body, text_body)

        event = "email_sent" if success else "email_failed"
        db.add(
            Log(
                event_type=event,
                user_id=user.id,
                detail=f"{len(matches)} matcheva (AI)",
            )
        )

        if success:
            sent += 1
        else:
            failed += 1

    db.commit()
    logging.info(f"Email notifikacije: {sent} poslano, {failed} neuspješno")
    return {"sent": sent, "failed": failed}
