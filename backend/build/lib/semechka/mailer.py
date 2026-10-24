"""Email delivery. Used only when SMTP is configured; otherwise callers report 'not configured'."""
from __future__ import annotations

import smtplib
import ssl
import threading
from email.message import EmailMessage


def configured(settings, s) -> bool:
    return bool(settings.deployment(s, "smtp.host") and settings.deployment(s, "smtp.from_address"))


def smtp_params(settings, s) -> dict:
    d = settings.deployment
    return dict(host=d(s, "smtp.host"), port=d(s, "smtp.port"), security=d(s, "smtp.security"),
                username=d(s, "smtp.username"), password=d(s, "smtp.password"), sender=d(s, "smtp.from_address"))


def send_sync(p: dict, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = p["sender"], to, subject
    msg.set_content(body)
    if p["security"] == "ssl":
        srv = smtplib.SMTP_SSL(p["host"], p["port"], timeout=15, context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(p["host"], p["port"], timeout=15)
    with srv:
        if p["security"] == "starttls":
            srv.starttls(context=ssl.create_default_context())
        if p["username"]:
            srv.login(p["username"], p["password"])
        srv.send_message(msg)


def send_background(p: dict, to: str, subject: str, body: str, on_error=None) -> None:
    def run():
        try:
            send_sync(p, to, subject, body)
        except Exception as e:  # noqa: BLE001
            if on_error:
                on_error(e)
    threading.Thread(target=run, daemon=True).start()
