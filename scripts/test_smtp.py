"""Check QQ SMTP and send one real, one-time Magic Link without printing secrets."""

import argparse
import smtplib
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.auth import AuthStore, deliver_link, normalize_email
from backend.app.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Test QQ SMTP and send a Magic Link.")
    parser.add_argument(
        "--to", help="Test recipient; defaults to SMTP_USERNAME. Never pass an authorization code."
    )
    args = parser.parse_args()

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"Configuration could not be loaded: {type(exc).__name__}", file=sys.stderr)
        return 2

    if settings.auth_delivery != "smtp":
        print("Set AUTH_DELIVERY=smtp before testing.", file=sys.stderr)
        return 2
    if (settings.smtp_host.lower(), settings.smtp_port, settings.smtp_security) != (
        "smtp.qq.com", 465, "ssl"
    ):
        print("QQ SMTP test requires smtp.qq.com:465 and SMTP_SECURITY=ssl.", file=sys.stderr)
        return 2
    if normalize_email(settings.smtp_from) != normalize_email(settings.smtp_username):
        print("SMTP_FROM must match SMTP_USERNAME for this QQ test.", file=sys.stderr)
        return 2

    try:
        recipient = normalize_email(
            validate_email(args.to or settings.smtp_username, check_deliverability=False).normalized
        )
    except EmailNotValidError:
        print("Test recipient email is invalid.", file=sys.stderr)
        return 2

    try:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()
        ) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
        print("QQ SMTP connection, TLS certificate and authorization-code login passed.")
    except Exception as exc:
        print(f"SMTP connection or authentication failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    try:
        token = AuthStore(settings.auth_db).issue_link(
            recipient, "127.0.0.1", settings.auth_link_ttl_seconds,
            settings.auth_email_cooldown_seconds, settings.auth_ip_window_seconds,
            settings.auth_ip_max_requests,
        )
        if token is None:
            print("Email cooldown or IP rate limit blocked this send; retry later.", file=sys.stderr)
            return 3
        deliver_link(settings, recipient, token)
    except Exception as exc:
        print(f"Magic Link send failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print("SMTP server accepted the Magic Link message. Confirm delivery in the inbox.")
    if urlparse(settings.auth_public_url).hostname in ("127.0.0.1", "localhost", "::1"):
        print("The link points to localhost; public login still needs a trusted HTTPS URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
