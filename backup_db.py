#!/usr/bin/env python3
"""Cotygodniowy backup bazy Turso wysyłany mailem jako załącznik ZIP.

Wymagane zmienne środowiskowe (ustawiaj jako GitHub Actions Secrets):
    TURSO_URL        – adres libsql://... bazy Turso
    TURSO_AUTH_TOKEN – token autoryzacji Turso
    RESEND_API_KEY   – klucz API z resend.com (darmowe konto)

Opcjonalne:
    RECIPIENT_EMAIL  – odbiorca (domyślnie marek.o.1999@gmail.com)
    FROM_EMAIL       – nadawca (domyślnie onboarding@resend.dev)
"""

import datetime
import io
import os
import zipfile

TURSO_URL = os.environ["TURSO_URL"]
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "marek.o.1999@gmail.com")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")

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
    """Wysyła ZIP jako załącznik przez Resend API."""
    import resend  # type: ignore

    resend.api_key = RESEND_API_KEY
    today = datetime.date.today().isoformat()

    params: resend.Emails.SendParams = {
        "from": f"Farmenager Backup <{FROM_EMAIL}>",
        "to": [RECIPIENT_EMAIL],
        "subject": f"Farmenager – backup bazy danych {today}",
        "text": (
            f"Automatyczny cotygodniowy backup bazy danych Farmenager z dnia {today}.\n\n"
            "Plik ZIP zawiera bazę SQLite ze wszystkimi danymi aplikacji.\n"
            "Możesz otworzyć plik .db programem DB Browser for SQLite "
            "(https://sqlitebrowser.org/)."
        ),
        "attachments": [
            {
                "filename": f"farmenager_backup_{today}.zip",
                "content": list(zip_bytes),
            }
        ],
    }

    response = resend.Emails.send(params)
    print(f"✓ Backup wysłany na {RECIPIENT_EMAIL} ({len(zip_bytes):,} bajtów) | id={response['id']}")


if __name__ == "__main__":
    print(f"[{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z] Start backupu...")
    zip_bytes = pull_and_zip()
    print(f"Rozmiar ZIP: {len(zip_bytes):,} bajtów")
    send_email(zip_bytes)
    print("Gotowe.")
