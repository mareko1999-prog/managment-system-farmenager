#!/usr/bin/env python3
"""Cotygodniowy backup bazy Turso wysyłany mailem jako załącznik ZIP.

Wymagane zmienne środowiskowe (ustawiaj jako GitHub Actions Secrets):
    TURSO_URL          – adres libsql://... bazy Turso
    TURSO_AUTH_TOKEN   – token autoryzacji Turso
    SMTP_USER          – adres nadawcy Gmail (np. twoj@gmail.com)
    SMTP_PASSWORD      – hasło aplikacji Gmail (App Password, nie zwykłe hasło)

Opcjonalne:
    RECIPIENT_EMAIL    – odbiorca (domyślnie marek.o.1999@gmail.com)
    SMTP_HOST          – domyślnie smtp.gmail.com
    SMTP_PORT          – domyślnie 587
"""

import datetime
import io
import os
import smtplib
import zipfile
from email.message import EmailMessage

TURSO_URL = os.environ["TURSO_URL"]
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "marek.o.1999@gmail.com")

REPLICA_PATH = "/tmp/farmenager_backup_replica.db"


def pull_and_zip() -> bytes:
    """Synchronizuje lokalną replikę z Turso i pakuje plik do ZIP-a."""
    import libsql  # type: ignore

    conn = libsql.connect(REPLICA_PATH, sync_url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)
    try:
        conn.sync()
    finally:
        conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(REPLICA_PATH, "farmenager_backup.db")
    return buf.getvalue()


def send_email(zip_bytes: bytes) -> None:
    """Wysyła ZIP jako załącznik na RECIPIENT_EMAIL przez SMTP."""
    today = datetime.date.today().isoformat()

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = f"Farmenager – backup bazy danych {today}"
    msg.set_content(
        f"Automatyczny cotygodniowy backup bazy danych Farmenager z dnia {today}.\n\n"
        "Plik ZIP zawiera bazę SQLite ze wszystkimi danymi aplikacji.\n"
        "Możesz otworzyć plik .db programem DB Browser for SQLite (https://sqlitebrowser.org/)."
    )
    msg.add_attachment(
        zip_bytes,
        maintype="application",
        subtype="zip",
        filename=f"farmenager_backup_{today}.zip",
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)

    print(f"✓ Backup wysłany na {RECIPIENT_EMAIL} ({len(zip_bytes):,} bajtów)")


if __name__ == "__main__":
    print(f"[{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z] Start backupu...")
    zip_bytes = pull_and_zip()
    print(f"Rozmiar ZIP: {len(zip_bytes):,} bajtów")
    send_email(zip_bytes)
    print("Gotowe.")
