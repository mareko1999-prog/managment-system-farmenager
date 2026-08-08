from collections.abc import Mapping
import datetime as dt
import os
import re
import time
from pathlib import Path
from typing import Any

import bcrypt
import streamlit as st
import streamlit_authenticator as stauth
import toml

import db


SESSION_AUTH_STATUS = "authentication_status"
SESSION_AUTH_NAME = "name"
SESSION_AUTH_USERNAME = "username"
SESSION_AUTH_LAST_ACTIVITY_KEY = "auth_last_activity"
FAILED_LOGIN_ATTEMPTS_KEY = "auth_failed_login_attempts"
FAILED_LOGIN_LOCK_UNTIL_KEY = "auth_failed_login_lock_until"
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 300
DEFAULT_INACTIVITY_MINUTES = 60.0
INACTIVITY_TIMEOUT_MINUTES = 10000.0


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    return value


def _load_secrets_from_file() -> dict[str, Any]:
    secrets_path = _get_secrets_file_path()
    if not secrets_path.exists():
        return {}

    try:
        with secrets_path.open("r", encoding="utf-8") as handle:
            parsed = toml.load(handle)
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}
    return parsed


def _get_auth_config() -> dict[str, Any]:
    secrets = getattr(st, "secrets", None)
    if isinstance(secrets, Mapping) and "auth" in secrets:
        config = _to_plain_data(secrets["auth"])
    else:
        file_config = _load_secrets_from_file()
        if isinstance(file_config, dict) and "auth" in file_config:
            config = _to_plain_data(file_config["auth"])
        else:
            st.error("Brak konfiguracji logowania. Ustaw sekcję [auth] w secrets.toml.")
            st.stop()

    if not isinstance(config, dict):
        st.error("Sekcja [auth] w secrets.toml ma nieprawidłowy format.")
        st.stop()

    cookie = config.get("cookie")
    if not isinstance(cookie, dict):
        st.error("Brak sekcji [auth.cookie] w secrets.toml.")
        st.stop()
    if not cookie.get("key"):
        st.error("Brak auth.cookie.key w secrets.toml.")
        st.stop()

    credentials = config.get("credentials")
    if credentials is not None and not isinstance(credentials, dict):
        st.error("Sekcja [auth.credentials] w secrets.toml ma nieprawidłowy format.")
        st.stop()

    return config


def _get_cookie_expiry_days(config: dict[str, Any] | None = None) -> float:
    return INACTIVITY_TIMEOUT_MINUTES / 1440.0


def _ensure_auth_store() -> None:
    with db.get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _load_auth_users_from_store() -> dict[str, dict[str, Any]]:
    _ensure_auth_store()
    with db.get_connection() as conn:
        rows = conn.execute("SELECT username, name, password, is_admin FROM users ORDER BY username").fetchall()
    users: dict[str, dict[str, Any]] = {}
    for row in rows:
        users[str(row[0]).lower()] = {
            "name": str(row[1]),
            "password": str(row[2]),
            "is_admin": bool(int(row[3] or 0)),
        }
    return users


def _seed_auth_store_from_secrets(config: dict[str, Any]) -> None:
    usernames = config.get("credentials", {}).get("usernames", {})
    if not isinstance(usernames, dict) or not usernames:
        return

    _ensure_auth_store()
    with db.get_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        if existing and int(existing[0]) > 0:
            return

        now = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for username, user_data in usernames.items():
            if not isinstance(user_data, dict):
                continue
            normalized_username = _normalize_username(str(username))
            conn.execute(
                "INSERT OR REPLACE INTO users (username, name, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    normalized_username,
                    str(user_data.get("name") or normalized_username),
                    str(user_data.get("password") or ""),
                    1 if is_admin_username(normalized_username) else 0,
                    now,
                ),
            )
        conn.commit()


def _build_authenticator_credentials() -> dict[str, Any]:
    config = _get_auth_config()
    _seed_auth_store_from_secrets(config)
    users = _load_auth_users_from_store()
    return {
        "usernames": {
            username: {
                "name": user_data["name"],
                "password": user_data["password"],
            }
            for username, user_data in users.items()
        }
    }


