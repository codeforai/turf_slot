"""Outgoing email. Called from FastAPI BackgroundTasks so slow SMTP never blocks a request."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger("turfslot.email")

# Messages "sent" with EMAIL_BACKEND=memory (used by the test suite).
outbox: list[EmailMessage] = []


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if settings.email_backend == "memory":
            outbox.append(message)
        elif settings.email_backend == "console":
            extra = {"to": to, "subject": subject}
            if settings.environment != "production":
                extra["body"] = body
            logger.info("email (console backend, not delivered)", extra=extra)
        else:
            context = ssl.create_default_context()
            if settings.smtp_port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    settings.smtp_host, settings.smtp_port, timeout=10, context=context
                )
            else:
                server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
            with server:
                if settings.smtp_port != 465 and settings.smtp_use_tls:
                    server.starttls(context=context)
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
            logger.info("email sent", extra={"to": to, "subject": subject})
    except Exception:
        # Email is best-effort: a mail outage must not fail bookings or payments.
        logger.exception("email delivery failed", extra={"to": to, "subject": subject})
