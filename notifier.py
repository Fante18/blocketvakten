"""E-mail notifications via SMTP (stdlib only)."""

from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage

import config


def _price_text(price) -> str:
    if price is None:
        return "Pris ej angivet"
    return f"{price:,}".replace(",", " ") + " kr"


def _header_text(value: str) -> str:
    """Avoid malformed headers when a user enters a name containing newlines."""
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def send_email_for_listings(
    search_name: str, listings: list[dict], recipient: str | None = None
) -> bool:
    """Send one HTML/text e-mail summarizing all listings from one check run."""
    recipient = (recipient or config.EMAIL_TO or "").strip()
    if not config.EMAIL_ENABLED or not recipient or not listings:
        return False

    clean_name = _header_text(search_name) or "Blocketbevakning"
    heading = html.escape(clean_name)
    html_rows = []
    text_rows = []
    for listing in listings:
        title_text = listing.get("title") or "(utan titel)"
        title = html.escape(title_text)
        url_text = listing.get("url") or ""
        url = html.escape(url_text, quote=True)
        price = _price_text(listing.get("price"))
        location = html.escape(listing.get("location") or "")
        image_url_text = listing.get("image_url") or ""
        image_url = html.escape(image_url_text, quote=True)
        image = (
            f'<p><img src="{image_url}" alt="{title}" '
            'style="max-width:320px;max-height:220px;object-fit:cover"></p>'
            if image_url
            else ""
        )
        html_rows.append(
            "<li style=\"margin-bottom:18px\">"
            f"{image}<a href=\"{url}\"><strong>{title}</strong></a>"
            f"<br>{html.escape(price)}"
            + (f" · {location}" if location else "")
            + f'<br><a href="{url}">Öppna annonsen på Blocket</a></li>'
        )
        text_rows.append(
            f"- {title_text} — {price}"
            + (f" · {listing.get('location')}" if listing.get("location") else "")
            + f"\n  {url_text}"
        )

    body_html = f"""<html><body>
<p>Nya annonser för bevakningen <strong>{heading}</strong>:</p>
<ul>{''.join(html_rows)}</ul>
</body></html>"""
    body_text = (
        f"Nya annonser för bevakningen {clean_name}:\n\n"
        + "\n\n".join(text_rows)
    )

    msg = EmailMessage()
    msg["Subject"] = f"Nya annonser: {clean_name} ({len(listings)})"
    msg["From"] = config.EMAIL_FROM
    msg["To"] = recipient
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if config.SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - e-mail must not break monitoring
        print(f"[notifier] E-post misslyckades: {exc}")
        return False


def send_price_drop_notification(
    search_name: str, alert: dict, user_id: int = 0
) -> bool:
    """Notify about a price drop on a followed listing via e-mail."""
    if not config.EMAIL_ENABLED:
        return False
    profile = _get_profile(user_id=user_id)
    recipient = profile.get("email", "")
    if not recipient:
        return False

    clean_name = _header_text(search_name) or "Blocketbevakning"
    title = html.escape(alert.get("title") or "(utan titel)")
    url = html.escape(alert.get("url") or "", quote=True)
    old_p = _price_text(alert.get("old_price"))
    new_p = _price_text(alert.get("new_price"))

    body_html = f"""<html><body>
<p>Priset har sänkts på en annons du följer i bevakningen <strong>{html.escape(clean_name)}</strong>:</p>
<p><a href="{url}"><strong>{title}</strong></a><br>
Från {html.escape(old_p)} → <strong style="color:#1e8e3e">{html.escape(new_p)}</strong></p>
</body></html>"""
    body_text = (
        f"Priset har sänkts: {alert.get('title')} "
        f"– från {_price_text(alert.get('old_price'))} till "
        f"{_price_text(alert.get('new_price'))}.\n{alert.get('url')}"
    )

    msg = EmailMessage()
    msg["Subject"] = f"Prissänkning: {clean_name}"
    msg["From"] = config.EMAIL_FROM
    msg["To"] = recipient
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if config.SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"[notifier] Prissänkningsmail misslyckades: {exc}")
        return False


def send_sms_for_listings(
    search_name: str, listings: list[dict], phone: str
) -> bool:
    """Send a single batched SMS for new listings (placeholder – no provider configured)."""
    if not config.SMS_ENABLED or not config.SMS_API_URL or not phone:
        return False
    # TODO: implement when SMS provider is chosen.
    items = []
    for listing in listings:
        title_text = listing.get("title") or "(utan titel)"
        price = _price_text(listing.get("price"))
        url_text = listing.get("url") or ""
        items.append(f"{title_text} – {price}\n{url_text}")
    body = f"Nya annonser: {search_name}\n\n" + "\n\n".join(items)
    print(f"[notifier] SMS skulle skickats till {phone}: {len(listings)} annonser")
    return True


def _get_profile(user_id: int = 0) -> dict:
    """Local import to avoid circular dependency."""
    import db
    return db.get_profile(user_id=user_id)

def send_reset_email(email: str, token: str) -> bool:
    if not config.EMAIL_ENABLED or not email:
        return False
    import html as _html
    base_url = config.APP_URL or f"http://127.0.0.1:{config.PORT}"
    reset_url = f"{base_url}/?reset={token}&email={_html.escape(email, quote=True)}"
    msg = EmailMessage()
    msg["Subject"] = "Aterstall ditt losenord"
    msg["From"] = config.EMAIL_FROM
    msg["To"] = email
    lines = [
        "Hej!", "",
        "Nagon har begart att aterstalla losenordet for Blocketvakten.",
        "",
        "Klicka pa denna lank:",
        reset_url, "",
        "Lanken ar giltig i 1 timme.", "",
        "Om du inte begart detta kan du ignorera mailet.",
    ]
    body = "\n".join(lines)
    esc = _html.escape(reset_url, quote=True)
    body_html = (
        "<html><body>\n"
        "<p>Hej!</p>\n"
        "<p>Losenordsaterstallning.</p>\n"
        "<p><a href=\"" + esc + "\" style=\"display:inline-block;padding:11px 20px;background:#d32f2f;color:#fff;border-radius:8px;text-decoration:none;font-weight:700\">Aterstall losenord</a></p>\n"
        "<p style=\"color:#888\">Lanken ar giltig i 1 timme.</p>\n"
        "</body></html>"
    )
    msg.set_content(body)
    msg.add_alternative(body_html, subtype="html")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if config.SMTP_USE_TLS: server.starttls(); server.ehlo()
            if config.SMTP_USER: server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as exc:
        print("[notifier] Aterstallningsmail misslyckades:", exc)
        return False