def _normalize_username(value: str) -> str:
    return str(value).strip().lower()


def _is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", str(value).strip()))


def _get_registration_access_key(config: dict[str, Any]) -> str:
    registration = config.get("registration")
    if not isinstance(registration, dict):
        st.error("Brak sekcji [auth.registration] w secrets.toml.")
        st.stop()

    access_key = str(registration.get("access_key") or "").strip()
    if not access_key:
        st.error("Brak auth.registration.access_key w secrets.toml.")
        st.stop()

    return access_key


def _get_usernames(config: dict[str, Any]) -> dict[str, Any]:
    credentials = config.setdefault("credentials", {})
    usernames = credentials.setdefault("usernames", {})
    if not isinstance(usernames, dict):
        raise TypeError("Sekcja [auth.credentials.usernames] musi być słownikiem.")
    return usernames


def is_admin_username(username: str | None) -> bool:
    return _normalize_username(str(username or "")) == "admin"


def list_registered_users() -> list[dict[str, str]]:
    _ensure_auth_store()
    with db.get_connection() as conn:
        rows = conn.execute("SELECT username, name FROM users ORDER BY username").fetchall()
    return [{"username": str(row[0]), "name": str(row[1])} for row in rows]


def delete_registered_user(username: str) -> bool:
    normalized_username = _normalize_username(username)
    if not normalized_username:
        return False
    if is_admin_username(normalized_username):
        st.error("Nie można usunąć konta administratora.")
        return False

    try:
        _ensure_auth_store()
        with db.get_connection() as conn:
            row = conn.execute("SELECT username FROM users WHERE username = ?", (normalized_username,)).fetchone()
            if not row:
                st.warning("Użytkownik nie istnieje.")
                return False
            conn.execute("DELETE FROM users WHERE username = ?", (normalized_username,))
            conn.commit()
        return True
    except Exception as exc:
        st.error(f"Nie udało się usunąć użytkownika: {exc}")
        return False


def set_registered_user_password(username: str, new_password: str) -> bool:
    normalized_username = _normalize_username(username)
    if not normalized_username:
        return False

    if len(str(new_password or "").strip()) < 6:
        st.error("Hasło musi mieć co najmniej 6 znaków.")
        return False

    try:
        _ensure_auth_store()
        with db.get_connection() as conn:
            row = conn.execute("SELECT username FROM users WHERE username = ?", (normalized_username,)).fetchone()
            if not row:
                st.warning("Użytkownik nie istnieje.")
                return False

            conn.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (_hash_password(str(new_password).strip()), normalized_username),
            )
            conn.commit()
        return True
    except Exception as exc:
        st.error(f"Nie udało się zresetować hasła: {exc}")
        return False


def _register_user(email: str, full_name: str, password: str) -> bool:
    try:
        normalized_email = _normalize_username(email)

        _ensure_auth_store()
        with db.get_connection() as conn:
            row = conn.execute("SELECT username FROM users WHERE username = ?", (normalized_email,)).fetchone()
            if row:
                st.error("Konto o tym adresie email już istnieje.")
                return False

            conn.execute(
                "INSERT INTO users (username, name, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    normalized_email,
                    full_name,
                    _hash_password(password),
                    0,
                    dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                ),
            )
            conn.commit()
        return True
    except Exception as exc:
        st.error(f"Nie udało się utworzyć konta: {exc}")
        return False


