"""Gửi email qua Gmail SMTP (App Password).

Gửi thử:  python -m mvdis_crew.mailer --test
"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from .config import Settings, load_settings

log = logging.getLogger(__name__)


def send_email(settings: Settings, subject: str, html_body: str, attachments: list[str] | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = ", ".join(settings.mail_to)
    msg.set_content("Email này cần trình đọc hỗ trợ HTML.")
    msg.add_alternative(html_body, subtype="html")

    for path in attachments or []:
        p = Path(path)
        if not p.is_file():
            continue
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(settings.gmail_address, settings.gmail_app_password)
        smtp.send_message(msg)
    log.info("Đã gửi email '%s' tới %s", subject, msg["To"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if "--test" not in sys.argv:
        sys.exit("Dùng: python -m mvdis_crew.mailer --test")
    s = load_settings(require_mvdis=False)
    send_email(s, "[MVDIS] Email thử nghiệm", "<p>Cấu hình Gmail SMTP hoạt động.</p>")
