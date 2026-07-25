"""Jednorazowy skrypt migracji z lokalnych plików SQLite do wspólnej bazy Turso.

Migruje:
  - auth_users.db  -> tabela `users` w Turso (konta i hashe haseł bez zmian)
  - user_databases/<hash>/farmenager.db (per użytkownik) -> wspólne tabele
    biznesowe w Turso, z dopisaną kolumną `owner_username`.

WAŻNE - kolejność działań:
  1. Uzupełnij sekcję [turso] w .streamlit/secrets.toml (url, auth_token).
  2. NIE zmieniaj jeszcze auth.cookie.key - ten skrypt musi znać ten sam klucz,
     którym wcześniej wygenerowano foldery user_databases/<hash>/ (HMAC).
  3. Uruchom raz `streamlit run app.py` i zaloguj się dowolnym kontem - to
     wywoła init_db() i utworzy puste tabele (z kolumną owner_username) w Turso.
  4. Zatrzymaj aplikację i uruchom ten skrypt: `python migrate_to_turso.py`.
  5. Dopiero teraz możesz bezpiecznie wygenerować nowy, losowy auth.cookie.key
     (stare foldery user_databases/ nie są już potrzebne).

Skrypt nie usuwa żadnych plików źródłowych - jedynie czyta z nich i zapisuje
do Turso, więc można go bezpiecznie uruchomić ponownie (używa INSERT OR IGNORE
po kluczu głównym `id`, ale ponieważ id jest AUTOINCREMENT i nie jest znane
z góry, uruchamiaj go tylko raz na czystej bazie Turso, aby uniknąć duplikatów).
"""

import hashlib
import hmac
import os
import sqlite3

import libsql
import toml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")
AUTH_STORE_PATH = os.path.join(BASE_DIR, "auth_users.db")
USER_DATABASE_DIR = os.path.join(BASE_DIR, "user_databases")
LOCAL_REPLICA_PATH = os.path.join(BASE_DIR, "farmenager_replica.db")

OWNED_TABLES = [
    "fields",
    "farms",
    "seasons",
    "plots",
    "treatments",
    "costs",
    "crops",
    "field_crop_assignments",
    "ŚOR",
    "Nawozy",
    "Materiał siewny",
    "Maszyny",
]


def load_secrets() -> dict:
    with open(SECRETS_PATH, "r", encoding="utf-8") as fh:
        return toml.load(fh)


def user_db_digest(username: str, cookie_key: str) -> str:
    normalized = username.strip().lower()
    if not cookie_key:
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return hmac.new(cookie_key.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def connect_turso(url: str, auth_token: str):
    conn = libsql.connect(LOCAL_REPLICA_PATH, sync_url=url, auth_token=auth_token, sync_interval=30)
    conn.sync()
    return conn


def migrate_auth_users(turso_conn) -> list[str]:
    if not os.path.exists(AUTH_STORE_PATH):
        print("Brak auth_users.db - pomijam migrację kont.")
        return []

    with sqlite3.connect(AUTH_STORE_PATH) as local_conn:
        rows = local_conn.execute(
            "SELECT username, name, password, is_admin, created_at FROM users"
        ).fetchall()

    usernames = []
    for username, name, password, is_admin, created_at in rows:
        turso_conn.execute(
            "INSERT OR REPLACE INTO users (username, name, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, name, password, is_admin, created_at),
        )
        usernames.append(username)
    turso_conn.commit()
    print(f"Skopiowano {len(rows)} kont do Turso.")
    return usernames


def migrate_user_data(turso_conn, username: str, cookie_key: str) -> None:
    digest = user_db_digest(username, cookie_key)
    user_db_path = os.path.join(USER_DATABASE_DIR, digest, "farmenager.db")
    if not os.path.exists(user_db_path):
        print(f"  (brak lokalnej bazy dla {username}, pomijam)")
        return

    with sqlite3.connect(user_db_path) as local_conn:
        local_conn.row_factory = sqlite3.Row
        for table_name in OWNED_TABLES:
            try:
                cursor = local_conn.execute(f'SELECT * FROM "{table_name}"')
            except sqlite3.OperationalError:
                continue

            rows = cursor.fetchall()
            if not rows:
                continue

            columns = [description[0] for description in cursor.description if description[0] != "id"]
            placeholders = ", ".join(["?"] * (len(columns) + 1))
            column_list = ", ".join(["owner_username", *columns])
            for row in rows:
                values = [username] + [row[col] for col in columns]
                turso_conn.execute(
                    f'INSERT INTO "{table_name}" ({column_list}) VALUES ({placeholders})',
                    values,
                )
            print(f"  {table_name}: {len(rows)} wierszy -> {username}")

    turso_conn.commit()


def main() -> None:
    secrets = load_secrets()
    turso_secrets = secrets.get("turso", {})
    url = str(turso_secrets.get("url") or "").strip()
    auth_token = str(turso_secrets.get("auth_token") or "").strip()
    if not url or not auth_token:
        raise SystemExit("Brak sekcji [turso] (url, auth_token) w .streamlit/secrets.toml.")

    cookie_key = str(secrets.get("auth", {}).get("cookie", {}).get("key") or "")

    turso_conn = connect_turso(url, auth_token)

    print("Migruję konta użytkowników...")
    usernames = migrate_auth_users(turso_conn)

    print("Migruję dane biznesowe użytkowników...")
    for username in usernames:
        print(f"Użytkownik: {username}")
        migrate_user_data(turso_conn, username, cookie_key)

    turso_conn.sync()
    print("Gotowe. Zweryfikuj dane w aplikacji przed usunięciem lokalnych plików.")


if __name__ == "__main__":
    main()