def show_registration_form() -> None:
    config = _get_auth_config()
    expected_access_key = _get_registration_access_key(config)

    with st.sidebar:
        st.divider()
        with st.expander("Załóż konto"):
            with st.form(key="registration_form"):
                first_name = st.text_input("Imię", key="registration_first_name")
                last_name = st.text_input("Nazwisko", key="registration_last_name")
                email = st.text_input("Adres email", key="registration_email")
                password = st.text_input("Hasło", type="password", key="registration_password")
                confirm_password = st.text_input("Potwierdź hasło", type="password", key="registration_password_confirm")
                access_key = st.text_input("Klucz dostępu", type="password", key="registration_access_key")

                submitted = st.form_submit_button("Utwórz konto")
                if not submitted:
                    return

                first_name = first_name.strip()
                last_name = last_name.strip()
                email = _normalize_username(email)
                password = password.strip()
                confirm_password = confirm_password.strip()
                access_key = access_key.strip()

                if not first_name or not last_name or not email or not password or not confirm_password or not access_key:
                    st.error("Wszystkie pola są wymagane.")
                    return

                if access_key != expected_access_key:
                    st.error("Nieprawidłowy klucz dostępu.")
                    return

                if not _is_valid_email(email):
                    st.error("Podaj poprawny adres email.")
                    return

                if password != confirm_password:
                    st.error("Hasła nie są identyczne.")
                    return

                if len(password) < 6:
                    st.error("Hasło musi mieć co najmniej 6 znaków.")
                    return

                full_name = f"{first_name} {last_name}".strip()
                if _register_user(email, full_name, password):
                    st.success("Konto zostało utworzone. Teraz możesz się zalogować adresem email i hasłem.")
                    st.rerun()


def require_authentication() -> None:
    config = _get_auth_config()
    st.session_state["logout"] = False

    lock_until = float(st.session_state.get(FAILED_LOGIN_LOCK_UNTIL_KEY) or 0.0)
    now_ts = dt.datetime.utcnow().timestamp()
    if lock_until > now_ts:
        remaining_seconds = int(lock_until - now_ts)
        st.error(f"Zbyt wiele nieudanych prób logowania. Spróbuj ponownie za {remaining_seconds} s.")
        st.stop()

    credentials = _build_authenticator_credentials()
    cookie = config["cookie"]
    preauthorized = config.get("preauthorized")

    was_authenticated = st.session_state.get(SESSION_AUTH_STATUS) is True

    # TYMCZASOWY DEBUG - do usunięcia po diagnozie problemu z ciasteczkiem.
    if os.environ.get("AUTH_COOKIE_DEBUG") or True:
        try:
            st.write("DEBUG st.context.cookies:", dict(st.context.cookies))
        except Exception as debug_exc:  # noqa: BLE001
            st.write("DEBUG st.context.cookies error:", repr(debug_exc))

    authenticator = stauth.Authenticate(
        credentials,
        str(cookie.get("name", "farmenager_auth")),
        str(cookie.get("key")),
        _get_cookie_expiry_days(config),
        preauthorized,
    )

    login_result: Any = None
    try:
        login_result = authenticator.login(location="main", key="login_form")
    except TypeError:
        login_result = authenticator.login("Logowanie", "main")

    auth_status = st.session_state.get(SESSION_AUTH_STATUS)
    name = st.session_state.get(SESSION_AUTH_NAME)
    username = st.session_state.get(SESSION_AUTH_USERNAME)

    if isinstance(login_result, tuple) and len(login_result) >= 2:
        tuple_name = login_result[0] if len(login_result) >= 1 else None
        tuple_auth_status = login_result[1]
        tuple_username = login_result[2] if len(login_result) >= 3 else None
        if tuple_name:
            name = tuple_name
            st.session_state[SESSION_AUTH_NAME] = tuple_name
        auth_status = tuple_auth_status
        if tuple_username:
            username = _normalize_username(str(tuple_username))
            st.session_state[SESSION_AUTH_USERNAME] = username

    if auth_status is False:
        st.error("Nieprawidłowy login lub hasło.")
        failed_attempts = int(st.session_state.get(FAILED_LOGIN_ATTEMPTS_KEY) or 0) + 1
        st.session_state[FAILED_LOGIN_ATTEMPTS_KEY] = failed_attempts
        if failed_attempts >= MAX_LOGIN_ATTEMPTS:
            st.session_state[FAILED_LOGIN_LOCK_UNTIL_KEY] = dt.datetime.utcnow().timestamp() + LOGIN_LOCK_SECONDS
            st.session_state[FAILED_LOGIN_ATTEMPTS_KEY] = 0
        show_registration_form()
        st.stop()

    if auth_status is None:
        st.warning("Zaloguj się, aby korzystać z aplikacji.")
        show_registration_form()
        st.stop()

    if auth_status is True and not was_authenticated:
        # Ciasteczko re-autentykacji jest zapisywane w przeglądarce asynchronicznie
        # przez ukryty komponent JS (CookieManager). To zajmuje chwilę – jeśli
        # użytkownik odświeży stronę (F5) natychmiast po zalogowaniu, ciasteczko
        # może jeszcze nie istnieć w przeglądarce i sesja zostanie utracona.
        # Krótka pauza (bez rerun, żeby nie przerywać zapisu ciasteczka w trakcie)
        # daje komponentowi czas na dokończenie zapisu.
        time.sleep(1.5)

    display_name = str(name or username or "Użytkownik")
    st.sidebar.success(f"Zalogowano: {display_name}")
    st.session_state["logout"] = False
    st.session_state[FAILED_LOGIN_ATTEMPTS_KEY] = 0
    st.session_state[FAILED_LOGIN_LOCK_UNTIL_KEY] = 0.0

    try:
        authenticator.logout("Wyloguj", location="sidebar", key="logout_button")
    except TypeError:
        authenticator.logout("Wyloguj", "sidebar")


