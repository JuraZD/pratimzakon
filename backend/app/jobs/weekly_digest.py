"""
Tjedni sažetak — šalje korisnicima koji su uključili digest
pregled svih matcheva iz proteklih 7 dana.
Pokreće se ponedjeljkom ujutro.
"""

import os
import sys
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SMTP_SERVER   = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT") or "587")
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL    = os.getenv("FROM_EMAIL", SMTP_USERNAME)
FROM_NAME     = os.getenv("FROM_NAME", "PratimZakon")
BASE_URL      = os.getenv("BASE_URL", "https://jurazd.github.io/pratimzakon")


def _send(to_email: str, subject: str, html: str, text: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as e:
        logging.error(f"Greška pri slanju na {to_email}: {e}")
        return False


def _build_digest(user, matches_by_kw: dict) -> tuple[str, str]:
    """Gradi HTML i plain-text digest email."""
    unsubscribe_url = f"{BASE_URL}/frontend/index.html"
    if hasattr(user, "unsubscribe_token") and user.unsubscribe_token:
        unsubscribe_url = f"https://pratimzakon.onrender.com/auth/unsubscribe?token={user.unsubscribe_token}"

    total = sum(len(v) for v in matches_by_kw.values())
    week_label = f"{(datetime.utcnow() - timedelta(days=7)).strftime('%d.%m.')}–{datetime.utcnow().strftime('%d.%m.%Y.')}"

    # Plain text
    lines = [
        f"PratimZakon — tjedni sažetak za {week_label}",
        f"Ukupno novih matcheva: {total}",
        "=" * 50,
        "",
    ]
    for kw, items in matches_by_kw.items():
        lines.append(f"Ključna riječ: {kw} ({len(items)} pronalazaka)")
        for item in items:
            lines.append(f"  • {item['title']}")
            if item.get("url"):
                lines.append(f"    {item['url']}")
        lines.append("")
    lines += [
        "Isključi tjedni sažetak:",
        unsubscribe_url,
        "",
        "PratimZakon – pratimo zakone umjesto vas.",
    ]
    plain = "\n".join(lines)

    # HTML
    rows_html = ""
    for kw, items in matches_by_kw.items():
        rows_html += f"""
        <tr>
          <td colspan="2" style="padding:14px 0 6px;font-family:'Barlow Condensed',Arial,sans-serif;
              font-size:13px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;
              color:#6b7a99;border-top:1px solid #e0dbd0;">
            {kw} &nbsp;<span style="color:#2e7d52;font-size:11px;">({len(items)} novih)</span>
          </td>
        </tr>"""
        for item in items:
            doc_url = item.get("url", "#")
            sazetak_url = f"{BASE_URL}/sazetak.html?id={item['doc_id']}&kw={kw}" if item.get("doc_id") else ""
            rows_html += f"""
        <tr>
          <td style="padding:6px 0;font-size:13.5px;color:#1a2744;line-height:1.4;vertical-align:top;">
            {item['title']}
          </td>
          <td style="padding:6px 0 6px 12px;white-space:nowrap;vertical-align:top;">
            <a href="{doc_url}" style="color:#b84d1b;font-size:12px;font-weight:600;text-decoration:none;margin-right:8px;">NN.hr ↗</a>
            {"<a href='" + sazetak_url + "' style='color:#1a2744;font-size:12px;font-weight:600;text-decoration:none;'>Sažetak →</a>" if sazetak_url else ""}
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="hr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>PratimZakon — Tjedni sažetak</title></head>
<body style="margin:0;padding:0;background:#e8e4da;font-family:'Barlow',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#e8e4da;padding:32px 0;">
  <tr><td align="center">
    <table width="580" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:4px;overflow:hidden;">
      <!-- Header -->
      <tr>
        <td style="background:#1a2744;padding:22px 32px;">
          <span style="font-family:'Barlow Condensed',Arial,sans-serif;font-size:22px;font-weight:800;
              letter-spacing:1px;text-transform:uppercase;color:#e8e4da;">
            PRATIM<span style="color:#d4581f;">ZAKON</span>
          </span>
          <span style="float:right;background:rgba(212,88,31,0.2);color:#d4581f;
              font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;
              padding:4px 10px;border-radius:2px;margin-top:4px;">
            TJEDNI SAŽETAK
          </span>
        </td>
      </tr>
      <!-- Summary bar -->
      <tr>
        <td style="background:#f2efe8;padding:14px 32px;border-bottom:2px solid #1a2744;">
          <span style="font-size:13px;color:#6b7a99;">
            {week_label} &nbsp;·&nbsp;
            <strong style="color:#1a2744;">{total} novih pronalazaka</strong>
          </span>
        </td>
      </tr>
      <!-- Matches -->
      <tr>
        <td style="padding:8px 32px 24px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            {rows_html}
          </table>
        </td>
      </tr>
      <!-- Footer -->
      <tr>
        <td style="background:#f2efe8;padding:16px 32px;border-top:1px solid #e0dbd0;">
          <span style="font-size:11px;color:#6b7a99;">
            Primljeno jer ste uključili tjedni sažetak u PratimZakon.
            &nbsp;<a href="{unsubscribe_url}" style="color:#b84d1b;">Isključi</a>
          </span>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    return html, plain


def run():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

    from app.database import SessionLocal
    from app.models import User, Log

    db = SessionLocal()
    cutoff = datetime.utcnow() - timedelta(days=7)
    sent_count = 0

    try:
        # Korisnici s digest uključenim — najnovija pref_digest log stavka je "enabled:1"
        all_users = (
            db.query(User)
            .filter(User.email_notifications_enabled == True)
            .all()
        )

        digest_users = []
        for user in all_users:
            latest_pref = (
                db.query(Log)
                .filter(Log.user_id == user.id, Log.event_type == "pref_digest")
                .order_by(Log.timestamp.desc())
                .first()
            )
            if latest_pref and latest_pref.detail == "enabled:1":
                digest_users.append(user)

        logging.info(f"Digest šaljem za {len(digest_users)} korisnika")

        for user in digest_users:
            # Dohvati keyword_match logove iz zadnjih 7 dana
            match_logs = (
                db.query(Log)
                .filter(
                    Log.user_id == user.id,
                    Log.event_type == "keyword_match",
                    Log.timestamp >= cutoff,
                )
                .order_by(Log.timestamp.desc())
                .all()
            )

            if not match_logs:
                logging.info(f"Nema matcheva za {user.email} — preskačem")
                continue

            # Grupiraj po ključnoj riječi
            matches_by_kw = defaultdict(list)
            seen = set()
            for log in match_logs:
                detail = log.detail or ""
                parts = dict(p.split(":", 1) for p in detail.split("|") if ":" in p)
                kw    = parts.get("keyword", "—")
                doc_id = parts.get("doc_id", "")
                title  = parts.get("title", "Nepoznat dokument")
                key = f"{kw}:{doc_id}"
                if key in seen:
                    continue
                seen.add(key)
                # Dohvati URL iz Documents tablice
                url = ""
                if doc_id:
                    from app.models import Document
                    doc = db.query(Document).filter(Document.id == int(doc_id)).first() if doc_id.isdigit() else None
                    url = doc.url if doc else ""
                matches_by_kw[kw].append({"doc_id": doc_id, "title": title, "url": url})

            html, plain = _build_digest(user, dict(matches_by_kw))
            total = sum(len(v) for v in matches_by_kw.values())
            subject = f"PratimZakon — {total} novih pronalazaka ovaj tjedan"

            if _send(user.email, subject, html, plain):
                sent_count += 1
                logging.info(f"Digest poslan: {user.email} ({total} matcheva)")
            else:
                logging.warning(f"Digest nije poslan: {user.email}")

    finally:
        db.close()

    logging.info(f"Tjedni digest završen — {sent_count} emailova poslano")


if __name__ == "__main__":
    run()