def _get_secrets_file_path() -> Path:
    """Zwraca ścieżkę do pliku secrets.toml."""
    # Najpierw sprawdź lokalny plik w projekcie
    local_secrets = Path(".streamlit/secrets.toml")
    if local_secrets.exists():
        return local_secrets
    
    # Fallback do globalnego pliku w katalogu użytkownika
    import os
    user_home = Path(os.path.expanduser("~"))
    return user_home / ".streamlit" / "secrets.toml"


def _hash_password(password: str) -> str:
    """Generuje hash bcrypt dla podanego hasła."""
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode()


def _verify_password(password: str, hashed: str) -> bool:
    """Weryfikuje czy hasło pasuje do hashu."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def show_password_change_form() -> None:
    """Wyświetla formularz zmiany hasła w sidebarze."""
    username = _normalize_username(str(st.session_state.get(SESSION_AUTH_USERNAME) or ""))
    
    if not username:
        return
    
    with st.sidebar:
        st.divider()
        with st.expander("🔐 Zmień hasło"):
            with st.form(key="password_change_form"):
                current_password = st.text_input(
                    "Obecne hasło",
                    type="password",
                    key="current_password_input"
                )
                new_password = st.text_input(
                    "Nowe hasło",
                    type="password",
                    key="new_password_input"
                )
                confirm_password = st.text_input(
                    "Potwierdź nowe hasło",
                    type="password",
                    key="confirm_password_input"
                )
                
                submit_button = st.form_submit_button("Zmień hasło")
                
                if submit_button:
                    # Walidacja
                    if not current_password or not new_password or not confirm_password:
                        st.error("Wszystkie pola są wymagane")
                        return
                    
                    if new_password != confirm_password:
                        st.error("Nowe hasła nie są identyczne")
                        return
                    
                    if len(new_password) < 6:
                        st.error("Nowe hasło musi mieć co najmniej 6 znaków")
                        return
                    
                    _ensure_auth_store()
                    with db.get_connection() as conn:
                        row = conn.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
                        current_hash = str(row[0]) if row else ""
                    
                    if not _verify_password(current_password, current_hash):
                        st.error("Nieprawidłowe obecne hasło")
                        return
                    
                    # Zapisz nowe hasło
                    if set_registered_user_password(username, new_password):
                        st.success("Hasło zostało zmienione! Zmiany będą aktywne po ponownym zalogowaniu.")
                        st.info("💡 Zaloguj się ponownie, aby nowe hasło zaczęło działać.")
                    else:
                        st.error("Nie udało się zmienić hasła")
